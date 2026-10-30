# brain/nlu/intent.py
# IntentResolver: IndexedRouter (FastEmbed) → Intent + confidence.
#
# UNKNOWN обязателен. Если confidence < threshold — UNKNOWN.
# Порог 0.5 — начальный. Подберём на реальных фразах (HIGH/MEDIUM/LOW).
#
# Калибровка (fit) — только при первом создании.
# Дальше — load_router из data/intent_router.json (без fit).
# Если меняешь _ROUTES / config/intent_fit.json — удали data/intent_router.json.
#
# Модель: sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
# (мультиязычная, русский есть, ~440 МБ на диске, ~300 МБ RAM, CPU, ONNX).
#
# B-новое (день 30): MUSIC_PLAY без мусорных объектов.
#   Раньше: "поставь группу", "включи группу", "поставь исполнителя",
#   "включи исполнителя" → intent=MUSIC_PLAY, confidence высокий, но
#   classify() даёт force_artist=True и cleaned="" → play_artist("") →
#   Яндекс ищет пустоту. Теперь: этих фраз в _ROUTES нет.
#   «Включи группу Ария» ловится через «включи арию» / «поставь арию».
#
# MOOD (новое): mood-фразы в MUSIC_PLAY.
#   «включи бодрое», «поставь весёлое», «хочу грустное» и т.д. — чтобы
#   «включи бодрое» → MUSIC_PLAY, а не VOLUME_UP (эмбеддинг «бодрое»
#   семантически близко к «громче», и без mood-фраз уходило в VOLUME_UP).
#
# FIX (UNKNOWN): убран Intent.UNKNOWN из _ROUTES.
#   Раньше UNKNOWN был отдельным route'ом с фразами «абракадабра», «собой»,
#   «музыку», «пятьдесят» и т.д. Это неправильно: UNKNOWN — не интент, а
#   fallback при confidence < threshold. Из-за route'а UNKNOWN «включи
#   бодрое» уходило в UNKNOWN (confidence 0.73), потому что эмбеддинг
#   «бодрое» ближе к «пятьдесят»/«музыку», чем к mood-фразам MUSIC_PLAY.
#   Теперь UNKNOWN — только fallback в resolve() при confidence < 0.5.
#   В types.py Intent.UNKNOWN остаётся (для fallback и для _fallback_empty).

import json
import threading

from shared import config as cfg
from shared.log import log
from brain.nlu.types import Intent


_router = None
_lock = threading.Lock()
_ROUTER_FILE = cfg.DATA_DIR / "intent_router.json"
_FIT_FILE = cfg.CONFIG_DIR / "intent_fit.json"

# Порог: ниже — UNKNOWN. Начальный 0.5.
_THRESHOLD = 0.5


