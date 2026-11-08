# tests/test_decision_system.py
from brain.nlu.types import Command, DecisionAction, Intent
from brain.nlu.decision.system import get as get_system_decision


def _cmd(intent, conf=0.95):
    return Command(
        intent=intent, entity=None, skill_name="system", action=None,
        confidence=conf, normalized_text="статус", original_text="статус",
    )


def test_system_status_execute():
    d = get_system_decision().resolve(_cmd(Intent.SYSTEM_STATUS))
    assert d.action == DecisionAction.EXECUTE
    assert d.command.action == "status"


def test_system_status_confidence_preserved():
    d = get_system_decision().resolve(_cmd(Intent.SYSTEM_STATUS, conf=0.87))
    assert d.confidence == 0.87


def test_system_unsupported_intent_fallback():
    d = get_system_decision().resolve(_cmd(Intent.MUSIC_PLAY))
    assert d.action == DecisionAction.FALLBACK


def test_system_none_command_fallback():
    d = get_system_decision().resolve(None)
    assert d.action == DecisionAction.FALLBACK