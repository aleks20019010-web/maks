# tests/test_appraisal.py
import pytest

from brain.appraisal.events import Event, KIND_SKIP
from brain.appraisal.history import History
from brain.appraisal.policy import Policy


def test_three_skips_reaction():
    h = History(window_sec=10)
    p = Policy(h)
    e = Event(kind=KIND_SKIP, artist='Ария')

    assert p.evaluate(e, now=100.0).reaction is False
    assert p.evaluate(e, now=101.0).reaction is False
    d = p.evaluate(e, now=102.0)
    assert d.reaction is True
    assert d.phrase_set == "APPRAISAL_SKIP_3X"
    assert d.artist == "Ария"


def test_cooldown():
    h = History(window_sec=10)
    p = Policy(h)
    e = Event(kind=KIND_SKIP, artist='Ария')

    # 3 скипа → реакция
    for i in range(3):
        p.evaluate(e, now=100.0 + i)

    # 4-й, 5-й, 6-й — внутри кулдауна
    assert p.evaluate(e, now=103.0).reaction is False
    assert p.evaluate(e, now=104.0).reaction is False
    assert p.evaluate(e, now=105.0).reaction is False

    # 7-й, 8-й, 9-й — после кулдауна (700 сек > 600)
    assert p.evaluate(e, now=806.0).reaction is False
    assert p.evaluate(e, now=807.0).reaction is False
    assert p.evaluate(e, now=808.0).reaction is True


def test_reset_after_reaction():
    h = History(window_sec=10)
    p = Policy(h)
    e = Event(kind=KIND_SKIP, artist='Ария')

    for i in range(3):
        p.evaluate(e, now=100.0 + i)

    h.reset(e.key())
    # Сбросить кулдаун (для теста) — иначе 10 минут ждать
    with p._lock:
        p._last_reaction.clear()

    assert p.evaluate(e, now=200.0).reaction is False
    assert p.evaluate(e, now=201.0).reaction is False
    assert p.evaluate(e, now=202.0).reaction is True


def test_different_artists():
    h = History(window_sec=10)
    p = Policy(h)

    e1 = Event(kind=KIND_SKIP, artist='Ария')
    e2 = Event(kind=KIND_SKIP, artist='КИНО')

    assert p.evaluate(e1, now=100.0).reaction is False
    assert p.evaluate(e2, now=101.0).reaction is False
    assert p.evaluate(e1, now=102.0).reaction is False
    assert p.evaluate(e2, now=103.0).reaction is False
    # Только на 3-м одного артиста
    assert p.evaluate(e1, now=104.0).reaction is True