# ── Маршруты (17 интентов, без UNKNOWN) ─────────────────────
# UNKNOWN — не route, а fallback при confidence < _THRESHOLD.
# Если добавить UNKNOWN в _ROUTES, он конкурирует с реальными интентами
# и «съедает» фразы с высоким confidence (например, «включи бодрое»).
_ROUTES = {
    Intent.MUSIC_PLAY: [
        "включи музыку", "вруби что-нибудь", "поставь арию",
        "давай рок", "хочу послушать киша", "навали арию",
        "зажги рок", "вруби радио", "поставь что-нибудь потяжелее",
        "хочу музыку",
        "поставь киша", "включи киша",
        # mood-фразы (чтобы «включи бодрое» → MUSIC_PLAY, а не VOLUME_UP)
        "включи бодрое", "поставь бодрое", "хочу бодрое",
        "давай бодрое", "вруби бодрое", "навали бодрое",
        "включи весёлое", "поставь весёлое", "хочу весёлое",
        "давай весёлое", "включи веселое", "поставь веселое",
        "включи грустное", "поставь грустное", "хочу грустное",
        "давай грустное", "включи грустную музыку",
        "включи спокойное", "поставь спокойное", "хочу спокойное",
        "давай спокойное", "включи спокойную музыку",
        "включи энергичное", "поставь энергичное", "хочу энергичное",
        "включи радостное", "поставь радостное", "хочу радостное",
        "включи агрессивное", "поставь агрессивное",
        "включи мрачное", "поставь мрачное",
        "включи эпичное", "поставь эпичное",
        "включи мечтательное", "поставь мечтательное",
        "включи сентиментальное", "поставь сентиментальное",
        "включи крутое", "поставь крутое",
        "включи душевное", "поставь душевное",
        "включи красивое", "поставь красивое",
        "включи расслабленное", "поставь расслабленное",
        "включи осеннее", "поставь осеннее",
        "включи зимнее", "поставь зимнее",
        "включи весеннее", "поставь весеннее",
        "включи летнее", "поставь летнее",
        "включи новогоднее", "поставь новогоднее",
        "включи для влюблённых", "поставь для влюблённых",
        "включи романтичное", "поставь романтичное",
        "включи что-нибудь бодрое", "поставь что-нибудь весёлое",
        "включи что-нибудь грустное", "поставь что-нибудь спокойное",
        "включи настроение", "хочу настроение", "поставь настроение",
        "включи по настроению", "хочу по настроению",
    ],
    Intent.MUSIC_WAVE: [
        "включи мою волну", "моя волна", "вруби мою волну",
        "включи русскую волну", "русская волна",
        "поставь мою волну", "хочу мою волну",
        "включи мою музыку", "давай мою волну", "вруби волну",
    ],
    Intent.MUSIC_STOP: [
        "стоп", "останови", "остановись", "стоп музыка",
        "выключи музыку", "хватит музыки", "прекрати музыку",
        "стоп всё", "останови музыку", "выруби музыку",
    ],
    Intent.MUSIC_PAUSE: [
        "пауза", "поставь на паузу", "сделай паузу",
        "приостанови", "паузу", "на паузу",
        "поставь паузу", "останови на время", "пауза музыка", "перерыв",
    ],
    Intent.MUSIC_RESUME: [
        "продолжи", "продолжить", "продолжай", "возобнови",
        "играй дальше", "плей", "играть", "включи снова",
        "продолжаем", "возобновить",
    ],
    Intent.MUSIC_NEXT: [
        "дальше", "следующий", "следующий трек", "пропусти",
        "переключи", "next", "дальше давай", "пропустить",
        "следующая песня", "переключи трек",
    ],
    Intent.MUSIC_PREV: [
        "назад", "предыдущий", "предыдущая", "вернись",
        "верни", "предыдущий трек", "назад давай",
        "прошлый трек", "верни назад", "предыдущая песня",
    ],
    Intent.VOLUME_UP: [
        "громче", "погромче", "сделай громче", "прибавь звук",
        "увеличь громкость", "громче давай", "звук громче",
        "прибавь громкости", "сделай погромче", "увеличь звук",
    ],
    Intent.VOLUME_DOWN: [
        "тише", "потише", "сделай тише", "убавь звук",
        "уменьши громкость", "тише давай", "звук тише",
        "убавь громкости", "сделай потише", "уменьши звук",
    ],
    Intent.VOLUME_SET: [
        "громкость 50", "громкость пятьдесят", "звук 30",
        "поставь громкость 70", "громкость на 40",
        "громкость 100", "звук на 20", "громкость 0",
        "поставь звук 50", "громкость сто",
    ],
    Intent.WEATHER_QUERY: [
        "погода", "что на улице", "дождь будет",
        "какая погода завтра", "что там с погодой",
        "зонт нужен", "температура за окном",
        "погода на сегодня", "будет ли снег", "что за окном",
    ],
    Intent.SYSTEM_STATUS: [
        "статус", "как система", "что с компом",
        "проверь систему", "как дела у компа",
        "сколько памяти", "загружен ли процессор",
        "статус системы", "что с диском", "как железо",
    ],
    Intent.REMINDER_ADD: [
        "напомни", "не забудь", "запомни",
        "напомни через час", "поставь напоминание",
        "напомни завтра", "не дай забыть",
        "запиши в напоминания", "что я забыл", "когда мне",
    ],
    Intent.CLEANUP_DISK: [
        "почисти диск", "почисти c", "очисти диск",
        "убери мусор", "почисти систему",
        "освободи место", "почисти комп", "очисти c",
        "удали временные файлы", "почисти диск c",
    ],
    Intent.CLEANUP_RECYCLE: [
        "очисти корзину", "убери корзину", "почисти корзину",
        "очистить корзину", "убрать корзину",
        "почистить корзину", "корзина", "пустая корзина",
        "очисти мусорку", "убери мусорку",
    ],
    Intent.SMALLTALK: [
        "привет", "как дела", "кто ты",
        "спасибо", "здравствуй",
        "как ты", "что ты умеешь",
        "добрый день", "пока", "как жизнь",
    ],
    Intent.LLM_QUESTION: [
        "почему небо голубое", "расскажи о чём-нибудь",
        "что такое", "объясни", "почему",
        "как работает", "расскажи про", "что значит",
        "зачем нужно", "в чём смысл",
        "расскажи про группу", "расскажи про артиста",
        "почему ария", "почему группа",
        "что такое музыка", "что такое волна",
        "расскажи про песню",
        # A2-фикс: «почему X так называется» → LLM_QUESTION, не MUSIC_PLAY
        "почему ария так называется",
        "почему группа так называется",
        "почему песня так называется",
    ],
}


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