# brain/skills/music/classify.py
# Классификация музыкальной команды.
# Только понимание: артист / трек / жанр / волна / лайки / радио / dislike.
# Без поиска, без скачивания, без VLC.

import os

from brain.skills.music._shared import (
    STOP_WORDS, PLAY_TRIGGERS, NOISE_WORDS, GENRE_ALIASES,
    ARTIST_HINTS, TRACK_HINTS,
    _strip_stop_prefix,
)

SHORT_QUERY_WORDS = 2

# Blacklist — «не предлагать больше в автовыборе»
DISLIKE_TRIGGERS = (
    "надоело",
    "убери трек",
    "убери песню",
    "больше не надо",
)


def _detect_intent(query: str):
    """Вернуть (force_artist, force_track, cleaned_query)."""
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
    """Классифицировать команду.

    Возвращает dict:
      {"action": "play_artist", "artist": "..."}
      {"action": "play_track", "track": "..."}
      {"action": "play_genre", "genre": "...", "station": "..."}
      {"action": "play_wave", "language": None|"russian"}
      {"action": "play_liked"}
      {"action": "play_radio", "station": "..."}
      {"action": "dislike"}  — blacklist текущего трека
      None — не музыкальная команда.
    """
    if not command:
        return None

    t = command.lower().strip()
    stripped = _strip_stop_prefix(t)
    if not stripped:
        return None
    t = stripped

    if t in STOP_WORDS:
        return None

    # ── Blacklist (проверяем ДО play-триггеров) ──────────
    if any(w in t for w in DISLIKE_TRIGGERS):
        return {"action": "dislike"}

    # ── Волна / лайки ─────────────────────────────────────
    if "мою музыку" in t or "мне нравится" in t:
        return {"action": "play_liked"}
    if t in ("включи музыку", "включи мою волну", "моя волна"):
        return {"action": "play_wave", "language": None}

    # ── Русская волна ─────────────────────────────────────
    russian_triggers = ("русское", "русскую", "русская", "русский",
                        "по-русски", "по русски")
    if any(w in t for w in russian_triggers) and "радио" not in t:
        return {"action": "play_wave", "language": "russian"}

    # ── Радио ─────────────────────────────────────────────
    try:
        from workers.music.radio import is_radio_query, resolve_radio
        if is_radio_query(t):
            station = resolve_radio(t)
            if station:
                return {"action": "play_radio", "station": station}
    except Exception:
        pass

    # ── Жанр ──────────────────────────────────────────────
    # Проверяем после очистки play-триггеров и noise
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

    # ── Play-команда (артист / трек) ──────────────────────
    if any(w in t for w in PLAY_TRIGGERS) or _is_short_query(t):
        # Чистим от play-триггеров и noise
        q2 = command
        for word in PLAY_TRIGGERS:
            q2 = q2.replace(word, "")
        force_artist, force_track, q2 = _detect_intent(q2)
        for word in NOISE_WORDS:
            q2 = q2.replace(word, "")
        q2 = " ".join(q2.split()).strip()
        if not q2:
            return None

        if force_artist:
            return {"action": "play_artist", "artist": q2}
        if force_track:
            return {"action": "play_track", "track": q2}

        # Развилка по длине
        if _is_short_query(q2):
            # Короткий — скорее артист. Music worker сам решит
            # (search_artist → fallback на search_track).
            return {"action": "play_artist", "artist": q2}
        else:
            # Длинный — скорее трек.
            return {"action": "play_track", "track": q2}

    return None