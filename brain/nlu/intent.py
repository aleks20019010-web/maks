# brain/nlu/intent.py
# IntentResolver: IndexedRouter (FastEmbed) → Intent + confidence.
#
# UNKNOWN обязателен. Если confidence < threshold — UNKNOWN.
# Порог 0.5 — начальный. Подберём на реальных фразах (HIGH/MEDIUM/LOW).
#
# Калибровка (fit) — только при первом создании.
# Дальше — load_router из data/intent_router.json (без fit).
# Если меняешь config/intent_routes.json / config/intent_fit.json —
# удали data/intent_router.json.
#
# Модель: sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
# (мультиязычная, русский есть, ~440 МБ на диске, ~300 МБ RAM, CPU, ONNX).
#
# FIX (routes → JSON): _ROUTES вынесен в config/intent_routes.json.
#   _ROUTES — данные, не код. Правится без правки Python. Тесты могут
#   подменять JSON, не патчить модуль.
#   Формат: {"MUSIC_PLAY": ["фраза1", ...], "VOLUME_UP": [...], ...}.
#   Если файл не найден или битый — пустой dict + WARN (router без routes
#   не создастся, resolve → UNKNOWN; это лучше, чем падать).
#
# FIX (UNKNOWN): убран Intent.UNKNOWN из routes.
#   UNKNOWN — не route, а fallback при confidence < threshold.
#
# MOOD: mood-фразы в MUSIC_PLAY (бодрое/весёлое/грустное), чтобы не
#   уходило в VOLUME_UP.
#
# FIX (послушать): MUSIC_PLAY += фразы с «послушать»/«послушаем»
#   («вруби что нибудь послушать» → MUSIC_PLAY, а не VOLUME_UP).
#
# WARMUP (новое): warmup() — прогреть router при старте brain (в фоне).
#   Раньше первая команда ждала 10-15 сек (загрузка intent_router.json +
#   инициализация FastEmbedEncoder/ONNX). Теперь brain/main.py вызывает
#   warmup() в фоне при старте, router греется параллельно. Первая команда
#   → 0.05 сек (если warmup успел) или подождёт (если команда пришла раньше).

import json
import threading

from shared import config as cfg
from shared.log import log
from brain.nlu.types import Intent


_router = None
_lock = threading.Lock()
_ROUTER_FILE = cfg.DATA_DIR / "intent_router.json"
_FIT_FILE = cfg.CONFIG_DIR / "intent_fit.json"
_ROUTES_FILE = cfg.CONFIG_DIR / "intent_routes.json"

# Порог: ниже — UNKNOWN. Начальный 0.5.
_THRESHOLD = 0.5


# ── Загрузка routes из config/intent_routes.json ────────────
def _load_routes() -> dict[Intent, list[str]]:
    """Загрузить config/intent_routes.json → {Intent: [фразы]}.

    Если файла нет / битый — {} + WARN. Router без routes не создастся,
    resolve → UNKNOWN (это лучше, чем падать).
    """
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


# Загружаем при импорте (один раз). Тесты могут monkeypatch'ить _ROUTES.
_ROUTES: dict[Intent, list[str]] = _load_routes()


# ── Загрузка held-out фраз из config/intent_fit.json ─────────
def _load_fit() -> tuple[list[str], list[Intent]]:
    """Загрузить config/intent_fit.json → (X, Y).

    Формат: {"MUSIC_PLAY": ["фраза1", ...], ..., "UNKNOWN": [...]}.
    Если файла нет или пуст — ([], []).
    """
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
    """Проверить пересечения fit-фраз с _ROUTES (utterances).

    Возвращает список фраз, которые есть в _ROUTES.
    """
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

        # 1. Если файл есть — загружаем (без fit).
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

        # 2. Файла нет — создаём.
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

            # fit — только если есть held-out фразы
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
    """Вернуть (Intent, confidence). UNKNOWN — если не уверен."""
    if not text:
        return Intent.UNKNOWN, 0.0
    router = _init_router()
    if router is None:
        return Intent.UNKNOWN, 0.0
    try:
        choice = router(text)
        if not choice.name or choice.confidence < _THRESHOLD:
            return Intent.UNKNOWN, float(choice.confidence or 0.0)
        try:
            intent = Intent(choice.name)
        except ValueError:
            return Intent.UNKNOWN, float(choice.confidence or 0.0)
        log(f"intent: «{text}» → {intent.value} "
            f"(confidence={choice.confidence:.2f})", tag="BRAIN")
        return intent, float(choice.confidence)
    except Exception as e:
        log(f"intent: resolve error: {e}", tag="BRAIN", level="WARN")
        return Intent.UNKNOWN, 0.0


def warmup() -> bool:
    """Прогреть router (загрузить intent_router.json + encoder).

    Вызывается из brain/main.py при старте (в фоне). Первая команда
    не ждёт 10-15 сек холодного старта — router уже в памяти.
    True — router готов, False — не удалось.

    Идемпотентно: если router уже загружен (_router is not None) —
    возвращает True сразу (без повторной загрузки).
    """
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