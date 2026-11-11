# brain/nlu/intent.py
# IntentResolver: ONNX (SetFit, fp32) → Intent + confidence.
#
# Приоритет:
#   1. Exact-match с фразой из config/intent_routes.json → (intent, 1.0).
#   2. ONNX (models/setfit_intents_onnx/model.onnx) → (intent, confidence).
#   3. Пост-правила (VOLUME_UP/DOWN, CLEANUP_DISK/RECYCLE,
#      SMALLTALK/SYSTEM_STATUS, LLM_QUESTION/MUSIC_NOW_PLAYING).
#   4. Fuzzy fallback (difflib, порог 0.8) — если ONNX не уверен.
#
# UNKNOWN — если всё ниже порога.
# warmup() — прогреть ONNX в фоне при старте brain.
#
# ── ONNX (2026-11-11): замена PyTorch SetFit ──────────────────
#   Было: from setfit import SetFitModel → 29.5с импорт, 35с warmup.
#   Стало: onnxruntime + tokenizers.Tokenizer + labels.json.
#     - import brain.main: 0.1с (setfit не тянется при старте)
#     - загрузка ONNX: 0.7с
#     - warmup: 1-2с (вместо 35)
#     - инференс: 5-10мс (было 20)
#
#   Модель: models/setfit_intents_onnx/model.onnx (111 МБ, fp32).
#   Токенизатор: models/setfit_intents/tokenizer.json (2.4 МБ).
#   Labels: models/setfit_intents_onnx/labels.json (18 классов).
#
#   ВАЖНО: onnxruntime может вернуть labels (строки) или probs
#   (числа). Обрабатываем оба случая. Для набора данных SetFit
#   обычно отдаёт labels, поэтому conf=1.0 — это нормально.

import json
import threading
import difflib

from shared import config as cfg
from shared.log import log
from brain.nlu.types import Intent


_onnx_session = None
_onnx_tokenizer = None
_onnx_labels = None
_lock = threading.Lock()

_ROUTES_FILE = cfg.CONFIG_DIR / "intent_routes.json"

# ONNX + tokenizer + labels
_ONNX_DIR = cfg.DATA_DIR.parent / "models" / "setfit_intents_onnx"
_ONNX_MODEL = _ONNX_DIR / "model.onnx"
_ONNX_LABELS = _ONNX_DIR / "labels.json"
_TOKENIZER_FILE = cfg.DATA_DIR.parent / "models" / "setfit_intents" / "tokenizer.json"

# Порог уверенности: ниже — пробуем fuzzy, потом UNKNOWN.
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


# ── Пост-правила ───────────────────────────────────────────
_VOLUME_DOWN_WORDS = ("уменьши", "убавь", "тише", "потише", "прикрути", "минимум")
_VOLUME_UP_WORDS = ("увеличь", "прибавь", "громче", "погромче", "максимум")
_RECYCLE_WORDS = ("корзин", "мусорк")
_DISK_WORDS = ("диск",)
_SMALLTALK_AS_SYSTEM = ("как дела", "как жизнь", "как настроение", "как ты")
_LLM_QUESTION_WORDS = ("что такое", "что значит", "объясни", "почему")


def _apply_post_rules(text: str, intent: Intent) -> Intent:
    """Чинит известные пары, где модель стабильно путается."""
    t = text.lower()

    if intent in (Intent.VOLUME_UP, Intent.VOLUME_DOWN):
        if any(w in t for w in _VOLUME_DOWN_WORDS):
            return Intent.VOLUME_DOWN
        if any(w in t for w in _VOLUME_UP_WORDS):
            return Intent.VOLUME_UP

    if intent in (Intent.CLEANUP_DISK, Intent.CLEANUP_RECYCLE):
        if any(w in t for w in _RECYCLE_WORDS):
            return Intent.CLEANUP_RECYCLE
        if any(w in t for w in _DISK_WORDS):
            return Intent.CLEANUP_DISK

    if intent == Intent.SYSTEM_STATUS:
        if any(w in t for w in _SMALLTALK_AS_SYSTEM):
            return Intent.SMALLTALK

    if intent == Intent.MUSIC_NOW_PLAYING:
        if any(w in t for w in _LLM_QUESTION_WORDS):
            return Intent.LLM_QUESTION

    return intent


