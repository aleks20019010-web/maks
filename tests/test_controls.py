# tests/test_controls.py
# Тесты brain/skills/controls.py: parse_volume + _require_current_track.
#
# A1 (день 29): «громкость X» → число. X — цифры ИЛИ слова.
# Vosk отдаёт числа СЛОВАМИ («пятьдесят»), не цифрами («50»).
# Только круглые: 0, 10, 20, ..., 100. «Сорок пять» — не делаем.
#
# A8 (день 29): next / prev / pause / resume при стопе → NO_TRACK_PLAYING.
# state.get_current() пуст → в music_inbox НЕ шлём, говорим фразу.

import pytest

from brain.skills import controls
from brain.skills.controls import parse_volume


# ── A1: parse_volume ──────────────────────────────────────────

def test_parse_volume_basic():
    """«громкость 50» → 50."""
    assert parse_volume("громкость 50") == 50


def test_parse_volume_zero_and_hundred():
    """Границы: 0 и 100."""
    assert parse_volume("громкость 0") == 0
    assert parse_volume("громкость 100") == 100


def test_parse_volume_on_pattern():
    """«громкость на 30» → 30. «звук 70» → 70."""
    assert parse_volume("громкость на 30") == 30
    assert parse_volume("звук 70") == 70


def test_parse_volume_out_of_range():
    """Вне диапазона 0–100 → None."""
    assert parse_volume("громкость 101") is None
    assert parse_volume("громкость 999") is None


def test_parse_volume_not_recognized():
    """Не распознано → None."""
    assert parse_volume("громкость") is None
    assert parse_volume("громкость на половину") is None
    assert parse_volume("включи музыку") is None
    assert parse_volume("") is None
    assert parse_volume(None) is None


def test_parse_volume_words():
    """A1 fix: Vosk отдаёт «пятьдесят», не «50»."""
    assert parse_volume("громкость пятьдесят") == 50
    assert parse_volume("громкость сто") == 100
    assert parse_volume("громкость ноль") == 0
    assert parse_volume("громкость на тридцать") == 30
    assert parse_volume("звук сорок") == 40


def test_parse_volume_words_not_round():
    """«Сорок пять» — не делаем (только круглые)."""
    assert parse_volume("громкость сорок пять") is None
    assert parse_volume("громкость пять") is None


# ── A8: _require_current_track ───────────────────────────────

@pytest.fixture
def no_current(monkeypatch):
    """state.get_current() → None. Фраза NO_TRACK_PLAYING — не в TTS."""
    from shared import state
    monkeypatch.setattr(state, "get_current", lambda: None)
    said = []
    monkeypatch.setattr(controls, "_respond_and_say",
                        lambda *a, **kw: said.append((a, kw)))
    return said


@pytest.fixture
def has_current(monkeypatch):
    """state.get_current() → трек. Фраза NO_TRACK_PLAYING — не в TTS."""
    from shared import state
    monkeypatch.setattr(state, "get_current",
                        lambda: {"artist": "Ария", "title": "Беспечный ангел"})
    said = []
    monkeypatch.setattr(controls, "_respond_and_say",
                        lambda *a, **kw: said.append((a, kw)))
    return said


@pytest.fixture
def music_inbox(monkeypatch):
    """Перехват ipc.put в MUSIC_INBOX."""
    from shared import ipc
    calls = []
    monkeypatch.setattr(ipc, "put",
                        lambda topic, msg: calls.append((topic, msg)))
    return calls


def test_next_no_track(no_current, music_inbox):
    """«дальше» при стопе → NO_TRACK_PLAYING, в music_inbox НЕ шлём."""
    skill = controls.ControlsSkill()
    skill.handle("дальше")
    # Фраза сказана
    assert len(no_current) == 1
    assert no_current[0][0][0] == "NO_TRACK_PLAYING"
    # В music_inbox ничего не ушло
    assert music_inbox == []


def test_pause_no_track(no_current, music_inbox):
    """«пауза» при стопе → NO_TRACK_PLAYING, в music_inbox НЕ шлём."""
    skill = controls.ControlsSkill()
    skill.handle("пауза")
    assert len(no_current) == 1
    assert no_current[0][0][0] == "NO_TRACK_PLAYING"
    assert music_inbox == []


def test_next_with_track(has_current, music_inbox, monkeypatch):
    """«дальше» при треке → в music_inbox уходит next.

    NEXT_TRACK — с вероятностью 15% (VOICE_RANDOM_CHANCE). Тест не должен
    зависеть от рандома: проверяем, что next ушёл в music_inbox,
    а NO_TRACK_PLAYING не сказан. NEXT_TRACK — опционально.
    """
    skill = controls.ControlsSkill()
    skill.handle("дальше")
    # NO_TRACK_PLAYING не сказан
    assert not any(a[0][0] == "NO_TRACK_PLAYING" for a in has_current), \
        f"NO_TRACK_PLAYING сказан при треке: {has_current}"
    # В music_inbox ушло next
    assert len(music_inbox) == 1
    topic, msg = music_inbox[0]
    assert topic == "music_inbox"
    assert msg.name == "next"


def test_pause_with_track(has_current, music_inbox):
    """«пауза» при треке → в music_inbox уходит pause + фраза PAUSE."""
    skill = controls.ControlsSkill()
    skill.handle("пауза")
    # PAUSE сказан (не NO_TRACK_PLAYING)
    assert len(has_current) == 1
    assert has_current[0][0][0] == "PAUSE"
    # В music_inbox ушло pause
    assert len(music_inbox) == 1
    topic, msg = music_inbox[0]
    assert topic == "music_inbox"
    assert msg.name == "pause"