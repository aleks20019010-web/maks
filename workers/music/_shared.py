# workers/music/_shared.py
# Общие константы и утилиты для пакета music.
# Словари из config/, _strip_stop_prefix, _clean_filename.

import os
import re
import json

from shared import config as cfg


MUSIC_WORDS_FILE = os.path.join(cfg.BASE_DIR, 'config', 'music_words.json')
GENRE_ALIASES_FILE = os.path.join(cfg.BASE_DIR, 'config', 'genre_aliases.json')
GENRE_TAGS_FILE = os.path.join(cfg.BASE_DIR, 'config', 'genre_tags.json')


def _load_json(path, default=None):
    if default is None:
        default = {}
    if not os.path.exists(path):
        print(f"⚠ Не найден {path}")
        return default
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"⚠ Ошибка чтения {path}: {e}")
        return default


def _load_music_words():
    """Вернуть: STOP_WORDS, STOP_PREFIXES, PLAY_TRIGGERS, NOISE_WORDS,
    ARTIST_HINTS, TRACK_HINTS."""
    if not os.path.exists(MUSIC_WORDS_FILE):
        return set(), [], (), (), (), ()
    try:
        with open(MUSIC_WORDS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        return set(), [], (), (), (), ()
    return (
        set(data.get('stop_words', [])),
        list(data.get('stop_prefixes', [])),
        tuple(data.get('play_triggers', [])),
        tuple(data.get('noise_words', [])),
        tuple(data.get('artist_hints', [])),
        tuple(data.get('track_hints', [])),
    )


(STOP_WORDS,
 STOP_PREFIXES,
 PLAY_TRIGGERS,
 NOISE_WORDS,
 ARTIST_HINTS,
 TRACK_HINTS) = _load_music_words()

GENRE_ALIASES = _load_json(GENRE_ALIASES_FILE)
GENRE_TAGS_MAP = _load_json(GENRE_TAGS_FILE)


def _strip_stop_prefix(query):
    q = query.lower().strip()
    changed = True
    while changed:
        changed = False
        for prefix in STOP_PREFIXES:
            if q.startswith(prefix):
                q = q[len(prefix):].strip(' ,.!?-')
                changed = True
                break
    return q


def _clean_filename(path):
    name = os.path.splitext(os.path.basename(path))[0]
    name = re.sub(r'^\d+[\s._-]*[-._]?\s*', '', name).strip()
    name = re.sub(r'^\d{4}\s*[-._]\s*', '', name).strip()
    return name if name else os.path.splitext(os.path.basename(path))[0]