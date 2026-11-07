# brain/nlu/intent.py
# IntentResolver: IndexedRouter (FastEmbed) → Intent + confidence.
#
# UNKNOWN обязателен. Если confidence < threshold — UNKNOWN.
# Порог 0.5 — начальный. Подберём на реальных фразах (HIGH/MEDIUM/LOW).
#
# Калибровка (fit) — только при первом создании.
# Дальше — load_router из data/intent_router.json (без fit).
#
# FIX (routes → JSON): _ROUTES вынесен в config/intent_routes.json.
# FIX (exact-first): resolve() сначала проверяет ТОЧНОЕ совпадение.
# WARMUP (новое): warmup() — прогреть router при старте brain (в фоне).
#
# FUZZY FALLBACK (2026-11-07, #4): если эмбеддинги дали confidence < 0.5
#   (UNKNOWN или «на грани»), пробуем fuzzy по ВСЕМ фразам ВСЕХ интентов
#   (difflib.SequenceMatcher, порог 0.8). Пример: «включи радio» (0.45)
#   → fuzzy «включи радио» (0.92) → MUSIC_PLAY (1.00). Дёшево (difflib
#   быстрый), точно (по фразам), без LLM. Не срабатывает, если
#   эмбеддинги уже дали confidence >= 0.5 (тогда это не «ошибка ASR»,
#   а нормальный распознанный intent).

import json
import threading
import difflib

from shared import config as cfg
from shared.log import log
from brain.nlu.types import Intent


_router = None
_lock = threading.Lock()
_ROUTER_FILE = cfg.DATA_DIR / "intent_router.json"
_FIT_FILE = cfg.CONFIG_DIR / "intent_fit.json"
_ROUTES_FILE = cfg.CONFIG_DIR / "intent_routes.json"

# Порог: ниже — UNKNOWN.
_THRESHOLD = 0.5
# Fuzzy fallback порог (2026-11-07, #4): fuzzy по фразам всех интентов,
# если эмбеддинги < 0.5. 0.8 — безопасно («включи радio» → «включи радио»
# 0.92 → MUSIC_PLAY; «привет» → «привет» 1.0 → SMALLTALK; «абракадабра»
# → «включи арию» 0.3 → не FOUND).
_FUZZY_FALLBACK_THRESHOLD = 0.80


# ── Загрузка routes из config/intent_routes.json ────────────
def _load_routes() -> dict[Intent, list[str]]:
    """Загрузить config/intent_routes.json → {Intent: [фразы]}."""
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
            log(f"intent: routes[{intent_name}] — не list — пропуск",
                tag="BRAIN", level="WARN")
            continue
        cleaned = [p.strip() for p in phrases if isinstance(p, str) and p.strip()]
        if cleaned:
            routes[intent] = cleaned
    log(f"intent: routes загружены: {len(routes)} интентов, "
        f"{sum(len(v) for v in routes.values())} фраз", tag="BRAIN")
    return routes


_ROUTES: dict[Intent, list[str]] = _load_routes()


# ── Exact-match (приоритет над эмбеддингами) ────────────────
def _exact_match(text: str) -> tuple[Intent, float] | None:
    """Точное совпадение с фразой из _ROUTES (case-insensitive, strip)."""
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


# ── FUZZY FALLBACK (2026-11-07, #4) ─────────────────────────
_fuzzy_index: list[tuple[str, Intent]] | None = None


def _build_fuzzy_index() -> list[tuple[str, Intent]]:
    """Плоский список (фраза, intent) из _ROUTES. Лениво, 1 раз."""
    index: list[tuple[str, Intent]] = []
    for intent, phrases in _ROUTES.items():
        for phrase in phrases:
            p = phrase.lower().strip()
            if p:
                index.append((p, intent))
    return index


def _fuzzy_fallback(text: str) -> tuple[Intent, float] | None:
    """Fuzzy по всем фразам всех интентов (difflib, порог 0.8).

    «включи радio» → «включи радио» (0.92) → MUSIC_PLAY.
    «абракадабра» → «включи арию» (0.3) → None.
    """
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


