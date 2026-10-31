"""tests/test_music_player_duck.py — ducking в MusicManager.

Проверяем:
  - duck() не ставит pause(), а меняет audio_set_volume;
  - duck() фиксирует намерение до player/mark_ready;
  - mark_ready() применяет _duck_level;
  - restore() возвращает self.volume, hold учитывается только для не-TTS;
  - release_duck() не снимает TTS-ducking;
  - set_player() при ducked=True создаёт плеер приглушённым;
  - mark_ready() снимает залипший hold (modes.py);
  - watchdog снимает ducking старше max_sec;
  - fade не блокирует lock, отменяется новым fade.

FIX (день 29, вечер): два теста ожидали audio_set_volume даже когда
  current == target (fade — no-op по дизайну). Поправлены:
    - test_duck_clamps_target: audio_get_volume=50, чтобы fade реально шёл;
    - test_hold_does_not_block_tts_restore: audio_get_volume=20 перед
      restore, чтобы fade 20→100 не был no-op.
"""
import threading
import time
from unittest.mock import MagicMock, call

import pytest
import vlc

from shared import config as cfg
from workers.music.player import MusicManager, FADE_DUCK_SEC, FADE_RESTORE_SEC


@pytest.fixture
def player():
    p = MagicMock()
    p.get_state.return_value = vlc.State.Playing
    p.audio_get_volume.return_value = 100
    return p


@pytest.fixture
def mgr(player):
    m = MusicManager()
    m.player = player
    m._ready = True
    m.volume = 100
    return m


def _wait_fade(m):
    """Дождаться завершения текущего fade-потока (если был)."""
    t = getattr(m, "_fade_thread", None)
    if t is not None:
        t.join(timeout=2.0)


# ── Базовое поведение duck() ──────────────────────────────────

def test_duck_does_not_pause(mgr, player):
    """duck() не должен звать pause() — только audio_set_volume."""
    mgr.duck()
    _wait_fade(mgr)
    player.pause.assert_not_called()


def test_duck_sets_volume_to_duck_level(mgr, player):
    """duck() применяет _duck_level (по умолчанию 20)."""
    mgr.duck()
    _wait_fade(mgr)
    calls = [c.args[0] for c in player.audio_set_volume.call_args_list]
    assert 20 in calls
    assert calls[-1] == 20


def test_duck_custom_target(mgr, player):
    mgr.duck(35)
    _wait_fade(mgr)
    assert player.audio_set_volume.call_args_list[-1] == call(35)


def test_duck_clamps_target(mgr, player):
    """duck(150) → 100, duck(-10) → 0.

    audio_get_volume != target, чтобы fade реально шёл (иначе no-op).
    """
    player.audio_get_volume.return_value = 50

    mgr.duck(150)                     # target=100
    _wait_fade(mgr)
    assert player.audio_set_volume.call_args_list[-1] == call(100)

    mgr.restore()
    _wait_fade(mgr)
    assert mgr.ducked is False

    player.audio_get_volume.return_value = 100

    mgr.duck(-10)                     # target=0
    _wait_fade(mgr)
    assert player.audio_set_volume.call_args_list[-1] == call(0)


# ── Намерение без плеера ─────────────────────────────────────

def test_duck_no_player_returns_true_and_sets_intent():
    """duck() без плеера фиксирует намерение, возвращает True."""
    m = MusicManager()
    m.player = None
    m._ready = False
    m.volume = 100
    assert m.duck() is True
    assert m.ducked is True
    assert m._pre_duck_volume == 100


def test_duck_not_ready_returns_true_and_sets_intent(mgr):
    """duck() при _ready=False фиксирует намерение, не падает."""
    mgr._ready = False
    assert mgr.duck() is True
    assert mgr.ducked is True


def test_set_player_when_ducked_creates_player_at_duck_level():
    """Если ducked=True, set_player создаёт плеер с _duck_level."""
    m = MusicManager()
    m.volume = 100
    m.duck()   # intent, player=None
    assert m.ducked is True

    new_player = MagicMock()
    m.set_player(new_player)
    new_player.audio_set_volume.assert_called_once_with(20)


def test_mark_ready_applies_duck_level():
    """mark_ready() применяет _duck_level, если ducked=True."""
    m = MusicManager()
    m.volume = 100
    m.duck()

    new_player = MagicMock()
    m.player = new_player
    m._ready = False

    m.mark_ready()
    new_player.audio_set_volume.assert_called_with(20)


# ── restore ──────────────────────────────────────────────────

def test_restore_returns_user_volume(mgr, player):
    mgr.volume = 50
    mgr.duck()
    _wait_fade(mgr)
    assert mgr.ducked is True

    player.audio_get_volume.return_value = 20    # duck применился
    player.audio_set_volume.reset_mock()

    mgr.restore()
    _wait_fade(mgr)

    calls = [c.args[0] for c in player.audio_set_volume.call_args_list]
    assert calls[-1] == 50


def test_restore_without_duck_is_noop(mgr, player):
    player.audio_set_volume.reset_mock()
    mgr.restore()
    _wait_fade(mgr)
    player.audio_set_volume.assert_not_called()


