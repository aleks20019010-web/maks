# brain/semantic.py
# B4: semantic-router (FastEmbed, IndexedRouter).
#
# Слой ПЕРЕД LLM: если router не понял (find_handler None), спрашиваем
# semantic.route(text). Если уверен (confidence > threshold) — вызываем
# скил по имени. Иначе — None (router идёт в LLM, как сейчас).
#
# ВАЖНО: semantic-router решает только «это про музыку / погоду / ...».
# Что именно (артист, жанр, радио) — решает classify.py внутри MusicSkill.
#
# fit() на held-out фразах (не utterances!). Иначе калибратор думает,
# что реальные запросы неуверенные. Метки None = «ни один маршрут».
#
# Модель: intfloat/multilingual-e5-small (384 dim, ~70 МБ, CPU, ONNX).
# Первый вызов скачивает, потом локально.

import threading

from shared.log import log


_router = None
_lock = threading.Lock()


# ── Интенты (10) ──────────────────────────────────────────────
_ROUTES = {
    "music": [
        "включи музыку", "вруби что-нибудь", "поставь арию",
        "давай рок", "хочу послушать киша", "вруби радио",
        "включи попсу", "поставь что-нибудь потяжелее",
        "хочу музыку", "вруби мою волну",
    ],
    "weather": [
        "погода", "что на улице", "дождь будет",
        "какая погода завтра", "что там с погодой",
        "зонт нужен", "температура за окном",
        "погода на сегодня", "будет ли снег", "что за окном",
    ],
    "system": [
        "статус", "как система", "что с компом",
        "проверь систему", "как дела у компа",
        "сколько памяти", "загружен ли процессор",
        "статус системы", "что с диском", "как железо",
    ],
    "reminders": [
        "напомни", "не забудь", "запомни",
        "напомни через час", "поставь напоминание",
        "напомни завтра", "не дай забыть",
        "запиши в напоминания", "что я забыл", "когда мне",
    ],
    "cleanup": [
        "почисти диск", "очисти корзину", "убери мусор",
        "почисти c", "очисти диск",
        "убери корзину", "почисти систему",
        "освободи место", "удали временные файлы", "почисти комп",
    ],
    "smalltalk": [
        "привет", "как дела", "кто ты",
        "спасибо", "здравствуй",
        "как ты", "что ты умеешь",
        "добрый день", "пока", "как жизнь",
    ],
    "controls": [
        "дальше", "пауза", "громче",
        "тише", "стоп", "следующий",
        "назад", "продолжи", "громкость 50", "что играет",
    ],
    "llm": [
        "почему небо голубое", "расскажи о чём-нибудь",
        "что такое", "объясни", "почему",
        "как работает", "расскажи про", "что значит",
        "зачем нужно", "в чём смысл",
    ],
    "jellyfin": [
        "включи фильм", "что смотрят", "включи сериал",
        "что играет в jellyfin", "статус сервера",
        "найди фильм", "что у младших", "что у старших",
        "пауза jellyfin", "продолжи jellyfin",
    ],
    "none": [
        "абракадабра", "собой", "музыку",
        "ты кино", "включить", "продолжит",
        "кишат", "скучаю", "пятьдесят", "сорок пять",
    ],
}


# ── Held-out для fit() (НЕ utterances) ───────────────────────
_FIT_X = [
    "вруби чего-нибудь", "поставь арию", "вруби рок", "включи радио",
    "что с погодой", "дождь будет завтра", "зонт нужен",
    "статус компа", "сколько памяти", "что с диском",
    "напомни через 5 минут", "запомни завтра врач",
    "почисти корзину", "убери мусор", "очисти c",
    "привет", "как дела", "спасибо",
    "дальше", "пауза", "громче", "что играет",
    "почему небо голубое", "расскажи о чём-нибудь",
    "включи фильм", "что смотрят",
    "абракадабра", "собой", "ты кино", "пятьдесят",
]

_FIT_Y = [
    "music", "music", "music", "music",
    "weather", "weather", "weather",
    "system", "system", "system",
    "reminders", "reminders",
    "cleanup", "cleanup", "cleanup",
    "smalltalk", "smalltalk", "smalltalk",
    "controls", "controls", "controls", "controls",
    "llm", "llm",
    "jellyfin", "jellyfin",
    None, None, None, None,
]


def _init_router():
    global _router
    if _router is not None:
        return _router

    with _lock:
        if _router is not None:
            return _router

        try:
            from semantic_router import IndexedRouter, Route
            from semantic_router.encoders import FastEmbedEncoder
        except ImportError as e:
            log(f"semantic: semantic_router не установлен: {e}", tag="BRAIN", level="ERROR")
            return None

        try:
            routes = [
                Route(name=name, utterances=utterances)
                for name, utterances in _ROUTES.items()
                if name != "none"
            ]
            encoder = FastEmbedEncoder(name="intfloat/multilingual-e5-small")
            router = IndexedRouter(encoder=encoder, routes=routes)
            router.confidence_threshold = 0.5
            report = router.fit(X=_FIT_X, y=_FIT_Y, method="platt")
            log(f"semantic: router готов, fit accuracy={report.accuracy:.2f}, "
                f"ece={report.ece:.3f}", tag="BRAIN")
            _router = router
        except Exception as e:
            log(f"semantic: router не создался: {e}", tag="BRAIN", level="ERROR")
            return None

    return _router


def route(text: str) -> str | None:
    """Вернуть имя интента или None. Быстро (<50 мс после init)."""
    if not text:
        return None
    router = _init_router()
    if router is None:
        return None
    try:
        choice = router(text)
        if choice.name and choice.name != "none":
            log(f"semantic: «{text}» → {choice.name} "
                f"(confidence={choice.confidence:.2f})", tag="BRAIN")
            return choice.name
    except Exception as e:
        log(f"semantic: route error: {e}", tag="BRAIN", level="WARN")
    return None


def get_handler(name: str):
    """Вернуть экземпляр скила по имени интента."""
    if not name:
        return None
    try:
        if name == "music":
            from brain.skills.music.skill import MusicSkill
            return MusicSkill()
        if name == "weather":
            from brain.skills.weather.skill import WeatherSkill
            return WeatherSkill()
        if name == "system":
            from brain.skills.system.skill import SystemSkill
            return SystemSkill()
        if name == "cleanup":
            from brain.skills.system.cleanup import CleanupSkill
            return CleanupSkill()
        if name == "smalltalk":
            from brain.skills.smalltalk import SmallTalkSkill
            return SmallTalkSkill()
        if name == "controls":
            from brain.skills.controls import ControlsSkill
            return ControlsSkill()
        if name == "jellyfin":
            try:
                from brain.skills.jellyfin.skill import JellyfinSkill
                return JellyfinSkill()
            except Exception:
                return None
    except Exception as e:
        log(f"semantic: get_handler({name}): {e}", tag="BRAIN", level="WARN")
    return None