"""tests/test_playback_no_release_duck.py — playback/radio не снимают ducking от TTS."""
from unittest.mock import patch


def test_play_index_does_not_call_release_duck():
    from workers.music import playback

    with patch.object(playback, "music") as mock_music:
        with patch.object(playback.state, "playlist_lock"):
            playback.state.playlist = []
            playback.state.playlist_index = 0
            playback.play_index(0)

        mock_music.release_duck.assert_not_called()


def test_play_next_does_not_call_release_duck():
    from workers.music import playback

    with patch.object(playback, "music") as mock_music:
        with patch.object(playback, "refill_one", return_value=False):
            with patch.object(playback.state, "playlist_lock"):
                playback.state.playlist = []
                playback.state.playlist_index = 0
                playback.state.last_next_time = 0
                playback.play_next(from_finish=False)

        mock_music.release_duck.assert_not_called()


def test_radio_start_does_not_call_release_duck():
    from workers.music import radio

    with patch.object(radio, "music") as mock_music:
        with patch.object(radio, "RADIO_STATIONS",
                          {"test": {"name": "Test", "url": "http://x"}}):
            with patch.object(radio, "memory"):
                with patch.object(radio.state, "playlist_lock"):
                    radio.state.playlist = []
                    radio.state.prefetched = {}
                    radio.start_radio("test")

        mock_music.release_duck.assert_not_called()