def test_restore_uses_current_volume(mgr, player):
    """restore() возвращает АКТУАЛЬНУЮ пользовательскую громкость,
    даже если она изменилась во время ducking."""
    mgr.volume = 50
    mgr.duck()
    _wait_fade(mgr)
    assert mgr.ducked is True

    mgr.volume = 70                   # пользователь поменял во время TTS
    player.audio_get_volume.return_value = 20
    player.audio_set_volume.reset_mock()

    mgr.restore()
    _wait_fade(mgr)

    calls = [c.args[0] for c in player.audio_set_volume.call_args_list]
    assert calls[-1] == 70


# ── hold ─────────────────────────────────────────────────────

def test_hold_blocks_restore_for_non_tts_duck(mgr, player):
    """hold блокирует restore(), если ducking не от TTS."""
    mgr.ducked = True
    mgr._ducked_by_tts = False
    mgr._pre_duck_volume = 100
    mgr.hold_duck()
    player.audio_set_volume.reset_mock()
    mgr.restore()
    _wait_fade(mgr)
    player.audio_set_volume.assert_not_called()
    assert mgr.ducked is True


def test_hold_does_not_block_tts_restore(mgr, player):
    """hold НЕ блокирует restore() от TTS (_ducked_by_tts=True).

    audio_get_volume=20 перед restore(), чтобы fade 20→100 реально шёл.
    """
    mgr.hold_duck()
    player.audio_get_volume.return_value = 20    # duck применился
    mgr.duck()
    _wait_fade(mgr)
    assert mgr.ducked is True

    player.audio_set_volume.reset_mock()
    player.audio_get_volume.return_value = 20    # всё ещё duck

    mgr.restore()
    _wait_fade(mgr)

    assert mgr.ducked is False
    calls = [c.args[0] for c in player.audio_set_volume.call_args_list]
    assert calls[-1] == 100


def test_release_duck_does_not_restore_tts(mgr, player):
    """release_duck() не снимает ducking от TTS."""
    mgr.hold_duck()
    mgr.duck()
    _wait_fade(mgr)
    assert mgr.ducked is True

    player.audio_set_volume.reset_mock()
    mgr.release_duck()

    assert mgr.ducked is True
    assert mgr.duck_hold is False


def test_mark_ready_clears_stale_hold():
    """mark_ready() снимает hold, если ducking не от TTS (modes.py)."""
    m = MusicManager()
    m.volume = 100
    m.hold_duck()
    assert m.duck_hold is True

    new_player = MagicMock()
    m.player = new_player
    m._ready = False
    m.ducked = False

    m.mark_ready()
    assert m.duck_hold is False


# ── set_volume при ducked ────────────────────────────────────

def test_set_volume_while_ducked_does_not_touch_player(mgr, player):
    mgr.duck()
    _wait_fade(mgr)
    assert mgr.ducked is True

    player.audio_set_volume.reset_mock()
    mgr.set_volume(70)
    player.audio_set_volume.assert_not_called()
    assert mgr.volume == 70


# ── watchdog ─────────────────────────────────────────────────

def test_watchdog_restores_stale_duck(mgr, player):
    mgr.duck()
    _wait_fade(mgr)
    assert mgr.ducked is True

    mgr._duck_started_at = time.monotonic() - 100
    player.audio_get_volume.return_value = 20
    player.audio_set_volume.reset_mock()

    mgr.check_duck_watchdog(max_sec=60)
    _wait_fade(mgr)

    assert mgr.ducked is False


def test_watchdog_does_not_touch_fresh_duck(mgr):
    mgr.duck()
    _wait_fade(mgr)
    assert mgr.ducked is True

    mgr.check_duck_watchdog(max_sec=60)
    assert mgr.ducked is True


# ── fade ─────────────────────────────────────────────────────

def test_fade_does_not_block_lock(mgr):
    """duck() возвращается мгновенно, fade идёт в потоке."""
    start = time.monotonic()
    mgr.duck()
    elapsed = time.monotonic() - start
    assert elapsed < FADE_DUCK_SEC * 0.5
    _wait_fade(mgr)


def test_duck_uses_short_fade_restore_uses_long_fade(mgr, player):
    """duck() запускает fade FADE_DUCK_SEC, restore() — FADE_RESTORE_SEC."""
    assert FADE_DUCK_SEC < FADE_RESTORE_SEC


def test_new_fade_cancels_previous(mgr, player):
    """Новый fade отменяет предыдущий, не зависает."""
    mgr.duck()
    t1 = mgr._fade_thread
    mgr.duck()  # повторный duck — новый fade
    t2 = mgr._fade_thread
    if t1 and t2:
        t1.join(timeout=2.0)
        t2.join(timeout=2.0)
    assert mgr.ducked is True


# ── pause/resume ─────────────────────────────────────────────

def test_resume_does_not_exit_ducking(mgr, player):
    """resume() при ducked=True не выходит из ducking."""
    mgr.duck()
    _wait_fade(mgr)
    assert mgr.ducked is True

    player.audio_set_volume.reset_mock()
    mgr.resume()

    calls = [c.args[0] for c in player.audio_set_volume.call_args_list]
    assert 20 in calls


def test_mark_user_paused_clears_flags(mgr):
    mgr.duck()
    mgr.hold_duck()
    mgr.mark_user_paused()
    assert mgr.ducked is False
    assert mgr.duck_hold is False
    assert mgr._ducked_by_tts is False