# tests/test_pipeline.py
import pytest

from brain.nlu import pipeline as nlu_pipeline
from brain.nlu import intent as intent_module
from brain.nlu.entity import music as entity_music
from brain.nlu.context import NLUContext
from brain.nlu.types import DecisionAction, Intent


FIXED_ALIASES = {
    "ария": {"artist_id": 164354, "artist_name": "Ария"},
    "арию": {"artist_id": 164354, "artist_name": "Ария"},
}
FIXED_GENRES = {"рок": "genre:rusrock"}


@pytest.fixture(autouse=True)
def _fixed_catalogs(monkeypatch):
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


def _mock_intent(monkeypatch, intent: Intent, confidence: float = 0.9):
    monkeypatch.setattr(
        intent_module, "resolve",
        lambda text: (intent, confidence),
    )


def test_pipeline_mood_execute_play_mood(monkeypatch):
    _mock_intent(monkeypatch, Intent.MUSIC_PLAY)
    ctx = NLUContext()
    d = nlu_pipeline.process("включи бодрое", context=ctx)
    assert d.action == DecisionAction.EXECUTE
    assert d.command.action == "play_mood"
    assert d.command.entity is not None
    assert d.command.entity.type == "mood"
    assert d.command.entity.value == "Энергичное"


def test_pipeline_artist_execute_play_artist(monkeypatch):
    _mock_intent(monkeypatch, Intent.MUSIC_PLAY)
    ctx = NLUContext()
    d = nlu_pipeline.process("включи арию", context=ctx)
    assert d.action == DecisionAction.EXECUTE
    assert d.command.action == "play_artist"
    assert d.command.entity is not None
    assert d.command.entity.type == "artist"
    assert d.command.entity.value == "Ария"


def test_pipeline_genre_execute_play_genre(monkeypatch):
    _mock_intent(monkeypatch, Intent.MUSIC_PLAY)
    ctx = NLUContext()
    d = nlu_pipeline.process("включи рок", context=ctx)
    assert d.action == DecisionAction.EXECUTE
    assert d.command.action == "play_genre"
    assert d.command.entity is not None
    assert d.command.entity.type == "genre"


def test_pipeline_absent_execute_play_wave(monkeypatch):
    _mock_intent(monkeypatch, Intent.MUSIC_PLAY)
    ctx = NLUContext()
    d = nlu_pipeline.process("включи музыку", context=ctx)
    assert d.action == DecisionAction.EXECUTE
    assert d.command.action == "play_wave"


def test_pipeline_unknown_artist_goes_to_play_search(monkeypatch):
    _mock_intent(monkeypatch, Intent.MUSIC_PLAY)
    ctx = NLUContext()
    d = nlu_pipeline.process("включи орион", context=ctx)
    assert d.action == DecisionAction.EXECUTE
    assert d.command.action == "play_search"


def test_pipeline_unknown_fallback(monkeypatch):
    _mock_intent(monkeypatch, Intent.UNKNOWN, confidence=0.2)
    ctx = NLUContext()
    d = nlu_pipeline.process("абракадабра", context=ctx)
    assert d.action == DecisionAction.FALLBACK


def test_pipeline_smalltalk_fallback(monkeypatch):
    _mock_intent(monkeypatch, Intent.SMALLTALK)
    ctx = NLUContext()
    d = nlu_pipeline.process("привет", context=ctx)
    assert d.action == DecisionAction.FALLBACK


def test_pipeline_wave_execute_play_wave(monkeypatch):
    _mock_intent(monkeypatch, Intent.MUSIC_WAVE)
    ctx = NLUContext()
    d = nlu_pipeline.process("включи мою волну", context=ctx)
    assert d.action == DecisionAction.EXECUTE
    assert d.command.action == "play_wave"