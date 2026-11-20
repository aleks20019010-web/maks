# tests/test_entity_music.py
# Контракт MusicEntityExtractor (изолирован от локального индекса треков и aliases.json).

import pytest

from brain.nlu.entity import music as entity_music
from brain.nlu.entity.music import get
from brain.nlu.types import EntityState, Intent


FIXED_ALIASES = {
    "ария": {"artist_id": 164354, "artist_name": "Ария"},
    "арию": {"artist_id": 164354, "artist_name": "Ария"},
    "кино": {"artist_id": 41075, "artist_name": "КИНО"},
    "киш": {"artist_id": 41052, "artist_name": "Король и Шут"},
}
FIXED_GENRES = {
    "рок": "genre:rusrock",
    "поп": "genre:pop",
    "джаз": "genre:jazz",
}


@pytest.fixture(autouse=True)
def _fixed_catalogs(monkeypatch):
    """Тесты не зависят от config/aliases.json и local_index.json."""
    from workers.music import local as music_local
    monkeypatch.setattr(entity_music, "_aliases_cache", FIXED_ALIASES)
    monkeypatch.setattr(entity_music, "_genres_cache", FIXED_GENRES)
    monkeypatch.setattr(music_local, "_index_cache", {"tracks": []})
    if hasattr(entity_music, "_local_artists_cache"):
        monkeypatch.setattr(entity_music, "_local_artists_cache", set())
    if hasattr(entity_music, "search_local"):
        monkeypatch.setattr(entity_music, "search_local", lambda q: None)
    if hasattr(entity_music, "_search_local_track"):
        monkeypatch.setattr(entity_music, "_search_local_track", lambda q: None)


def test_extract_absent_for_vklyuchi_muzyku():
    assert get().extract_candidate("включи музыку", Intent.MUSIC_PLAY) is None


def test_extract_candidate_for_orion():
    assert get().extract_candidate("включи орион", Intent.MUSIC_PLAY) == "орион"


def test_extract_candidate_strips_artist_hint():
    assert get().extract_candidate("включи группу ария", Intent.MUSIC_PLAY) == "ария"


def test_extract_candidate_strips_track_hint():
    assert get().extract_candidate("включи песню кино", Intent.MUSIC_PLAY) == "кино"


def test_extract_candidate_for_mood():
    assert get().extract_candidate("включи бодрое", Intent.MUSIC_PLAY) == "бодрое"


def test_extract_candidate_wave_returns_none():
    assert get().extract_candidate("включи мою волну", Intent.MUSIC_WAVE) is None


def test_match_unknown_artist_resolves_to_search():
    entity = get().match("квантумвейв", Intent.MUSIC_PLAY)
    assert entity.state in (EntityState.FOUND, EntityState.UNRESOLVED)
    assert "квантумвейв" in entity.value.lower()


def test_match_found_for_ariya():
    entity = get().match("ария", Intent.MUSIC_PLAY)
    assert entity.state == EntityState.FOUND
    assert entity.value == "Ария"
    assert entity.type == "artist"


def test_match_found_for_ariya_case():
    entity = get().match("арию", Intent.MUSIC_PLAY)
    assert entity.state == EntityState.FOUND
    assert entity.value == "Ария"
    assert entity.type == "artist"


def test_match_found_for_genre_rock():
    entity = get().match("рок", Intent.MUSIC_PLAY)
    assert entity.state == EntityState.FOUND
    assert entity.type == "genre"


def test_match_found_for_mood_bodroe():
    entity = get().match("бодрое", Intent.MUSIC_PLAY)
    assert entity.state == EntityState.FOUND
    assert entity.type == "mood"
    assert entity.value == "Энергичное"


def test_match_found_for_mood_bodraya_case():
    entity = get().match("бодрая", Intent.MUSIC_PLAY)
    assert entity.state == EntityState.FOUND
    assert entity.type == "mood"
    assert entity.value == "Энергичное"


def test_match_found_for_mood_veseloe():
    entity = get().match("весёлое", Intent.MUSIC_PLAY)
    assert entity.state == EntityState.FOUND
    assert entity.type == "mood"
    assert entity.value == "Радостная музыка"


def test_match_found_for_mood_grustnoe():
    entity = get().match("грустное", Intent.MUSIC_PLAY)
    assert entity.state == EntityState.FOUND
    assert entity.type == "mood"
    assert entity.value == "Грустная музыка"


def test_mood_takes_priority_over_artist():
    entity = get().match("бодрое", Intent.MUSIC_PLAY)
    assert entity.type == "mood"


def test_match_ambiguous_or_found_for_kino():
    entity = get().match("кино", Intent.MUSIC_PLAY)
    assert entity.state in (EntityState.FOUND, EntityState.AMBIGUOUS)


def test_match_nonsense_resolves_to_search_or_unresolved():
    entity = get().match("абракадабра", Intent.MUSIC_PLAY)
    assert entity.state in (EntityState.FOUND, EntityState.UNRESOLVED)