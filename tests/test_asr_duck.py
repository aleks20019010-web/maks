"""tests/test_asr_duck.py — привязка ducking к микрофону в ASR.

Проверяем:
  - _duck_on() отправляет duck_on в music_inbox и ставит _duck_sent=True;
  - _duck_off() отправляет duck_off и сбрасывает _duck_sent;
  - _duck_on() идемпотентен (не шлёт дважды);
  - _duck_off() без _duck_on — no-op;
  - при ошибке ipc.put _duck_sent откатывается;
  - _duck_off() вызывается ПОСЛЕ mute-паузы (в main-цикле).
"""
from unittest.mock import MagicMock, call, patch

import pytest


@pytest.fixture
def asr(monkeypatch):
    """Импортируем asr.main с моками, сбрасываем _duck_sent."""
    import importlib
    import workers.asr.main as asr_main
    importlib.reload(asr_main)
    # сбрасываем глобал
    asr_main._duck_sent = False
    return asr_main


def test_duck_on_sends_message(asr):
    with patch.object(asr, "ipc") as mock_ipc:
        asr._duck_on()
        mock_ipc.put.assert_called_once()
        args = mock_ipc.put.call_args
        # protocol.MUSIC_INBOX, protocol.music_control("duck_on")
        msg = args.args[1]
        assert msg.type == "control"
        assert msg.name == "duck_on"
    assert asr._duck_sent is True


def test_duck_off_sends_message(asr):
    asr._duck_sent = True
    with patch.object(asr, "ipc") as mock_ipc:
        asr._duck_off()
        mock_ipc.put.assert_called_once()
        args = mock_ipc.put.call_args
        msg = args.args[1]
        assert msg.type == "control"
        assert msg.name == "duck_off"
    assert asr._duck_sent is False


def test_duck_on_idempotent(asr):
    with patch.object(asr, "ipc") as mock_ipc:
        asr._duck_on()
        asr._duck_on()
        asr._duck_on()
        assert mock_ipc.put.call_count == 1


def test_duck_off_without_duck_on_is_noop(asr):
    with patch.object(asr, "ipc") as mock_ipc:
        asr._duck_off()
        mock_ipc.put.assert_not_called()


def test_duck_off_idempotent(asr):
    asr._duck_sent = True
    with patch.object(asr, "ipc") as mock_ipc:
        asr._duck_off()
        asr._duck_off()
        assert mock_ipc.put.call_count == 1


def test_duck_on_error_rolls_back_flag(asr):
    with patch.object(asr, "ipc") as mock_ipc:
        mock_ipc.put.side_effect = Exception("ipc broken")
        asr._duck_on()
        # флаг откатился, следующий вызов попробует снова
        assert asr._duck_sent is False
        mock_ipc.put.side_effect = None
        asr._duck_on()
        mock_ipc.put.assert_called()


def test_duck_on_off_roundtrip(asr):
    with patch.object(asr, "ipc") as mock_ipc:
        asr._duck_on()
        asr._duck_off()
        names = [c.args[1].name for c in mock_ipc.put.call_args_list]
        assert names == ["duck_on", "duck_off"]