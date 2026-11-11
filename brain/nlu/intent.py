# brain/nlu/intent.py
# IntentResolver: SetFit → Intent + confidence.
#
# Приоритет:
#   1. Exact-match с фразой из config/intent_routes.json → (intent, 1.0).
#   2. SetFit (models/setfit_intents/) → (intent, confidence).
#   3. Пост-правила (VOLUME_UP/DOWN, CLEANUP_DISK/RECYCLE,
#      SMALLTALK/SYSTEM_STATUS, LLM_QUESTION/MUSIC_NOW_PLAYING) —
#      чинят известные слабые места tiny-энкодера.
#   4. Fuzzy fallback (difflib, порог 0.8) — если SetFit не уверен.
#
# UNKNOWN — если всё ниже порога.
# warmup() — прогреть SetFit в фоне при старте brain.
#
# Заменяет semantic_router (IndexedRouter + FastEmbedEncoder).
#
# ── MEM-OPT (2026-11-11): ужимаем PyTorch ──────────────────────
#   Модель уже маленькая (113 МБ на диске), но torch на CPU ест
#   600+ МБ на рантайм, пулы потоков и кэши аллокатора.
#
#   ВАЖНО: import torch и настройка потоков идут ДО from setfit.
#   set_num_interop_threads работает ТОЛЬКО до первой параллельной
#   работы torch. setfit при импорте её запускает — поэтому порядок
#   критичен.
#
#   Патчи:
#     1. env OMP/MKL/TORCH_NUM_THREADS=2 + TOKENIZERS_PARALLELISM=false
#        в самом начале файла — до любого импорта torch.
#     2. import torch → set_num_threads(2) → set_num_interop_threads(1)
#        → set_flush_denormal(True) — ДО from setfit.
#     3. model.model.eval() + torch.inference_mode() при predict.
#   Логика resolve/warmup/пост-правил НЕ изменена.

import os

# ── MEM-OPT (патч 1): env ДО любого импорта torch/setfit ──────
# Каждый поток = стек + буферы. По умолчанию torch создаёт пул
# по числу ядер (у 6 ГБ RAM обычно 4–8 ядер). 2 потока достаточно
# для инференса tiny-энкодера, а память падает на 100–200 МБ.
os.environ["OMP_NUM_THREADS"] = "2"
os.environ["MKL_NUM_THREADS"] = "2"
os.environ["TORCH_NUM_THREADS"] = "2"
os.environ["TOKENIZERS_PARALLELISM"] = "false"
os.environ["TRANSFORMERS_NO_ADVISORY_WARNINGS"] = "1"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"

import json
import threading
import difflib

from shared import config as cfg
from shared.log import log
from brain.nlu.types import Intent


_setfit_model = None
_lock = threading.Lock()
_ROUTES_FILE = cfg.CONFIG_DIR / "intent_routes.json"
_MODEL_DIR = cfg.DATA_DIR.parent / "models" / "setfit_intents"

# Порог уверенности SetFit: ниже — пробуем fuzzy, потом UNKNOWN.
# 0.5 — стартовое значение; на CV модель уверена (>0.8) на большинстве
# правильных предсказаний, а на ошибочных — часто ниже.
_THRESHOLD = 0.5
_FUZZY_FALLBACK_THRESHOLD = 0.80


