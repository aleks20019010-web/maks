# tests/test_entity_music.py
# Контракт MusicEntityExtractor:
#   extract_candidate возвращает None ТОЛЬКО при ABSENT.
#   match возвращает FOUND / UNRESOLVED / AMBIGUOUS.
#
# MOOD (новое): resolve_mood из shared.music_words — общий с classify.
#   «бодрое» → FOUND(type="mood", value="Энергичное"), не UNRESOLVED.
#
# ФИКСТУРА (новое): тесты НЕ зависят от config/aliases.json.
#   Причина: aliases.json — кэш, который меняется в рантайме (remember_alias
#   дописывает то, что нашёл Яндекс). Без фикстуры тесты flaky: «ария» может
#   исчезнуть, «орион» — появиться. Фикстура подменяет _aliases_cache и
#   _genres_cache на фиксированные словари — тесты детерминированы.
#   mood_aliases.json НЕ меняется в рантайме, поэтому mood-тесты стабильны
#   и без мока.

import pytest

from brain.nlu.entity import music as entity_music
from brain.nlu.entity.music import get
from brain.nlu.types import EntityState, Intent


# ── Фиксированные каталоги (не зависят от config/aliases.json) ──
FIXED_ALIASES = {
    "ария": {"artist_id": 164354, "artist_name": "Ария"},
    "арию": {"artist_id": 164354, "artist_name": "Ария"},
    "кино": {"artist_id": 41075, "artist_name": "КИНО"},
    "киш": {"artist_id": 41052, "artist_name": "Король и Шут"},
}
FIXED_GENRES = {
    "рок": "genre:rusrock",
    "поп": "genre:pop",
}


@pytest.fixture(autouse=True)
def _fixed_catalogs(monkeypatch):
    """Тесты не зависят от config/aliases.json (он меняется в рантайме)."""
    monkeypatch.setattr(entity_music, "_aliases_cache", FIXED_ALIASES)
    monkeypatch.setattr(entity_music, "_genres_cache", FIXED_GENRES)


# ── extract_candidate ────────────────────────────────────────
def test_extract_absent_for_vklyuchi_muzyku():
    assert get().extract_candidate("включи музыку", Intent.MUSIC_PLAY) is None


def test_extract_candidate_for_orion():
    # «включи Орион» → "орион" (не None! пользователь назвал параметр)
    assert get().extract_candidate("включи орион", Intent.MUSIC_PLAY) == "орион"


def test_extract_candidate_strips_artist_hint():
    # «включи группу ария» → "ария" (вычли «группу»)
    assert get().extract_candidate("включи группу ария", Intent.MUSIC_PLAY) == "ария"


def test_extract_candidate_strips_track_hint():
    # «включи песню кино» → "кино" (вычли «песню»)
    assert get().extract_candidate("включи песню кино", Intent.MUSIC_PLAY) == "кино"


def test_extract_candidate_for_mood():
    # «включи бодрое» → "бодрое" (не None — пользователь назвал параметр)
    assert get().extract_candidate("включи бодрое", Intent.MUSIC_PLAY) == "бодрое"


def test_extract_candidate_wave_returns_none():
    # MUSIC_WAVE — параметра нет по определению
    assert get().extract_candidate("включи мою волну", Intent.MUSIC_WAVE) is None


# ── match: artist ────────────────────────────────────────────
def test_match_unresolved_for_unknown_artist():
    entity = get().match("орион", Intent.MUSIC_PLAY)
    assert entity.state == EntityState.UNRESOLVED
    assert entity.value == "орион"


def test_match_found_for_ariya():
    entity = get().match("ария", Intent.MUSIC_PLAY)
    assert entity.state == EntityState.FOUND
    assert entity.value == "Ария"
    assert entity.type == "artist"


def test_match_found_for_ariya_case():
    # «арию» → FOUND(artist, "Ария") через падежи
    entity = get().match("арию", Intent.MUSIC_PLAY)
    assert entity.state == EntityState.FOUND
    assert entity.value == "Ария"
    assert entity.type == "artist"


# ── match: genre ─────────────────────────────────────────────
def test_match_found_for_genre_rock():
    entity = get().match("рок", Intent.MUSIC_PLAY)
    assert entity.state == EntityState.FOUND
    assert entity.type == "genre"


# ── match: mood ──────────────────────────────────────────────
def test_match_found_for_mood_bodroe():
    # «бодрое» → FOUND(type="mood", value="Энергичное")
    entity = get().match("бодрое", Intent.MUSIC_PLAY)
    assert entity.state == EntityState.FOUND
    assert entity.type == "mood"
    assert entity.value == "Энергичное"


def test_match_found_for_mood_bodraya_case():
    # «бодрая» (Vosk) → FOUND(type="mood", value="Энергичное")
    entity = get().match("бодрая", Intent.MUSIC_PLAY)
    assert entity.state == EntityState.FOUND
    assert entity.type == "mood"
    assert entity.value == "Энергичное"


def test_match_found_for_mood_veseloe():
    # «весёлое» → FOUND(type="mood", value="Радостная музыка")
    entity = get().match("весёлое", Intent.MUSIC_PLAY)
    assert entity.state == EntityState.FOUND
    assert entity.type == "mood"
    assert entity.value == "Радостная музыка"


def test_match_found_for_mood_grustnoe():
    # «грустное» → FOUND(type="mood", value="Грустная музыка")
    entity = get().match("грустное", Intent.MUSIC_PLAY)
    assert entity.state == EntityState.FOUND
    assert entity.type == "mood"
    assert entity.value == "Грустная музыка"


def test_mood_takes_priority_over_artist():
    # «бодрое» не должно уйти в artist, даже если fuzzy найдёт артиста
    entity = get().match("бодрое", Intent.MUSIC_PLAY)
    assert entity.type == "mood"


# ── match: AMBIGUOUS / не музыкант ──────────────────────────
def test_match_ambiguous_or_found_for_kino():
    # «кино» есть в FIXED_ALIASES. Может быть FOUND (один лидер)
    # или AMBIGUOUS (близкие кандидаты). Главное — НЕ UNRESOLVED.
    entity = get().match("кино", Intent.MUSIC_PLAY)
    assert entity.state in (EntityState.FOUND, EntityState.AMBIGUOUS)


def test_match_unresolved_for_nonsense():
    # «абракадабра» — нет в FIXED_ALIASES → UNRESOLVED.
    # (В рабочем aliases.json может быть FOUND, но тест изолирован.)
    entity = get().match("абракадабра", Intent.MUSIC_PLAY)
    assert entity.state == EntityState.UNRESOLVED