# ── ONNX ───────────────────────────────────────────────────
def _init_setfit():
    """Загрузить ONNX-модель + tokenizers + labels."""
    global _onnx_session, _onnx_tokenizer, _onnx_labels
    if _onnx_session is not None:
        return _onnx_session

    with _lock:
        if _onnx_session is not None:
            return _onnx_session

        if not _ONNX_MODEL.exists():
            log(f"intent: ONNX не найден в {_ONNX_MODEL}",
                tag="BRAIN", level="ERROR")
            return None
        if not _TOKENIZER_FILE.exists():
            log(f"intent: tokenizer.json не найден в {_TOKENIZER_FILE}",
                tag="BRAIN", level="ERROR")
            return None
        if not _ONNX_LABELS.exists():
            log(f"intent: labels.json не найден в {_ONNX_LABELS}",
                tag="BRAIN", level="ERROR")
            return None

        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer

            _onnx_session = ort.InferenceSession(str(_ONNX_MODEL))

            _onnx_tokenizer = Tokenizer.from_file(str(_TOKENIZER_FILE))
            _onnx_tokenizer.enable_truncation(max_length=512)

            with open(_ONNX_LABELS, encoding="utf-8") as f:
                _onnx_labels = json.load(f)

            log(f"intent: ONNX загружен из {_ONNX_DIR} "
                f"({len(_onnx_labels)} классов)", tag="BRAIN")
        except Exception as e:
            log(f"intent: ONNX не загрузился: {e}", tag="BRAIN", level="ERROR")
            return None

    return _onnx_session


def _setfit_predict(text: str) -> tuple[Intent, float] | None:
    """ONNX-инференс + confidence."""
    session = _init_setfit()
    if session is None:
        return None
    try:
        import numpy as np

        enc = _onnx_tokenizer.encode(text)
        input_ids = np.array([enc.ids], dtype=np.int64)
        attention_mask = np.array([enc.attention_mask], dtype=np.int64)

        input_names = [i.name for i in session.get_inputs()]
        feed = {"input_ids": input_ids, "attention_mask": attention_mask}
        if "token_type_ids" in input_names:
            feed["token_type_ids"] = np.zeros_like(input_ids)

        out = session.run(None, feed)[0]

        first = out[0] if hasattr(out, "__getitem__") else out
        if isinstance(first, str):
            label = first
            conf = 1.0
        else:
            probs = np.array(first)
            idx = int(np.argmax(probs))
            label = _onnx_labels[idx]
            s = float(probs.sum())
            if abs(s - 1.0) > 0.05:
                e = np.exp(probs - probs.max())
                probs = e / e.sum()
            conf = float(probs[idx])

        try:
            intent = Intent(label)
        except ValueError:
            return None
        return intent, conf
    except Exception as e:
        log(f"intent: ONNX predict error: {e}", tag="BRAIN", level="WARN")
        return None


# ── resolve ────────────────────────────────────────────────
def resolve(text: str) -> tuple[Intent, float]:
    if not text:
        return Intent.UNKNOWN, 0.0

    # 1. Exact-first.
    exact = _exact_match(text)
    if exact is not None:
        intent, conf = exact
        log(f"intent: «{text}» → {intent.value} (exact, conf={conf:.2f})",
            tag="BRAIN")
        return intent, conf

    # 2. ONNX.
    setfit = _setfit_predict(text)
    if setfit is not None:
        intent, conf = setfit
        # 3. Пост-правила.
        intent_fixed = _apply_post_rules(text, intent)
        if intent_fixed != intent:
            log(f"intent: «{text}» → {intent.value} → {intent_fixed.value} "
                f"(post-rule, onnx_conf={conf:.2f})", tag="BRAIN")
            return intent_fixed, conf
        if conf >= _THRESHOLD:
            log(f"intent: «{text}» → {intent.value} "
                f"(onnx, conf={conf:.2f})", tag="BRAIN")
            return intent, conf
        # 4. Fuzzy fallback, если ONNX не уверен.
        fuzzy = _fuzzy_fallback(text)
        if fuzzy is not None:
            fi, fc = fuzzy
            log(f"intent: «{text}» → {fi.value} "
                f"(fuzzy, conf={fc:.2f}, onnx={conf:.2f})", tag="BRAIN")
            return fi, fc
        return Intent.UNKNOWN, conf

    # ONNX недоступен — сразу fuzzy.
    fuzzy = _fuzzy_fallback(text)
    if fuzzy is not None:
        fi, fc = fuzzy
        log(f"intent: «{text}» → {fi.value} "
            f"(fuzzy, conf={fc:.2f}, onnx=None)", tag="BRAIN")
        return fi, fc
    return Intent.UNKNOWN, 0.0


def warmup() -> bool:
    """Прогреть ONNX (загрузить сессию + tokenizer + первый predict)."""
    try:
        session = _init_setfit()
        if session is None:
            log("intent: warmup — ONNX не загрузился", tag="BRAIN", level="WARN")
            return False
        # первый predict — прогрев ONNX-сессии
        _ = _setfit_predict("прогрев")
        log("intent: warmup — ONNX готов", tag="BRAIN")
        return True
    except Exception as e:
        log(f"intent: warmup error: {e}", tag="BRAIN", level="WARN")
        return False