# tests/test_music_words.py
# Тесты shared/music_words.py + обёрток brain/skills/music/_shared.py
# и workers/music/_shared.py.
#
# FIX (день 28, аудит 6.1): один shared/music_words.py, оба _shared.py —
# тонкие обёртки. Тесты проверяют, что:
#   1. shared/music_words грузит config (STOP_WORDS, PLAY_TRIGGERS, GENRE_ALIASES).
#   2. brain-обёртка реэкспортирует то же самое (не разъехалась).
#   3. workers-обёртка реэкспортирует то же самое.

from shared import music_words
from brain.skills.music import _shared as brain_shared
from workers.music import _shared as workers_shared


def test_stop_words_not_empty():
    assert len(music_words.STOP_WORDS) > 0
    assert "стоп" in music_words.STOP_WORDS


def test_play_triggers():
    assert "включи" in music_words.PLAY_TRIGGERS
    assert "поставь" in music_words.PLAY_TRIGGERS


def test_genre_aliases_loaded():
    assert len(music_words.GENRE_ALIASES) > 0
    # genre_aliases.json: {alias: station}. Проверяем, что station — строка.
    sample_key = next(iter(music_words.GENRE_ALIASES))
    assert isinstance(music_words.GENRE_ALIASES[sample_key], str)


def test_brain_wrapper_same_as_shared():
    """brain-обёртка не разъехалась с shared."""
    assert brain_shared.STOP_WORDS == music_words.STOP_WORDS
    assert brain_shared.PLAY_TRIGGERS == music_words.PLAY_TRIGGERS
    assert brain_shared.GENRE_ALIASES == music_words.GENRE_ALIASES
    # _strip_stop_prefix — функция, не значение. Проверяем поведение.
    assert brain_shared._strip_stop_prefix("макс включи музыку") == "включи музыку"


def test_workers_wrapper_same_as_shared():
    """workers-обёртка не разъехалась с shared."""
    assert workers_shared.STOP_WORDS == music_words.STOP_WORDS
    assert workers_shared.GENRE_TAGS_MAP == music_words.GENRE_TAGS_MAP
    assert workers_shared._strip_stop_prefix("макс включи музыку") == "включи музыку"