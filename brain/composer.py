# brain/composer.py
# Composer: сборка ответа из слотов (opening + core + closing).
#
# Не заменяет PhrasePicker — использует его для выбора фраз по слотам.
# Правила слотов — в responses/composer.json.
#
# Новый формат composer.json:
#   "PLAY_PLAYLIST": {"opening": "OPENING_MUSIC", "core": "PLAY_PLAYLIST", "closing": "CLOSING_MUSIC"}
# Старый формат (для совместимости):
#   "PLAY_PLAYLIST": ["opening", "core", "closing"]
#
# Между частями добавляется sil<[100]> (пауза), если часть не заканчивается
# на точку/паузу. Чтобы TTS не склеивал «волну Поехали» в одно слово.
#
# FIX: добавлена подстановка {answer} (и любых {key} из subs) в фразы.
# Нужно для llm_answer: LLM_CORE = "{answer}" → подставляется текст LLM.
#
# Если phrase_set нет в composer.json — возвращает None (fallback на ResponseBuilder).

import os
import json

from shared.log import log


_COMPOSER_FILE = "composer.json"

# Пауза между частями (если предыдущая не заканчивается на точку/паузу)
_PART_PAUSE = "sil<[100]>"

# Символы, после которых пауза не нужна (TTS сам сделает)
_END_MARKS = (".", "!", "?", "…", "sil<[")


def _ends_with_pause(text: str) -> bool:
    t = (text or "").rstrip()
    if not t:
        return False
    if t.endswith(_END_MARKS):
        return True
    if t.endswith(">"):
        idx = t.rfind("sil<[")
        if idx != -1 and t.endswith(">"):
            return True
    return False


def _substitute(text: str, subs: dict) -> str:
    """Подставить {key} из subs в text. Неизвестные ключи — оставить как есть."""
    if not text or "{" not in text:
        return text
    for key, value in subs.items():
        if value is None:
            continue
        text = text.replace("{" + key + "}", str(value))
    return text


class Composer:
    def __init__(self, base_dir: str, phrase_picker):
        self.base_dir = base_dir
        self.phrase_picker = phrase_picker
        self.responses_dir = os.path.join(base_dir, "responses")
        self._rules: dict = {}
        self._load()

    def _load(self):
        path = os.path.join(self.responses_dir, _COMPOSER_FILE)
        if not os.path.exists(path):
            log(f"composer: {_COMPOSER_FILE} не найден, composer отключён", tag="BRAIN", level="WARN")
            return
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._rules = data
                log(f"composer: загружено {len(self._rules)} правил", tag="BRAIN")
        except Exception as e:
            log(f"composer: ошибка чтения {_COMPOSER_FILE}: {e}", tag="BRAIN", level="WARN")

    def reload(self):
        self._rules = {}
        self._load()

    def _slots_for(self, phrase_set: str):
        rule = self._rules.get(phrase_set)
        if not rule:
            return None

        if isinstance(rule, dict):
            result = []
            for slot in ("opening", "core", "closing"):
                ps = rule.get(slot)
                if ps:
                    result.append((slot, ps))
            return result if result else None

        if isinstance(rule, list):
            result = []
            for slot in rule:
                if slot in ("opening", "core", "closing"):
                    result.append((slot, phrase_set))
            return result if result else None

        return None

    def compose(self, policy_result, ctx):
        """Собрать ответ из слотов.

        Возвращает JarvisResponse или None (если phrase_set нет в правилах).
        """
        if policy_result is None:
            return None
        if not policy_result.phrase_set:
            return None

        slots = self._slots_for(policy_result.phrase_set)
        if not slots:
            return None

        # Подстановки: payload из правила + из запроса + identity
        subs = {}
        if hasattr(ctx, "_payload"):
            subs.update(ctx._payload or {})
        if policy_result.payload:
            subs.update(policy_result.payload)
        try:
            subs.setdefault("identity_name", ctx.identity_name())
            subs.setdefault("identity_about", ctx.identity_about())
            subs.setdefault("identity_can", ctx.identity_can())
            subs.setdefault("identity_wont", ctx.identity_wont())
        except Exception:
            pass

        parts = []
        core_phrase = None
        for slot, ps in slots:
            phrase = self.phrase_picker.pick(
                ps,
                slot=slot,
                **subs,
            )
            if phrase is None:
                continue
            text = phrase.get("text", "")
            if not text:
                continue
            # FIX: подстановка {answer} и других {key} из subs
            text = _substitute(text, subs)
            parts.append(text)
            if slot == "core" and core_phrase is None:
                core_phrase = phrase

        if not parts:
            return None

        full_text = _join_parts(parts)
        if not full_text:
            return None

        src = core_phrase or {}
        from personality_core.types import JarvisResponse
        return JarvisResponse(
            text=full_text,
            rate=src.get("rate"),
            pitch=src.get("pitch"),
            emotion=src.get("emotion"),
            pause_before=float(src.get("pause_before", 0.0)),
            pause_after=float(src.get("pause_after", 0.0)),
            actions=policy_result.payload.get("actions", []),
            explain=f"composer {policy_result.rule_name} → {policy_result.phrase_set} [{','.join(s for s, _ in slots)}]",
        )


def _join_parts(parts: list) -> str:
    cleaned = []
    for p in parts:
        p = (p or "").strip()
        if not p:
            continue
        cleaned.append(p)

    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0]

    result = []
    for i, p in enumerate(cleaned):
        result.append(p)
        if i < len(cleaned) - 1:
            if not _ends_with_pause(p):
                result.append(_PART_PAUSE)

    return " ".join(result)