# brain/skills/music/_shared.py
# FIX (день 28, аудит 6.1): тонкая обёртка над shared/music_words.py.
# Раньше был дубль workers/music/_shared.py, они разъехались.
# Теперь один источник правды — shared/music_words.py.
#
# Реэкспорт: чтобы `from brain.skills.music._shared import STOP_WORDS`
# продолжало работать без правок в classify.py, skill.py.

from shared.music_words import (
    STOP_WORDS,
    STOP_PREFIXES,
    PLAY_TRIGGERS,
    NOISE_WORDS,
    ARTIST_HINTS,
    TRACK_HINTS,
    GENRE_ALIASES,
    GENRE_TAGS_MAP,
    _strip_stop_prefix,
    _clean_filename,
)

__all__ = [
    'STOP_WORDS',
    'STOP_PREFIXES',
    'PLAY_TRIGGERS',
    'NOISE_WORDS',
    'ARTIST_HINTS',
    'TRACK_HINTS',
    'GENRE_ALIASES',
    'GENRE_TAGS_MAP',
    '_strip_stop_prefix',
    '_clean_filename',
]