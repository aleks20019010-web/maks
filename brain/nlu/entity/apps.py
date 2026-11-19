# brain/nlu/entity/apps.py
# Извлечение сущности программы из фразы по каталогу config/apps.json.

import difflib
import json
import re
from pathlib import Path

from brain.nlu.types import Entity, EntityCandidate, Intent
from shared import config as cfg
from shared.log import log


_APPS_FILE = Path(cfg.CONFIG_DIR) / "apps.json"

_OPEN_VERBS = {
    "открой", "открыть", "запусти", "запустить",
    "включи", "включить", "вруби", "врубить", "подними", "стартуй",
}
_CLOSE_VERBS = {
    "закрой", "закрыть", "выключи", "выключить",
    "заверши", "завершить", "убей", "убить", "сверни", "свернуть", "останови",
}
_FILLER_WORDS = {"программу", "приложение", "окно", "пожалуйста", "мне", "быстро"}


def load_apps_catalog() -> dict:
    if not _APPS_FILE.exists():
        log(f"entity.apps: {_APPS_FILE} не найден", tag="BRAIN", level="WARN")
        return {}
    try:
        with open(_APPS_FILE, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        log(f"entity.apps: ошибка чтения {_APPS_FILE}: {e}", tag="BRAIN", level="WARN")
        return {}


class AppExtractor:
    supported_intents: tuple[Intent, ...] = (Intent.APP_OPEN, Intent.APP_CLOSE)

    def supports(self, intent: Intent) -> bool:
        return intent in self.supported_intents

    def extract_candidate(self, text: str, intent: Intent) -> str | None:
        """Вырезать глаголы действия и служебные слова, оставив название программы."""
        if not text:
            return None
        clean = re.sub(r"[^\w\s\-]", " ", text.lower())
        words = [w for w in clean.split() if w]

        # Убираем стартовый глагол («открой», «закрой» и т.д.)
        while words and (words[0] in _OPEN_VERBS or words[0] in _CLOSE_VERBS or words[0] in _FILLER_WORDS):
            words.pop(0)

        # Убираем хвостовые слова-пустышки
        while words and words[-1] in _FILLER_WORDS:
            words.pop()

        candidate = " ".join(words).strip()
        return candidate or None

    def match(self, candidate: str, intent: Intent) -> Entity:
        if not candidate:
            return Entity.absent()

        cand = candidate.lower().strip()
        catalog = load_apps_catalog()
        if not catalog:
            return Entity.unresolved(candidate)

        # Нормализуем простые русские падежи («хроме» → «хром», «хрома» → «хром»)
        cand_variants = {cand}
        if len(cand) > 3 and cand[-1] in ("а", "у", "е", "ом", "ой"):
            cand_variants.add(cand[:-1])
        if len(cand) > 4 and cand.endswith(("ом", "ой", "ем", "ей")):
            cand_variants.add(cand[:-2])

        # 1. Точное совпадение с алиасом или ключом
        for app_key, info in catalog.items():
            if not isinstance(info, dict):
                continue
            aliases = [app_key.lower()] + [
                a.lower().strip() for a in info.get("aliases", []) if isinstance(a, str)
            ]
            for alias in aliases:
                if alias in cand_variants:
                    return Entity.found("app", app_key, 1.0, "exact")

        # 2. Вхождение длинного алиаса или fuzzy-совпадение
        scored: list[EntityCandidate] = []
        for app_key, info in catalog.items():
            if not isinstance(info, dict):
                continue
            aliases = [app_key.lower()] + [
                a.lower().strip() for a in info.get("aliases", []) if isinstance(a, str)
            ]
            best_for_app = 0.0
            for alias in aliases:
                if not alias:
                    continue
                if len(alias) >= 4 and (alias in cand or cand in alias):
                    best_for_app = max(best_for_app, 0.90)
                else:
                    ratio = difflib.SequenceMatcher(None, cand, alias).ratio()
                    best_for_app = max(best_for_app, ratio)
            if best_for_app >= 0.78:
                scored.append(EntityCandidate("app", app_key, best_for_app, "fuzzy"))

        if len(scored) == 1:
            c = scored[0]
            return Entity.found("app", c.value, c.match_confidence, c.source)
        if len(scored) > 1:
            scored.sort(key=lambda x: x.match_confidence, reverse=True)
            if scored[0].match_confidence - scored[1].match_confidence >= 0.12:
                c = scored[0]
                return Entity.found("app", c.value, c.match_confidence, c.source)
            return Entity.ambiguous(scored[:3])

        return Entity.unresolved(candidate)


_extractor: AppExtractor | None = None


def get() -> AppExtractor:
    global _extractor
    if _extractor is None:
        _extractor = AppExtractor()
    return _extractor