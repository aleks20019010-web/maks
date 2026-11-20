# tests/test_hot_words.py
# A2: hot words с контекстом.

import pytest

from shared import runtime_state as rt
from workers.asr.text_utils import CommandFilter, hot_word_appropriate
from brain.skills.music.classify import classify


# ── hot_word_appropriate + CommandFilter ───────────────────────

def test_yes_no_appropriate_with_pending(monkeypatch):
    """YES/NO при expecting_input → True."""
    monkeypatch.setattr(rt, "exists", lambda flag: flag == rt.FLAG_EXPECTING_INPUT)
    assert hot_word_appropriate("да") is True
    assert hot_word_appropriate("нет") is True
    assert hot_word_appropriate("ок") is True


def test_yes_no_not_appropriate_without_pending(monkeypatch):
    """YES/NO без expecting_input → False."""
    monkeypatch.setattr(rt, "exists", lambda flag: False)
    assert hot_word_appropriate("да") is False
    assert hot_word_appropriate("нет") is False
    assert hot_word_appropriate("ок") is False


def test_control_words_always_appropriate(monkeypatch):
    """Управление плеером → True всегда."""
    monkeypatch.setattr(rt, "exists", lambda flag: False)
    assert hot_word_appropriate("дальше") is True
    assert hot_word_appropriate("пауза") is True
    assert hot_word_appropriate("стоп") is True
    assert hot_word_appropriate("громче") is True


def test_volume_always_appropriate(monkeypatch):
    """«громкость X» → True всегда."""
    monkeypatch.setattr(rt, "exists", lambda flag: False)
    assert hot_word_appropriate("громкость 50") is True
    assert hot_word_appropriate("громкость пятьдесят") is True


def test_now_playing_not_command_word():
    """«что играет» → False (не hot word в CommandFilter)."""
    cf = CommandFilter()
    cf.update_hot_words({"дальше", "пауза", "стоп", "громче", "тише"})
    assert cf.is_command_word("что играет") is False
    assert cf.is_command_word("дальше") is True


# ── classify: REFUSAL_WORDS ───────────────────────────────────

def test_classify_refusal_none():
    """«нет», «не надо», «отмена» → None (не музыка)."""
    assert classify("нет") is None
    assert classify("не надо") is None
    assert classify("отмена") is None


def test_classify_not_refusal():
    """«включи арию» → play_search."""
    r = classify("включи арию")
    assert r is not None
    assert r["action"] == "play_search"


# ── router-страховка ──────────────────────────────────────────

def test_router_yes_no_without_pending(monkeypatch):
    """YES/NO без pending → router возвращает None (игнор)."""
    from brain import router, pending
    from shared.sources import CommandSource

    monkeypatch.setattr(pending, "get", lambda key: None)
    called = []
    monkeypatch.setattr(router, "_respond_and_say",
                        lambda *a, **kw: called.append(a))

    result = router.process_command("да", source=CommandSource.VOICE)
    assert result is None
    assert called == [], f"router сказал что-то: {called}"


def test_router_yes_with_pending(monkeypatch):
    """YES с pending → router обрабатывает pending (не игнор)."""
    from brain import router, pending
    from shared.sources import CommandSource

    class FakePending:
        data = {"kind": "music_offer"}

    monkeypatch.setattr(pending, "get",
                        lambda key: FakePending() if key == router.PENDING_ASK else None)
    called = []
    monkeypatch.setattr(router, "_respond_and_say",
                        lambda *a, **kw: called.append(a))
    monkeypatch.setattr(router, "_accept_pending",
                        lambda data: called.append(("accept", data)))
    monkeypatch.setattr(pending, "pop", lambda key: FakePending())

    router.process_command("да", source=CommandSource.VOICE)
    assert called, "router не обработал YES с pending"