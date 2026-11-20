# tests/test_decision_music.py
# Контракт MusicDecisionEngine:
#   FOUND(artist) → EXECUTE(play_artist)
#   FOUND(genre)  → EXECUTE(play_genre)
#   FOUND(mood)   → EXECUTE(play_mood)
#   FOUND(search) → EXECUTE(play_search)
#   ABSENT        → EXECUTE(play_wave)
#   UNRESOLVED    → EXECUTE(play_search) (умный fallback без переспроса)
#   AMBIGUOUS     → EXECUTE(play_search)

from brain.nlu.decision.music import get
from brain.nlu.types import (
    Command, DecisionAction, Entity, EntityCandidate, Intent,
)


def _command(intent=Intent.MUSIC_PLAY, entity=None) -> Command:
    return Command(
        intent=intent,
        entity=entity,
        skill_name=None,
        action=None,
        confidence=0.8,
        normalized_text="x",
        original_text="x",
    )


def test_found_artist_execute_play_artist():
    ent = Entity.found("artist", "Ария", 1.0, "alias")
    d = get().resolve(_command(entity=ent))
    assert d.action == DecisionAction.EXECUTE
    assert d.command.action == "play_artist"
    assert "Ария" in d.reason


def test_found_genre_execute_play_genre():
    ent = Entity.found("genre", "рок", 1.0, "genre_alias")
    d = get().resolve(_command(entity=ent))
    assert d.action == DecisionAction.EXECUTE
    assert d.command.action == "play_genre"


def test_found_mood_execute_play_mood():
    ent = Entity.found("mood", "Энергичное", 1.0, "mood_alias")
    d = get().resolve(_command(entity=ent))
    assert d.action == DecisionAction.EXECUTE
    assert d.command.action == "play_mood"


def test_absent_execute_play_wave():
    ent = Entity.absent()
    d = get().resolve(_command(entity=ent))
    assert d.action == DecisionAction.EXECUTE
    assert d.command.action == "play_wave"


def test_none_entity_execute_play_wave():
    d = get().resolve(_command(entity=None))
    assert d.action == DecisionAction.EXECUTE
    assert d.command.action == "play_wave"


def test_unresolved_goes_to_play_search():
    ent = Entity.unresolved("орион", 0.0)
    d = get().resolve(_command(entity=ent))
    assert d.action == DecisionAction.EXECUTE
    assert d.command.action == "play_search"


def test_ambiguous_goes_to_play_search():
    candidates = [
        EntityCandidate("artist", "Ария", 0.80, "fuzzy"),
        EntityCandidate("artist", "Кипелов", 0.78, "fuzzy"),
    ]
    ent = Entity.ambiguous(candidates)
    d = get().resolve(_command(entity=ent))
    assert d.action == DecisionAction.EXECUTE
    assert d.command.action == "play_search"


def test_music_wave_execute_play_wave():
    d = get().resolve(_command(intent=Intent.MUSIC_WAVE, entity=None))
    assert d.action == DecisionAction.EXECUTE
    assert d.command.action == "play_wave"


def test_found_unknown_type_fallback():
    ent = Entity.found("date", "2026-10-31", 1.0, "x")
    d = get().resolve(_command(entity=ent))
    assert d.action == DecisionAction.FALLBACK