# tests/test_play_index.py
# Тесты play_index: счётчик попыток, пустой плейлист, сдаться после 3.
#
# БЕЗ реального VLC: monkeypatch на vlc.MediaPlayer → FakePlayer,
# download_track → None / список, find_local_by_track → None.
#
# FakeTrack — минимальный объект с .title, .artists, .id, .albums.
# Не object() — play_index читает .title и .artists, на object() упадёт
# с AttributeError (это правильно, но не то, что проверяет тест).

import pytest
from types import SimpleNamespace


# ── Фейковый трек ─────────────────────────────────────────────
class FakeArtist:
    def __init__(self, name: str):
        self.name = name


class FakeTrack:
    """Минимальный трек. Не LocalTrack — идёт по ветке «не локальный»."""

    def __init__(self, title: str = "Fake", artist: str = "Fake Artist"):
        self.title = title
        self.artists = [FakeArtist(artist)]
        self.id = hash(f"{title}|{artist}")
        self.albums = []


class FakePlayer:
    """Мок vlc.MediaPlayer. play() ничего не делает, set_volume — тоже."""

    def __init__(self, file_path=""):
        self.file_path = file_path
        self.played = False
        self.stopped = False
        self.released = False

    def audio_set_volume(self, vol):
        pass

    def play(self):
        self.played = True

    def stop(self):
        self.stopped = True

    def release(self):
        self.released = True

    def get_state(self):
        # Для monitor_player — не важно, тест его не запускает
        import vlc
        return vlc.State.Playing


# ── Фикстуры ──────────────────────────────────────────────────
@pytest.fixture
def clean_state(monkeypatch):
    """Сбросить state.playlist / playlist_index / prefetched перед тестом."""
    from workers.music import state

    with state.playlist_lock:
        state.playlist = []
        state.playlist_index = 0
        state.prefetched = {}
    with state.played_lock:
        state.played_track_ids.clear()
    with state.queued_lock:
        state.queued_track_ids.clear()

    yield state

    # cleanup после
    with state.playlist_lock:
        state.playlist = []
        state.playlist_index = 0
        state.prefetched = {}


@pytest.fixture
def no_vlc(monkeypatch):
    """Подменить vlc.MediaPlayer на FakePlayer, чтобы не трогать libvlc.dll."""
    from workers.music import playback

    created_players = []

    def _make_player(file_path):
        p = FakePlayer(file_path)
        created_players.append(p)
        return p

    monkeypatch.setattr(playback.vlc, "MediaPlayer", _make_player)
    return created_players


@pytest.fixture
def no_tts(monkeypatch):
    """TTS не говорит — play_index не ждёт 30 сек."""
    from workers.music import playback
    monkeypatch.setattr(playback, "_tts_speaking", lambda: False)


# ── Тесты ─────────────────────────────────────────────────────
def test_play_index_empty_playlist_returns_false(clean_state):
    """Пустой плейлист → False, без падений."""
    from workers.music import playback

    assert playback.play_index(0) is False


def test_play_index_idx_out_of_range_returns_false(clean_state):
    """idx >= plen → False (очередь кончилась, не «сдался»)."""
    from workers.music import playback

    with clean_state.playlist_lock:
        clean_state.playlist = [FakeTrack("t0")]
        clean_state.playlist_index = 0

    assert playback.play_index(5) is False


def test_play_index_all_broken_gives_up_after_3(
    clean_state, no_vlc, no_tts, monkeypatch,
):
    """5 битых треков → сдаться после 3 → False. download_track вызван ≤ 3."""
    from workers.music import playback

    calls = []

    def _download(track):
        calls.append(track)
        return None

    monkeypatch.setattr(playback, "download_track", _download)
    monkeypatch.setattr(playback, "find_local_by_track", lambda t: None)

    with clean_state.playlist_lock:
        clean_state.playlist = [FakeTrack(f"t{i}") for i in range(5)]
        clean_state.playlist_index = 0

    result = playback.play_index(0)

    assert result is False, f"ожидали False, получили {result!r}"
    assert len(calls) <= 3, f"download_track вызван {len(calls)} раз, ожидали ≤ 3"


def test_play_index_stops_at_3_not_full_playlist(
    clean_state, no_vlc, no_tts, monkeypatch,
):
    """10 битых треков → download_track вызван ≤ 3 (не 10)."""
    from workers.music import playback

    calls = []

    def _download(track):
        calls.append(track)
        return None

    monkeypatch.setattr(playback, "download_track", _download)
    monkeypatch.setattr(playback, "find_local_by_track", lambda t: None)

    with clean_state.playlist_lock:
        clean_state.playlist = [FakeTrack(f"t{i}") for i in range(10)]
        clean_state.playlist_index = 0

    playback.play_index(0)

    assert len(calls) <= 3, f"download_track вызван {len(calls)} раз, ожидали ≤ 3 (attempts=3)"


def test_play_index_second_track_plays_after_first_broken(
    clean_state, no_vlc, no_tts, monkeypatch,
):
    """1-й битый, 2-й рабочий → True, играет 2-й (idx=1), attempts=1."""
    from workers.music import playback

    calls = []
    good_file = "C:/fake/music/track.mp3"

    # Первый трек → download None (битый). Второй → download вернёт good_file.
    def _download(track):
        calls.append(track)
        if track.title == "good":
            return good_file
        return None

    monkeypatch.setattr(playback, "download_track", _download)
    monkeypatch.setattr(playback, "find_local_by_track", lambda t: None)
    # os.path.exists(good_file) → True (файла нет, но play_index проверяет exists)
    monkeypatch.setattr(playback.os.path, "exists", lambda p: p == good_file)

    with clean_state.playlist_lock:
        clean_state.playlist = [FakeTrack("bad"), FakeTrack("good")]
        clean_state.playlist_index = 0

    result = playback.play_index(0)

    assert result is True, f"ожидали True (2-й трек играет), получили {result!r}"
    assert len(calls) == 2, f"download_track вызван {len(calls)} раз, ожидали 2"
    assert clean_state.playlist_index == 1, f"playlist_index={clean_state.playlist_index}, ожидали 1"
    assert clean_state.current_track_title is not None
    assert "good" in clean_state.current_track_title


def test_play_index_does_not_play_after_3_broken(
    clean_state, no_vlc, no_tts, monkeypatch,
):
    """5 битых, потом рабочий → False. Не «допрыгиваем» до 6-го после 3."""
    from workers.music import playback

    calls = []
    good_file = "C:/fake/music/track.mp3"

    def _download(track):
        calls.append(track)
        if track.title == "good":
            return good_file
        return None

    monkeypatch.setattr(playback, "download_track", _download)
    monkeypatch.setattr(playback, "find_local_by_track", lambda t: None)
    monkeypatch.setattr(playback.os.path, "exists", lambda p: p == good_file)

    with clean_state.playlist_lock:
        # 5 битых + 1 рабочий
        clean_state.playlist = [FakeTrack(f"bad{i}") for i in range(5)] + [FakeTrack("good")]
        clean_state.playlist_index = 0

    result = playback.play_index(0)

    # 3 попытки — все битые (bad0, bad1, bad2). good (idx=5) не достигнут.
    assert result is False, f"ожидали False (сдались на 3-х), получили {result!r}"
    assert len(calls) == 3, f"download_track вызван {len(calls)} раз, ожидали 3"