"""tests/test_asr_duck.py — привязка ducking к микрофону в ASR (DuckingController)."""
from unittest.mock import patch

import pytest

from workers.asr.ducking import DuckingController


@pytest.fixture
def duck():
    return DuckingController()


def test_duck_on_sends_message(duck):
    with patch("workers.asr.ducking.ipc") as mock_ipc:
        duck.duck_on()
        mock_ipc.put.assert_called_once()
        args = mock_ipc.put.call_args
        msg = args.args[1]
        assert msg.type == "control"
        assert msg.name == "duck_on"
    assert duck._duck_sent is True


def test_duck_off_sends_message(duck):
    duck._duck_sent = True
    with patch("workers.asr.ducking.ipc") as mock_ipc:
        duck.duck_off()
        mock_ipc.put.assert_called_once()
        args = mock_ipc.put.call_args
        msg = args.args[1]
        assert msg.type == "control"
        assert msg.name == "duck_off"
    assert duck._duck_sent is False


def test_duck_on_idempotent(duck):
    with patch("workers.asr.ducking.ipc") as mock_ipc:
        duck.duck_on()
        duck.duck_on()
        duck.duck_on()
        assert mock_ipc.put.call_count == 1


def test_duck_off_without_duck_on_is_noop(duck):
    with patch("workers.asr.ducking.ipc") as mock_ipc:
        duck.duck_off()
        mock_ipc.put.assert_not_called()


def test_duck_off_idempotent(duck):
    duck._duck_sent = True
    with patch("workers.asr.ducking.ipc") as mock_ipc:
        duck.duck_off()
        duck.duck_off()
        assert mock_ipc.put.call_count == 1


def test_duck_on_error_rolls_back_flag(duck):
    with patch("workers.asr.ducking.ipc") as mock_ipc:
        mock_ipc.put.side_effect = Exception("ipc broken")
        duck.duck_on()
        assert duck._duck_sent is False
        mock_ipc.put.side_effect = None
        duck.duck_on()
        mock_ipc.put.assert_called()


def test_duck_on_off_roundtrip(duck):
    with patch("workers.asr.ducking.ipc") as mock_ipc:
        duck.duck_on()
        duck.duck_off()
        names = [c.args[1].name for c in mock_ipc.put.call_args_list]
        assert names == ["duck_on", "duck_off"]