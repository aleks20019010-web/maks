# tests/test_context.py
# Контракт NLUContext / PendingCommand:
#   TTL 60 сек, cancel, history.

import time
import pytest

from brain.nlu.context import (
    NLUContext, PendingCommand, DEFAULT_TTL_SEC,
)
from brain.nlu.types import Intent


def test_pending_not_expired_immediately():
    p = PendingCommand(intent=Intent.MUSIC_PLAY, ttl_sec=60.0)
    assert not p.is_expired()


def test_pending_expired_after_ttl():
    p = PendingCommand(intent=Intent.MUSIC_PLAY, ttl_sec=0.01)
    time.sleep(0.02)
    assert p.is_expired()


def test_get_pending_returns_none_when_expired():
    ctx = NLUContext()
    p = PendingCommand(intent=Intent.MUSIC_PLAY, ttl_sec=0.01)
    ctx.set_pending(p)
    time.sleep(0.02)
    assert ctx.get_pending() is None


def test_is_cancel():
    ctx = NLUContext()
    assert ctx.is_cancel("отмена") is True
    assert ctx.is_cancel("не надо") is True
    assert ctx.is_cancel("забудь") is True
    assert ctx.is_cancel("включи арию") is False


def test_add_history_limits():
    ctx = NLUContext()
    for i in range(30):
        ctx.add_history(Intent.MUSIC_PLAY, f"cmd{i}")
    assert len(ctx.history) == 20  # max_len=20