# ── Загрузка routes ────────────────────────────────────────
def _load_routes() -> dict[Intent, list[str]]:
    if not _ROUTES_FILE.exists():
        log(f"intent: {_ROUTES_FILE} не найден — routes пустые",
            tag="BRAIN", level="ERROR")
        return {}
    try:
        with open(_ROUTES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        log(f"intent: ошибка чтения {_ROUTES_FILE}: {e} — routes пустые",
            tag="BRAIN", level="ERROR")
        return {}

    if not isinstance(data, dict):
        log(f"intent: {_ROUTES_FILE} — не dict — routes пустые",
            tag="BRAIN", level="ERROR")
        return {}

    routes: dict[Intent, list[str]] = {}
    for intent_name, phrases in data.items():
        try:
            intent = Intent(intent_name)
        except ValueError:
            log(f"intent: неизвестный intent в routes: {intent_name} — пропуск",
                tag="BRAIN", level="WARN")
            continue
        if not isinstance(phrases, list):
            continue
        cleaned = [p.strip() for p in phrases if isinstance(p, str) and p.strip()]
        if cleaned:
            routes[intent] = cleaned
    log(f"intent: routes загружены: {len(routes)} интентов, "
        f"{sum(len(v) for v in routes.values())} фраз", tag="BRAIN")
    return routes


_ROUTES: dict[Intent, list[str]] = _load_routes()


# ── Exact-match ────────────────────────────────────────────
def _exact_match(text: str) -> tuple[Intent, float] | None:
    if not text:
        return None
    text_norm = text.lower().strip()
    if not text_norm:
        return None
    for intent, phrases in _ROUTES.items():
        for phrase in phrases:
            if phrase.lower().strip() == text_norm:
                return intent, 1.0
    return None


# ── Fuzzy fallback ─────────────────────────────────────────
_fuzzy_index: list[tuple[str, Intent]] | None = None


def _build_fuzzy_index() -> list[tuple[str, Intent]]:
    index: list[tuple[str, Intent]] = []
    for intent, phrases in _ROUTES.items():
        for phrase in phrases:
            p = phrase.lower().strip()
            if p:
                index.append((p, intent))
    return index


def _fuzzy_fallback(text: str) -> tuple[Intent, float] | None:
    global _fuzzy_index
    if not text:
        return None
    text_norm = text.lower().strip()
    if not text_norm:
        return None
    if _fuzzy_index is None:
        _fuzzy_index = _build_fuzzy_index()
    if not _fuzzy_index:
        return None

    best_score = 0.0
    best_intent: Intent | None = None
    for phrase, intent in _fuzzy_index:
        score = difflib.SequenceMatcher(None, text_norm, phrase).ratio()
        if score > best_score:
            best_score = score
            best_intent = intent
    if best_score >= _FUZZY_FALLBACK_THRESHOLD and best_intent is not None:
        return best_intent, best_score
    return None


# ── Пост-правила (чинят известные слабости tiny-энкодера) ──
_VOLUME_DOWN_WORDS = ("уменьши", "убавь", "тише", "потише", "прикрути", "минимум")
_VOLUME_UP_WORDS = ("увеличь", "прибавь", "громче", "погромче", "максимум")
_RECYCLE_WORDS = ("корзин", "мусорк")
_DISK_WORDS = ("диск",)
_SMALLTALK_AS_SYSTEM = ("как дела", "как жизнь", "как настроение", "как ты")
_LLM_QUESTION_WORDS = ("что такое", "что значит", "объясни", "почему")


def _apply_post_rules(text: str, intent: Intent) -> Intent:
    """Чинит известные пары, где tiny-энкодер стабильно путается.

    Применяется ТОЛЬКО если SetFit предсказал один из конфликтующих
    классов — не перебивает всё подряд.
    """
    t = text.lower()

    # VOLUME_UP ↔ VOLUME_DOWN: антонимы, tiny не различает.
    if intent in (Intent.VOLUME_UP, Intent.VOLUME_DOWN):
        if any(w in t for w in _VOLUME_DOWN_WORDS):
            return Intent.VOLUME_DOWN
        if any(w in t for w in _VOLUME_UP_WORDS):
            return Intent.VOLUME_UP

    # CLEANUP_DISK ↔ CLEANUP_RECYCLE: «мусор» / «мусорка».
    if intent in (Intent.CLEANUP_DISK, Intent.CLEANUP_RECYCLE):
        if any(w in t for w in _RECYCLE_WORDS):
            return Intent.CLEANUP_RECYCLE
        if any(w in t for w in _DISK_WORDS):
            return Intent.CLEANUP_DISK

    # SMALLTALK → SYSTEM_STATUS: «как дела/жизнь/настроение».
    if intent == Intent.SYSTEM_STATUS:
        if any(w in t for w in _SMALLTALK_AS_SYSTEM):
            return Intent.SMALLTALK

    # LLM_QUESTION ↔ MUSIC_NOW_PLAYING: «что такое/что значит».
    if intent == Intent.MUSIC_NOW_PLAYING:
        if any(w in t for w in _LLM_QUESTION_WORDS):
            return Intent.LLM_QUESTION

    return intent


# ── SetFit ─────────────────────────────────────────────────
_torch_tuned = False


def _tune_torch():
    """MEM-OPT (патч 2): настроить пулы потоков torch.

    ВАЖНО: вызывать ДО from setfit. set_num_interop_threads работает
    только до первой параллельной работы torch, а setfit её запускает
    при импорте. Разбито на отдельные try, чтобы падение
    set_num_interop_threads не отменяло set_num_threads и
    set_flush_denormal.
    """
    global _torch_tuned
    if _torch_tuned:
        return
    _torch_tuned = True

    try:
        import torch
    except Exception as e:
        log(f"intent: torch import: {e}", tag="BRAIN", level="WARN")
        return

    try:
        torch.set_num_threads(2)
        log("intent: torch.set_num_threads(2) — ok",
            tag="BRAIN", level="DEBUG")
    except Exception as e:
        log(f"intent: torch.set_num_threads: {e}", tag="BRAIN", level="WARN")

    try:
        torch.set_num_interop_threads(1)
        log("intent: torch.set_num_interop_threads(1) — ok",
            tag="BRAIN", level="DEBUG")
    except Exception as e:
        log(f"intent: torch.set_num_interop_threads: {e}",
            tag="BRAIN", level="WARN")

    try:
        torch.set_flush_denormal(True)
        log("intent: torch.set_flush_denormal(True) — ok",
            tag="BRAIN", level="DEBUG")
    except Exception as e:
        log(f"intent: torch.set_flush_denormal: {e}",
            tag="BRAIN", level="WARN")


def _init_setfit():
    global _setfit_model
    if _setfit_model is not None:
        return _setfit_model

    with _lock:
        if _setfit_model is not None:
            return _setfit_model

        if not _MODEL_DIR.exists():
            log(f"intent: SetFit модель не найдена в {_MODEL_DIR} — "
                f"работаем только на exact+fuzzy", tag="BRAIN", level="ERROR")
            return None

        # ── MEM-OPT (патч 2): настроить torch ДО setfit ──
        _tune_torch()

        try:
            from setfit import SetFitModel
            model = SetFitModel.from_pretrained(str(_MODEL_DIR))

            # ── MEM-OPT (патч 3): eval-режим ──
            # Отключает dropout/batchnorm-тренинг и градиентные
            # буферы. SetFit обычно делает это сам, но проверить
            # стоит — если модель осталась в train, память течёт.
            try:
                if hasattr(model, "model") and model.model is not None:
                    model.model.eval()
                    log("intent: model.eval() — ok",
                        tag="BRAIN", level="DEBUG")
            except Exception as e:
                log(f"intent: model.eval: {e}", tag="BRAIN", level="WARN")

            _setfit_model = model
            log(f"intent: SetFit загружен из {_MODEL_DIR}", tag="BRAIN")
        except Exception as e:
            log(f"intent: SetFit не загрузился: {e}", tag="BRAIN", level="ERROR")
            return None

    return _setfit_model


def _setfit_predict(text: str) -> tuple[Intent, float] | None:
    """Предсказание SetFit + уверенность (max вероятность)."""
    model = _init_setfit()
    if model is None:
        return None
    try:
        # ── MEM-OPT (патч 3): inference_mode ──
        # Убирает градиентные буферы во время predict.
        import torch
        with torch.inference_mode():
            proba = model.predict_proba([text])
        if hasattr(proba, "numpy"):
            proba = proba.numpy()
        # proba shape: (1, n_classes)
        row = proba[0]
        # индекс максимума
        best_idx = int(max(range(len(row)), key=lambda i: row[i]))
        best_conf = float(row[best_idx])
        label = str(model.labels[best_idx])
        try:
            intent = Intent(label)
        except ValueError:
            return None
        return intent, best_conf
    except Exception as e:
        log(f"intent: SetFit predict error: {e}", tag="BRAIN", level="WARN")
        return None


# ── resolve ────────────────────────────────────────────────
def resolve(text: str) -> tuple[Intent, float]:
    """Вернуть (Intent, confidence). UNKNOWN — если не уверен.

    Приоритет:
      1. Exact-match → (intent, 1.0).
      2. SetFit → (intent, confidence) + пост-правила.
      3. Fuzzy fallback, если SetFit < порога.
    """
    if not text:
        return Intent.UNKNOWN, 0.0

    # 1. Exact-first.
    exact = _exact_match(text)
    if exact is not None:
        intent, conf = exact
        log(f"intent: «{text}» → {intent.value} (exact, conf={conf:.2f})",
            tag="BRAIN")
        return intent, conf

    # 2. SetFit.
    setfit = _setfit_predict(text)
    if setfit is not None:
        intent, conf = setfit
        # 3. Пост-правила.
        intent_fixed = _apply_post_rules(text, intent)
        if intent_fixed != intent:
            log(f"intent: «{text}» → {intent.value} → {intent_fixed.value} "
                f"(post-rule, setfit_conf={conf:.2f})", tag="BRAIN")
            return intent_fixed, conf
        if conf >= _THRESHOLD:
            log(f"intent: «{text}» → {intent.value} "
                f"(setfit, conf={conf:.2f})", tag="BRAIN")
            return intent, conf
        # 4. Fuzzy fallback, если SetFit не уверен.
        fuzzy = _fuzzy_fallback(text)
        if fuzzy is not None:
            fi, fc = fuzzy
            log(f"intent: «{text}» → {fi.value} "
                f"(fuzzy, conf={fc:.2f}, setfit={conf:.2f})", tag="BRAIN")
            return fi, fc
        return Intent.UNKNOWN, conf

    # SetFit недоступен — сразу fuzzy.
    fuzzy = _fuzzy_fallback(text)
    if fuzzy is not None:
        fi, fc = fuzzy
        log(f"intent: «{text}» → {fi.value} "
            f"(fuzzy, conf={fc:.2f}, setfit=None)", tag="BRAIN")
        return fi, fc
    return Intent.UNKNOWN, 0.0


def warmup() -> bool:
    """Прогреть SetFit (загрузить модель + первый predict)."""
    try:
        model = _init_setfit()
        if model is None:
            log("intent: warmup — SetFit не загрузился", tag="BRAIN", level="WARN")
            return False
        # первый predict — прогрев токенизатора/энкодера
        # ── MEM-OPT: inference_mode и здесь ──
        import torch
        with torch.inference_mode():
            _ = model.predict(["прогрев"])
        log("intent: warmup — SetFit готов", tag="BRAIN")
        return True
    except Exception as e:
        log(f"intent: warmup error: {e}", tag="BRAIN", level="WARN")
        return False