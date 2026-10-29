# tests/test_hot_words.py
# A2: hot words с контекстом.
#
# Проверяем:
#   - _hot_word_appropriate: YES/NO при expecting_input → True
#   - _hot_word_appropriate: YES/NO без expecting_input → False
#   - _hot_word_appropriate: next/prev/pause/stop → True (всегда)
#   - _hot_word_appropriate: «громкость X» → True (всегда)
#   - _hot_word_appropriate: «что играет» → False (не hot word)
#   - classify: «нет» → None (отказ, не музыка)
#   - router-страховка: YES/NO без pending → игнор

import pytest

from workers.asr.main import _hot_word_appropriate
from brain.skills.music.classify import classify


# ── _hot_word_appropriate ──────────────────────────────────────

def test_yes_no_appropriate_with_pending(monkeypatch):
    """YES/NO при expecting_input → True."""
    from workers.asr import main as asr_main
    monkeypatch.setattr(asr_main, "_is_expecting_input", lambda: True)
    assert _hot_word_appropriate("да") is True
    assert _hot_word_appropriate("нет") is True
    assert _hot_word_appropriate("ок") is True


def test_yes_no_not_appropriate_without_pending(monkeypatch):
    """YES/NO без expecting_input → False."""
    from workers.asr import main as asr_main
    monkeypatch.setattr(asr_main, "_is_expecting_input", lambda: False)
    assert _hot_word_appropriate("да") is False
    assert _hot_word_appropriate("нет") is False
    assert _hot_word_appropriate("ок") is False


def test_control_words_always_appropriate(monkeypatch):
    """Управление плеером → True всегда."""
    from workers.asr import main as asr_main
    monkeypatch.setattr(asr_main, "_is_expecting_input", lambda: False)
    assert _hot_word_appropriate("дальше") is True
    assert _hot_word_appropriate("пауза") is True
    assert _hot_word_appropriate("стоп") is True
    assert _hot_word_appropriate("громче") is True


def test_volume_always_appropriate(monkeypatch):
    """«громкость X» → True всегда."""
    from workers.asr import main as asr_main
    monkeypatch.setattr(asr_main, "_is_expecting_input", lambda: False)
    assert _hot_word_appropriate("громкость 50") is True
    assert _hot_word_appropriate("громкость пятьдесят") is True


def test_now_playing_not_appropriate(monkeypatch):
    """«что играет» → False (не hot word)."""
    from workers.asr import main as asr_main
    monkeypatch.setattr(asr_main, "_is_expecting_input", lambda: False)
    assert _hot_word_appropriate("что играет") is False


# ── classify: REFUSAL_WORDS ───────────────────────────────────

def test_classify_refusal_none():
    """«нет», «не надо», «отмена» → None (не музыка)."""
    assert classify("нет") is None
    assert classify("не надо") is None
    assert classify("отмена") is None


def test_classify_not_refusal():
    """«включи арию» → play_artist."""
    r = classify("включи арию")
    assert r is not None
    assert r["action"] == "play_artist"


# ── router-страховка ──────────────────────────────────────────

def test_router_yes_no_without_pending(monkeypatch):
    """YES/NO без pending → router возвращает None (игнор)."""
    from brain import router, pending
    from shared.sources import CommandSource

    # Мокаем pending.get → None
    monkeypatch.setattr(pending, "get", lambda key: None)
    # Мокаем respond_and_say — не должен вызываться
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