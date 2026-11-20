"""tests/test_music_player_duck.py — ducking в MusicManager."""
import time
from unittest.mock import MagicMock, call

import pytest
import vlc

from shared import config as cfg
from workers.music.player import MusicManager, FADE_DUCK_SEC, FADE_RESTORE_SEC

DEFAULT_DUCK = int(getattr(cfg, "DUCK_LEVEL", 30))


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
    t = getattr(m, "_fade_thread", None)
    if t is not None:
        t.join(timeout=2.0)


def test_duck_does_not_pause(mgr, player):
    mgr.duck()
    _wait_fade(mgr)
    player.pause.assert_not_called()


def test_duck_sets_volume_to_duck_level(mgr, player):
    mgr.duck()
    _wait_fade(mgr)
    calls = [c.args[0] for c in player.audio_set_volume.call_args_list]
    assert DEFAULT_DUCK in calls
    assert calls[-1] == DEFAULT_DUCK


def test_duck_custom_target(mgr, player):
    mgr.duck(35)
    _wait_fade(mgr)
    assert player.audio_set_volume.call_args_list[-1] == call(35)


def test_duck_clamps_target(mgr, player):
    player.audio_get_volume.return_value = 50

    mgr.duck(150)
    _wait_fade(mgr)
    assert player.audio_set_volume.call_args_list[-1] == call(100)

    mgr.restore()
    _wait_fade(mgr)
    assert mgr.ducked is False

    player.audio_get_volume.return_value = 100

    mgr.duck(-10)
    _wait_fade(mgr)
    assert player.audio_set_volume.call_args_list[-1] == call(0)


def test_duck_no_player_returns_true_and_sets_intent():
    m = MusicManager()
    m.player = None
    m._ready = False
    m.volume = 100
    assert m.duck() is True
    assert m.ducked is True
    assert m._pre_duck_volume == 100


def test_duck_not_ready_returns_true_and_sets_intent(mgr):
    mgr._ready = False
    assert mgr.duck() is True
    assert mgr.ducked is True


def test_set_player_when_ducked_creates_player_at_duck_level():
    m = MusicManager()
    m.volume = 100
    m.duck()
    assert m.ducked is True

    new_player = MagicMock()
    m.set_player(new_player)
    new_player.audio_set_volume.assert_called_once_with(DEFAULT_DUCK)


def test_mark_ready_applies_duck_level():
    m = MusicManager()
    m.volume = 100
    m.duck()

    new_player = MagicMock()
    m.player = new_player
    m._ready = False

    m.mark_ready()
    new_player.audio_set_volume.assert_called_with(DEFAULT_DUCK)


def test_restore_returns_user_volume(mgr, player):
    mgr.volume = 50
    mgr.duck()
    _wait_fade(mgr)
    assert mgr.ducked is True

    player.audio_get_volume.return_value = DEFAULT_DUCK
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
    mgr.volume = 50
    mgr.duck()
    _wait_fade(mgr)
    assert mgr.ducked is True

    mgr.volume = 70
    player.audio_get_volume.return_value = DEFAULT_DUCK
    player.audio_set_volume.reset_mock()

    mgr.restore()
    _wait_fade(mgr)

    calls = [c.args[0] for c in player.audio_set_volume.call_args_list]
    assert calls[-1] == 70


def test_hold_blocks_restore_for_non_tts_duck(mgr, player):
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
    mgr.hold_duck()
    player.audio_get_volume.return_value = DEFAULT_DUCK
    mgr.duck()
    _wait_fade(mgr)
    assert mgr.ducked is True

    player.audio_set_volume.reset_mock()
    player.audio_get_volume.return_value = DEFAULT_DUCK

    mgr.restore()
    _wait_fade(mgr)

    assert mgr.ducked is False
    calls = [c.args[0] for c in player.audio_set_volume.call_args_list]
    assert calls[-1] == 100


def test_release_duck_does_not_restore_tts(mgr, player):
    mgr.hold_duck()
    mgr.duck()
    _wait_fade(mgr)
    assert mgr.ducked is True

    player.audio_set_volume.reset_mock()
    mgr.release_duck()

    assert mgr.ducked is True
    assert mgr.duck_hold is False


def test_mark_ready_clears_stale_hold():
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


def test_set_volume_while_ducked_does_not_touch_player(mgr, player):
    mgr.duck()
    _wait_fade(mgr)
    assert mgr.ducked is True

    player.audio_set_volume.reset_mock()
    mgr.set_volume(70)
    player.audio_set_volume.assert_not_called()
    assert mgr.volume == 70


def test_watchdog_restores_stale_duck(mgr, player):
    mgr.duck()
    _wait_fade(mgr)
    assert mgr.ducked is True

    mgr._duck_started_at = time.monotonic() - 100
    player.audio_get_volume.return_value = DEFAULT_DUCK
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


def test_fade_does_not_block_lock(mgr):
    start = time.monotonic()
    mgr.duck()
    elapsed = time.monotonic() - start
    assert elapsed < FADE_DUCK_SEC * 0.5
    _wait_fade(mgr)


def test_duck_uses_short_fade_restore_uses_long_fade(mgr, player):
    assert FADE_DUCK_SEC < FADE_RESTORE_SEC


def test_new_fade_cancels_previous(mgr, player):
    mgr.duck()
    t1 = mgr._fade_thread
    mgr.duck()
    t2 = mgr._fade_thread
    if t1 and t2:
        t1.join(timeout=2.0)
        t2.join(timeout=2.0)
    assert mgr.ducked is True


def test_resume_does_not_exit_ducking(mgr, player):
    mgr.duck()
    _wait_fade(mgr)
    assert mgr.ducked is True

    player.audio_set_volume.reset_mock()
    mgr.resume()

    calls = [c.args[0] for c in player.audio_set_volume.call_args_list]
    assert DEFAULT_DUCK in calls


def test_mark_user_paused_clears_flags(mgr):
    mgr.duck()
    mgr.hold_duck()
    mgr.mark_user_paused()
    assert mgr.ducked is False
    assert mgr.duck_hold is False
    assert mgr._ducked_by_tts is False