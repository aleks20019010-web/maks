# shared/music_words.py
# Один источник правды для «музыкальных слов».
#
# A2 (день 29): REFUSAL_WORDS — «нет», «не надо», «отмена». Если фраза —
# отказ, classify() не считает её музыкальной командой. Нужно, чтобы
# «нет» в ответ на «Включить что-нибудь потише?» не включало музыку.
#
# MOOD (новое): MOOD_ALIASES + resolve_mood + all_case_variants.
#   config/mood_aliases.json — {слово: tag_метатега}. «бодрое» → «Энергичное».
#   resolve_mood(text) — общая функция: и classify(), и entity/music.py
#   читают оттуда, чтобы не дублировать mood-знание.
#   all_case_variants — падежи («бодрая» → «бодрое», «арию» → «ария»).
#   Вынесено из entity/music.py в общий модуль.

import os
import re
import json

from shared import config as cfg


MUSIC_WORDS_FILE = os.path.join(cfg.BASE_DIR, 'config', 'music_words.json')
GENRE_ALIASES_FILE = os.path.join(cfg.BASE_DIR, 'config', 'genre_aliases.json')
GENRE_TAGS_FILE = os.path.join(cfg.BASE_DIR, 'config', 'genre_tags.json')
MOOD_ALIASES_FILE = os.path.join(cfg.BASE_DIR, 'config', 'mood_aliases.json')


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
    if not os.path.exists(MUSIC_WORDS_FILE):
        return set(), [], (), (), (), (), (), {}, set()
    try:
        with open(MUSIC_WORDS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        return set(), [], (), (), (), (), (), {}, set()
    return (
        set(data.get('stop_words', [])),
        list(data.get('stop_prefixes', [])),
        tuple(data.get('play_triggers', [])),
        tuple(data.get('noise_words', [])),
        tuple(data.get('artist_hints', [])),
        tuple(data.get('track_hints', [])),
        tuple(data.get('volume_patterns', [])),
        dict(data.get('number_words', {})),
        set(data.get('refusal_words', [])),
    )


(STOP_WORDS,
 STOP_PREFIXES,
 PLAY_TRIGGERS,
 NOISE_WORDS,
 ARTIST_HINTS,
 TRACK_HINTS,
 VOLUME_PATTERNS,
 NUMBER_WORDS,
 REFUSAL_WORDS) = _load_music_words()

GENRE_ALIASES = _load_json(GENRE_ALIASES_FILE)
GENRE_TAGS_MAP = _load_json(GENRE_TAGS_FILE)
MOOD_ALIASES = _load_json(MOOD_ALIASES_FILE)


# ── Падежи (общие для classify и entity/music) ───────────────
def _normalize_case(word: str) -> str:
    """Грубая нормализация русских падежей. Возвращает несколько вариантов."""
    if not word:
        return word
    w = word.lower().strip()
    variants = [w]
    # Винительный/родительный: «арию» → «ария», «киша» → «киш»
    if w.endswith("ю"):
        variants.append(w[:-1] + "я")
    if w.endswith("у"):
        variants.append(w[:-1] + "а")
        variants.append(w[:-1])
    # Родительный: «сплина» → «сплин», «арии» → «ария»
    if w.endswith("а"):
        variants.append(w[:-1])
    if w.endswith("и"):
        variants.append(w[:-1] + "я")
        variants.append(w[:-1] + "ь")
    # Творительный: «арией» → «ария»
    if w.endswith("ей"):
        variants.append(w[:-2] + "я")
    if w.endswith("ой"):
        variants.append(w[:-2] + "а")
    # Дубликаты
    seen = set()
    out = []
    for v in variants:
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out[0] if len(out) == 1 else out


def all_case_variants(word: str) -> list[str]:
    """Вернуть список вариантов (сам word + падежи)."""
    if not word:
        return []
    w = word.lower().strip()
    variants = [w]
    extra = _normalize_case(w)
    if isinstance(extra, list):
        variants.extend(extra)
    elif extra and extra != w:
        variants.append(extra)
    seen = set()
    out = []
    for v in variants:
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


# ── Mood (общий для classify и entity/music) ────────────────
def resolve_mood(text: str) -> str | None:
    """Вернуть tag метатега для mood или None.

    «бодрое» → «Энергичное», «бодрая» → «Энергичное»,
    «весёлое» → «Радостная музыка», «грустное» → «Грустная музыка».

    Точное совпадение + падежи. Не бросает.
    """
    if not text:
        return None
    t = text.lower().strip()
    if not t:
        return None

    # Точное
    if t in MOOD_ALIASES:
        return MOOD_ALIASES[t]

    # Падежи: «бодрая» → «бодрое»? Нет, «бодрая» → «бодрый»?
    # _normalize_case даёт «бодрая» → «бодрая» (не меняется),
    # но в mood_aliases есть «бодрая» → «Энергичное». Поэтому —
    # проверяем все варианты.
    for variant in all_case_variants(t):
        if variant in MOOD_ALIASES:
            return MOOD_ALIASES[variant]

    return None


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