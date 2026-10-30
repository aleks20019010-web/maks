# tests/test_pipeline.py
# Контракт NLUPipeline:
#   normalize → intent → entity → decision → Command
#
# Тесты с МОКОМ intent.resolve (не зависят от эмбеддингов/IndexedRouter).
# Pipeline тестируется как ОРКЕСТРАТОР, а не как эмбеддинги.
# Эмбеддинги — отдельно, вручную (intent.resolve), не в pytest.
#
# Фикстура _fixed_catalogs: тесты не зависят от config/aliases.json.
#   (aliases.json меняется в рантайме: remember_alias дописывает артистов.)

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
    """Тесты не зависят от config/aliases.json (он меняется в рантайме)."""
    monkeypatch.setattr(entity_music, "_aliases_cache", FIXED_ALIASES)
    monkeypatch.setattr(entity_music, "_genres_cache", FIXED_GENRES)


def _mock_intent(monkeypatch, intent: Intent, confidence: float = 0.9):
    """Подменить intent.resolve на фиксированный (без эмбеддингов)."""
    monkeypatch.setattr(
        intent_module, "resolve",
        lambda text: (intent, confidence),
    )


# ── MUSIC_PLAY + mood → EXECUTE(play_mood) ──────────────────
def test_pipeline_mood_execute_play_mood(monkeypatch):
    _mock_intent(monkeypatch, Intent.MUSIC_PLAY)
    ctx = NLUContext()
    d = nlu_pipeline.process("включи бодрое", context=ctx)
    assert d.action == DecisionAction.EXECUTE
    assert d.command.action == "play_mood"
    assert d.command.entity is not None
    assert d.command.entity.type == "mood"
    assert d.command.entity.value == "Энергичное"


# ── MUSIC_PLAY + artist → EXECUTE(play_artist) ──────────────
def test_pipeline_artist_execute_play_artist(monkeypatch):
    _mock_intent(monkeypatch, Intent.MUSIC_PLAY)
    ctx = NLUContext()
    d = nlu_pipeline.process("включи арию", context=ctx)
    assert d.action == DecisionAction.EXECUTE
    assert d.command.action == "play_artist"
    assert d.command.entity is not None
    assert d.command.entity.type == "artist"
    assert d.command.entity.value == "Ария"


# ── MUSIC_PLAY + genre → EXECUTE(play_genre) ────────────────
def test_pipeline_genre_execute_play_genre(monkeypatch):
    _mock_intent(monkeypatch, Intent.MUSIC_PLAY)
    ctx = NLUContext()
    d = nlu_pipeline.process("включи рок", context=ctx)
    assert d.action == DecisionAction.EXECUTE
    assert d.command.action == "play_genre"
    assert d.command.entity is not None
    assert d.command.entity.type == "genre"


# ── MUSIC_PLAY + ABSENT → EXECUTE(play_wave) ────────────────
def test_pipeline_absent_execute_play_wave(monkeypatch):
    _mock_intent(monkeypatch, Intent.MUSIC_PLAY)
    ctx = NLUContext()
    d = nlu_pipeline.process("включи музыку", context=ctx)
    assert d.action == DecisionAction.EXECUTE
    assert d.command.action == "play_wave"


# ── MUSIC_PLAY + UNRESOLVED → CLARIFY ───────────────────────
def test_pipeline_unresolved_clarify(monkeypatch):
    _mock_intent(monkeypatch, Intent.MUSIC_PLAY)
    ctx = NLUContext()
    d = nlu_pipeline.process("включи орион", context=ctx)
    assert d.action == DecisionAction.CLARIFY
    assert d.clarify is not None
    assert d.clarify.phrase_set == "MUSIC_CLARIFY_UNRESOLVED_ARTIST"


# ── UNKNOWN → FALLBACK ──────────────────────────────────────
def test_pipeline_unknown_fallback(monkeypatch):
    _mock_intent(monkeypatch, Intent.UNKNOWN, confidence=0.2)
    ctx = NLUContext()
    d = nlu_pipeline.process("абракадабра", context=ctx)
    assert d.action == DecisionAction.FALLBACK


# ── SMALLTALK → FALLBACK (нет decision_engine) ─────────────
def test_pipeline_smalltalk_fallback(monkeypatch):
    _mock_intent(monkeypatch, Intent.SMALLTALK)
    ctx = NLUContext()
    d = nlu_pipeline.process("привет", context=ctx)
    assert d.action == DecisionAction.FALLBACK


# ── MUSIC_WAVE → EXECUTE(play_wave) ────────────────────────
def test_pipeline_wave_execute_play_wave(monkeypatch):
    _mock_intent(monkeypatch, Intent.MUSIC_WAVE)
    ctx = NLUContext()
    d = nlu_pipeline.process("включи мою волну", context=ctx)
    assert d.action == DecisionAction.EXECUTE
    assert d.command.action == "play_wave"