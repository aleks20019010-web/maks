# brain/skills/music/classify.py
# Классификация музыкальной команды.
# Только понимание: артист / трек / жанр / волна / лайки / радио / dislike / mood.
# Без поиска, без скачивания, без VLC.
#
# A2 (день 29): REFUSAL_WORDS — если фраза отказ («нет», «не надо»),
# classify() возвращает None. Иначе «нет» в ответ на «Включить потише?»
# классифицировался бы как play_wave (short query → artist).
#
# MOOD (новое): resolve_mood(q) из shared.music_words — ДО play_artist.
#   «включи бодрая» → play_mood("Энергичное"), а не play_artist("бодрая").
#   resolve_mood общий с entity/music.py — mood-знание одно, не дублируется.

from brain.skills.music._shared import (
    STOP_WORDS, PLAY_TRIGGERS, NOISE_WORDS, GENRE_ALIASES,
    ARTIST_HINTS, TRACK_HINTS, REFUSAL_WORDS,
    _strip_stop_prefix,
)
from shared.music_words import resolve_mood

SHORT_QUERY_WORDS = 2

DISLIKE_TRIGGERS = (
    "надоело",
    "убери трек",
    "убери песню",
    "больше не надо",
)


def _detect_intent(query: str):
    q_low = query.lower()
    force_artist = any(h in q_low for h in ARTIST_HINTS)
    force_track = any(h in q_low for h in TRACK_HINTS)

    cleaned = query
    for word in ARTIST_HINTS + TRACK_HINTS:
        cleaned = cleaned.replace(word, "")
    cleaned = " ".join(cleaned.split()).strip()
    return force_artist, force_track, cleaned


def _is_short_query(query: str) -> bool:
    return len(query.split()) <= SHORT_QUERY_WORDS


def classify(command: str) -> dict | None:
    if not command:
        return None

    t = command.lower().strip()

    # A2: отказ — не музыкальная команда
    if t in REFUSAL_WORDS:
        return None

    stripped = _strip_stop_prefix(t)
    if not stripped:
        return None
    t = stripped

    if t in STOP_WORDS:
        return None

    if any(w in t for w in DISLIKE_TRIGGERS):
        return {"action": "dislike"}

    if "мою музыку" in t or "мне нравится" in t:
        return {"action": "play_liked"}
    if t in ("включи музыку", "включи мою волну", "моя волна"):
        return {"action": "play_wave", "language": None}

    russian_triggers = ("русское", "русскую", "русская", "русский",
                        "по-русски", "по русски")
    if any(w in t for w in russian_triggers) and "радио" not in t:
        return {"action": "play_wave", "language": "russian"}

    try:
        from workers.music.radio import is_radio_query, resolve_radio
        if is_radio_query(t):
            station = resolve_radio(t)
            if station:
                return {"action": "play_radio", "station": station}
    except Exception:
        pass

    q = command
    for word in PLAY_TRIGGERS:
        q = q.replace(word, "")
    for word in NOISE_WORDS:
        q = q.replace(word, "")
    q = " ".join(q.split()).strip().lower()

    if q in GENRE_ALIASES:
        return {
            "action": "play_genre",
            "genre": q,
            "station": GENRE_ALIASES[q],
        }

    # MOOD: «бодрая» → «Энергичное», «бодрое» → «Энергичное».
    # ДО play_artist, чтобы «включи бодрая» не ушло в артиста.
    mood = resolve_mood(q)
    if mood:
        return {"action": "play_mood", "mood": mood}

    if any(w in t for w in PLAY_TRIGGERS) or _is_short_query(t):
        q2 = command
        for word in PLAY_TRIGGERS:
            q2 = q2.replace(word, "")
        force_artist, force_track, q2 = _detect_intent(q2)
        for word in NOISE_WORDS:
            q2 = q2.replace(word, "")
        q2 = " ".join(q2.split()).strip()
        if not q2:
            return None

        # MOOD ещё раз — после _detect_intent (на случай «бодрая» в q2)
        mood2 = resolve_mood(q2)
        if mood2:
            return {"action": "play_mood", "mood": mood2}

        if force_artist:
            return {"action": "play_artist", "artist": q2}
        if force_track:
            return {"action": "play_track", "track": q2}

        if _is_short_query(q2):
            return {"action": "play_artist", "artist": q2}
        else:
            return {"action": "play_track", "track": q2}

    return None