# ── Загрузка held-out фраз из config/intent_fit.json ─────────
def _load_fit() -> tuple[list[str], list[Intent]]:
    """Загрузить config/intent_fit.json → (X, Y)."""
    if not _FIT_FILE.exists():
        log(f"intent: {_FIT_FILE} не найден, fit пропущен", tag="BRAIN", level="WARN")
        return [], []
    try:
        with open(_FIT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        log(f"intent: ошибка чтения {_FIT_FILE}: {e}", tag="BRAIN", level="WARN")
        return [], []

    if not isinstance(data, dict):
        log(f"intent: {_FIT_FILE} — не dict, fit пропущен", tag="BRAIN", level="WARN")
        return [], []

    X, Y = [], []
    for intent_name, phrases in data.items():
        try:
            intent = Intent(intent_name)
        except ValueError:
            log(f"intent: неизвестный intent в fit: {intent_name}", tag="BRAIN", level="WARN")
            continue
        if not isinstance(phrases, list):
            continue
        for phrase in phrases:
            if isinstance(phrase, str) and phrase.strip():
                X.append(phrase.strip())
                Y.append(intent)
    return X, Y


def _check_overlap(X: list[str]) -> list[str]:
    """Проверить пересечения fit-фраз с _ROUTES (utterances)."""
    routes_set = {u.strip() for utts in _ROUTES.values() for u in utts}
    return [p for p in X if p.strip() in routes_set]


def _init_router():
    global _router
    if _router is not None:
        return _router

    with _lock:
        if _router is not None:
            return _router

        try:
            from semantic_router import IndexedRouter, Route, load_router
            from semantic_router.encoders import FastEmbedEncoder
        except ImportError as e:
            log(f"intent: semantic_router не установлен: {e}", tag="BRAIN", level="ERROR")
            return None

        if not _ROUTES:
            log("intent: _ROUTES пуст — router не создастся", tag="BRAIN", level="ERROR")
            return None

        if _ROUTER_FILE.exists():
            try:
                router = load_router(path=str(_ROUTER_FILE))
                log(f"intent: router загружен из {_ROUTER_FILE} (без fit)", tag="BRAIN")
                _router = router
                return _router
            except Exception as e:
                log(f"intent: load_router error: {e} — пересоздаю", tag="BRAIN", level="WARN")
                try:
                    _ROUTER_FILE.unlink()
                except Exception:
                    pass

        try:
            routes = [
                Route(name=intent.value, utterances=utterances)
                for intent, utterances in _ROUTES.items()
            ]
            encoder = FastEmbedEncoder(
                name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
            )
            router = IndexedRouter(encoder=encoder, routes=routes)
            router.confidence_threshold = _THRESHOLD

            X, Y = _load_fit()
            if X:
                overlap = _check_overlap(X)
                if overlap:
                    log(f"intent: WARN {len(overlap)}/{len(X)} fit-фраз "
                        f"пересекаются с _ROUTES: {overlap[:5]}",
                        tag="BRAIN", level="WARN")
                try:
                    report = router.fit(X=X, y=Y, method="platt")
                    log(f"intent: router создан, fit accuracy={report.accuracy:.2f}, "
                        f"ece={report.ece:.3f}", tag="BRAIN")
                except Exception as e:
                    log(f"intent: fit error: {e} — без калибровки",
                        tag="BRAIN", level="WARN")
            else:
                log("intent: fit-фраз нет — без калибровки (confidence_threshold)",
                    tag="BRAIN", level="WARN")

            try:
                _ROUTER_FILE.parent.mkdir(parents=True, exist_ok=True)
                router.to_json(path=str(_ROUTER_FILE))
                log(f"intent: router сохранён в {_ROUTER_FILE}", tag="BRAIN")
            except Exception as e:
                log(f"intent: to_json error: {e}", tag="BRAIN", level="WARN")

            _router = router
        except Exception as e:
            log(f"intent: router не создался: {e}", tag="BRAIN", level="ERROR")
            return None

    return _router


def resolve(text: str) -> tuple[Intent, float]:
    """Вернуть (Intent, confidence). UNKNOWN — если не уверен.

    Приоритет:
      1. Exact match с фразой из _ROUTES → (intent, 1.0). Без эмбеддингов.
      2. Эмбеддинги (IndexedRouter) → (intent, confidence) или UNKNOWN.
      3. FUZZY FALLBACK (2026-11-07, #4): если эмбеддинги < 0.5 → fuzzy
         по всем фразам всех интентов (difflib, порог 0.8). «включи радio»
         (0.45) → «включи радио» (0.92) → MUSIC_PLAY (1.00). Без LLM.
    """
    if not text:
        return Intent.UNKNOWN, 0.0

    # 1. Exact-first: точное совпадение с routes.
    exact = _exact_match(text)
    if exact is not None:
        intent, conf = exact
        log(f"intent: «{text}» → {intent.value} (exact, conf={conf:.2f})",
            tag="BRAIN")
        return intent, conf

    # 2. Эмбеддинги — как раньше.
    router = _init_router()
    if router is None:
        # Router не создался — сразу fuzzy fallback (без эмбеддингов).
        fuzzy = _fuzzy_fallback(text)
        if fuzzy is not None:
            intent, conf = fuzzy
            log(f"intent: «{text}» → {intent.value} "
                f"(fuzzy, conf={conf:.2f}, router=None)", tag="BRAIN")
            return intent, conf
        return Intent.UNKNOWN, 0.0
    try:
        choice = router(text)
        emb_conf = float(choice.confidence or 0.0)
        if not choice.name or emb_conf < _THRESHOLD:
            # 3. FUZZY FALLBACK (2026-11-07, #4): эмбеддинги < 0.5 → fuzzy.
            fuzzy = _fuzzy_fallback(text)
            if fuzzy is not None:
                intent, conf = fuzzy
                log(f"intent: «{text}» → {intent.value} "
                    f"(fuzzy, conf={conf:.2f}, emb={emb_conf:.2f})", tag="BRAIN")
                return intent, conf
            return Intent.UNKNOWN, emb_conf
        try:
            intent = Intent(choice.name)
        except ValueError:
            return Intent.UNKNOWN, emb_conf
        log(f"intent: «{text}» → {intent.value} "
            f"(confidence={emb_conf:.2f})", tag="BRAIN")
        return intent, emb_conf
    except Exception as e:
        log(f"intent: resolve error: {e}", tag="BRAIN", level="WARN")
        # Даже при ошибке эмбеддингов — fuzzy fallback.
        fuzzy = _fuzzy_fallback(text)
        if fuzzy is not None:
            intent, conf = fuzzy
            log(f"intent: «{text}» → {intent.value} "
                f"(fuzzy, conf={conf:.2f}, emb_error)", tag="BRAIN")
            return intent, conf
        return Intent.UNKNOWN, 0.0


def warmup() -> bool:
    """Прогреть router (загрузить intent_router.json + encoder)."""
    try:
        router = _init_router()
        if router is None:
            log("intent: warmup — router не создался", tag="BRAIN", level="WARN")
            return False
        log("intent: warmup — router готов", tag="BRAIN")
        return True
    except Exception as e:
        log(f"intent: warmup error: {e}", tag="BRAIN", level="WARN")
        return False