# tests/test_play_index.py
# Тесты play_index: счётчик попыток, пустой плейлист, сдаться после 3.

import pytest


class FakeArtist:
    def __init__(self, name: str):
        self.name = name


class FakeTrack:
    def __init__(self, title: str = "Fake", artist: str = "Fake Artist"):
        self.title = title
        self.artists = [FakeArtist(artist)]
        self.id = hash(f"{title}|{artist}")
        self.albums = []


class FakeMedia:
    def __init__(self, file_path=""):
        self.file_path = file_path
        self.released = False

    def release(self):
        self.released = True


class FakePlayer:
    def __init__(self, file_path=""):
        self.file_path = file_path
        self.media = None
        self.played = False
        self.stopped = False
        self.released = False

    def set_media(self, media):
        self.media = media
        self.file_path = getattr(media, "file_path", "")

    def audio_set_volume(self, vol):
        pass

    def play(self):
        self.played = True

    def stop(self):
        self.stopped = True

    def pause(self):
        pass

    def release(self):
        self.released = True

    def get_state(self):
        import vlc
        return vlc.State.Playing


class FakeInstance:
    def __init__(self, created_players):
        self._created_players = created_players

    def media_new(self, file_path):
        return FakeMedia(file_path)

    def media_player_new(self):
        p = FakePlayer()
        self._created_players.append(p)
        return p


@pytest.fixture
def clean_state(monkeypatch):
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

    with state.playlist_lock:
        state.playlist = []
        state.playlist_index = 0
        state.prefetched = {}


@pytest.fixture
def no_vlc(monkeypatch):
    from workers.music import playback

    created_players = []
    fake_inst = FakeInstance(created_players)

    monkeypatch.setattr(playback, "_get_music_instance", lambda: fake_inst)
    monkeypatch.setattr(playback.vlc, "MediaPlayer", lambda fp="": fake_inst.media_player_new())
    return created_players


@pytest.fixture
def no_tts(monkeypatch):
    from workers.music import playback
    monkeypatch.setattr(playback, "_tts_speaking", lambda: False)


def test_play_index_empty_playlist_returns_false(clean_state):
    from workers.music import playback
    assert playback.play_index(0) is False


def test_play_index_idx_out_of_range_returns_false(clean_state):
    from workers.music import playback

    with clean_state.playlist_lock:
        clean_state.playlist = [FakeTrack("t0")]
        clean_state.playlist_index = 0

    assert playback.play_index(5) is False


def test_play_index_all_broken_gives_up_after_3(
    clean_state, no_vlc, no_tts, monkeypatch,
):
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

    assert result is False
    assert len(calls) <= 3


def test_play_index_stops_at_3_not_full_playlist(
    clean_state, no_vlc, no_tts, monkeypatch,
):
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

    assert len(calls) <= 3


def test_play_index_second_track_plays_after_first_broken(
    clean_state, no_vlc, no_tts, monkeypatch,
):
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
        clean_state.playlist = [FakeTrack("bad"), FakeTrack("good")]
        clean_state.playlist_index = 0

    result = playback.play_index(0)

    assert result is True
    assert len(calls) == 2
    assert clean_state.playlist_index == 1
    assert clean_state.current_track_title is not None
    assert "good" in clean_state.current_track_title


def test_play_index_does_not_play_after_3_broken(
    clean_state, no_vlc, no_tts, monkeypatch,
):
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
        clean_state.playlist = [FakeTrack(f"bad{i}") for i in range(5)] + [FakeTrack("good")]
        clean_state.playlist_index = 0

    result = playback.play_index(0)

    assert result is False
    assert len(calls) == 3