# workers/music/_shared.py
# FIX (день 28, аудит 6.1): тонкая обёртка над shared/music_words.py.
# Раньше был дубль brain/skills/music/_shared.py, они разъехались:
# в brain-версии не было GENRE_TAGS_MAP, в этой — был. Две копии = баги.
# Теперь один источник правды.
#
# Реэкспорт: чтобы `from workers.music._shared import GENRE_TAGS_MAP`
# продолжало работать без правок в local.py, modes.py.

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