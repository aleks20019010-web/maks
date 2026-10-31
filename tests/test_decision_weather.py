# tests/test_decision_weather.py
# Контракт WeatherDecisionEngine:
#   WEATHER_QUERY → EXECUTE(weather).

from brain.nlu.decision.weather import get
from brain.nlu.types import Command, DecisionAction, Intent


def _command(intent=Intent.WEATHER_QUERY) -> Command:
    return Command(
        intent=intent,
        entity=None,
        skill_name=None,
        action=None,
        confidence=0.8,
        normalized_text="погода",
        original_text="погода",
    )


def test_weather_query_execute_weather():
    d = get().resolve(_command())
    assert d.action == DecisionAction.EXECUTE
    assert d.command.action == "weather"


def test_weather_query_unsupported_intent_fallback():
    d = get().resolve(_command(intent=Intent.MUSIC_PLAY))
    assert d.action == DecisionAction.FALLBACK