# tests/test_modes_duck.py
"""Парность hold_duck/release_duck в workers/music/modes.py."""

import threading
import pytest

from workers.music import modes, playback
from workers.music.player import music, MusicManager


@pytest.fixture
def duck_spy(monkeypatch):
    calls = []
    monkeypatch.setattr(music, "hold_duck", lambda *a, **k: calls.append("hold"))
    monkeypatch.setattr(music, "release_duck", lambda *a, **k: calls.append("release"))
    return calls


@pytest.fixture
def clean_state(monkeypatch):
    monkeypatch.setattr(modes, "_reset_source_state", lambda: None)
    monkeypatch.setattr(playback, "refill_one", lambda: True)
    if not hasattr(modes.state, "playlist_lock"):
        monkeypatch.setattr(modes.state, "playlist_lock", threading.RLock())
    return monkeypatch


# ── queue_wave ──────────────────────────────────────────────

def test_queue_wave_releases_on_success(duck_spy, clean_state):
    assert modes.queue_wave() is True
    assert duck_spy == ["hold", "release"]


def test_queue_wave_releases_when_refill_fails(duck_spy, clean_state):
    clean_state.setattr(playback, "refill_one", lambda: False)
    res = modes.queue_wave()
    assert isinstance(res, bool)
    assert duck_spy == ["hold", "release"]


def test_queue_wave_russian_releases_on_success(duck_spy, clean_state):
    assert modes.queue_wave(language="russian") is True
    assert duck_spy == ["hold", "release"]


def test_queue_wave_releases_on_exception(duck_spy, clean_state):
    def boom():
        raise RuntimeError("network down")
    clean_state.setattr(modes, "_reset_source_state", boom)
    with pytest.raises(RuntimeError):
        modes.queue_wave()
    assert duck_spy == ["hold", "release"]


# ── start_genre ─────────────────────────────────────────────

def test_start_genre_releases_on_success(duck_spy, clean_state):
    assert modes.start_genre(42, "rock") is True
    assert duck_spy == ["hold", "release"]


def test_start_genre_releases_when_refill_fails(duck_spy, clean_state):
    clean_state.setattr(playback, "refill_one", lambda: False)
    res = modes.start_genre(42, "rock")
    assert isinstance(res, bool)
    assert duck_spy == ["hold", "release"]


def test_start_genre_releases_on_exception(duck_spy, clean_state):
    def boom():
        raise RuntimeError("boom")
    clean_state.setattr(modes, "_reset_source_state", boom)
    with pytest.raises(RuntimeError):
        modes.start_genre(42, "rock")
    assert duck_spy == ["hold", "release"]


# ── queue_liked_tracks ──────────────────────────────────────

def test_queue_liked_releases_when_client_none(duck_spy, clean_state):
    clean_state.setattr(modes.state, "client", None)
    assert modes.queue_liked_tracks() is False
    assert duck_spy == ["hold", "release"]


def test_queue_liked_releases_when_likes_empty(duck_spy, clean_state):
    class FakeLiked:
        tracks = []

    class FakeClient:
        @staticmethod
        def users_likes_tracks():
            return FakeLiked()

    clean_state.setattr(modes.state, "client", FakeClient())
    assert modes.queue_liked_tracks() is False
    assert duck_spy == ["hold", "release"]


def test_queue_liked_releases_on_exception(duck_spy, clean_state):
    class FakeClient:
        @staticmethod
        def users_likes_tracks():
            raise RuntimeError("api 500")

    clean_state.setattr(modes.state, "client", FakeClient())
    assert modes.queue_liked_tracks() is False
    assert duck_spy == ["hold", "release"]


# ── player.check_duck_watchdog ──────────────────────────────

def test_watchdog_clears_stale_hold_without_duck():
    m = MusicManager()
    m.hold_duck()
    assert m.duck_hold is True
    m._hold_started_at -= 200.0
    m.check_duck_watchdog(max_sec=60.0)
    assert m.duck_hold is False


def test_watchdog_does_not_touch_fresh_hold():
    m = MusicManager()
    m.hold_duck()
    m.check_duck_watchdog(max_sec=60.0)
    assert m.duck_hold is True


def test_hold_started_at_reset_on_release():
    m = MusicManager()
    m.hold_duck()
    assert m._hold_started_at is not None
    m.release_duck()
    assert m._hold_started_at is None
    assert m.duck_hold is False