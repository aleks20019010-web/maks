# workers/music/modes.py
# Режимы воспроизведения: артист, трек артиста, одиночный трек,
# лайки, волна, жанр.
#
# Music worker НЕ говорит. Brain скажет сам, до отправки в music_inbox.
#
# DUCKING (день 29, вечер): ducking привязан к микрофону ASR.
#   hold_duck/release_duck здесь — это «пока грузим данные, не восстанавливай
#   громкость». С TTS-ducking они не пересекаются (см. player.py:
#   restore() учитывает hold только при _ducked_by_tts=False,
#   release_duck() при _ducked_by_tts=True не вызывает restore()).
#   Прямых music.duck()/music.restore() тут нет и не должно быть —
#   ducking включает/снимает только ASR через mic_off/mic_on.
#
# FIX (2026-10-31): hold_duck/release_duck парны на ВСЕХ путях выхода
#   (try/finally). Раньше успешный return True оставлял duck_hold висеть
#   до mark_ready/watchdog. Теперь release_duck() вызывается в finally.

import threading
import time

from workers.music import state
from workers.music.player import music
from memory import memory
from workers.music.sources import (
    get_artist_tracks_page, get_next_unplayed_track_from_page,
)
from workers.music.search import find_artist_track
from workers.music.similar import fetch_similar_background
from workers.music.reset import _reset_source_state
from workers.music.playback import refill_one


def start_artist_only(artist):
    print(f"🎤 Режим: только {artist.name}")
    state.radio_artist_id = artist.id
    state.radio_artist_name = artist.name
    _reset_source_state()
    first_page = get_artist_tracks_page(artist.id, 0)
    first_track = get_next_unplayed_track_from_page(first_page)
    if not first_track:
        return False
    with state.played_lock:
        state.played_track_ids.add(first_track.id)
    with state.playlist_lock:
        state.playlist = [first_track]
        state.prefetched = {}
    state.playlist_index = 0
    state.playlist_mode = "artist_only"
    print(f"▶ Первый трек: {first_track.title}")
    return True


def start_artist_track(artist, track_query):
    print(f"🎤 Режим: трек «{track_query}» у {artist.name}")
    target_track = find_artist_track(artist, track_query)
    state.radio_artist_id = artist.id
    state.radio_artist_name = artist.name
    _reset_source_state()
    if target_track:
        with state.played_lock:
            state.played_track_ids.add(target_track.id)
        with state.playlist_lock:
            state.playlist = [target_track]
            state.prefetched = {}
    else:
        with state.playlist_lock:
            state.playlist = []
            state.prefetched = {}
        t = get_next_source_track_for_artist_start()
        if not t:
            return False
        with state.played_lock:
            state.played_track_ids.add(t.id)
        with state.playlist_lock:
            state.playlist = [t]
    state.playlist_index = 0
    threading.Thread(target=fetch_similar_background, args=(artist.id,), daemon=True).start()
    waited = 0
    while not state.radio_similar_list and waited < 10:
        time.sleep(0.5)
        waited += 1
    if state.radio_similar_list:
        state.playlist_mode = "similar"
        state.radio_similar_offset = 0
        state.radio_similar_page = 0
        state.radio_similar_exhausted = False
    else:
        state.playlist_mode = "single"
    return True


def get_next_source_track_for_artist_start():
    while True:
        page_tracks = get_artist_tracks_page(state.radio_artist_id, state.radio_artist_page)
        if not page_tracks:
            return None
        t = get_next_unplayed_track_from_page(page_tracks)
        if t:
            return t
        state.radio_artist_page += 1


def start_single_track(track):
    print(f"🎤 Режим: одиночный трек")
    with state.played_lock:
        state.played_track_ids.add(track.id)
    with state.playlist_lock:
        state.playlist = [track]
        state.prefetched = {}
    state.playlist_index = 0
    state.playlist_mode = "single"
    try:
        if track.artists:
            state.single_artist_id = track.artists[0].id
    except Exception:
        state.single_artist_id = None
    if state.single_artist_id:
        threading.Thread(target=fetch_similar_background,
                         args=(state.single_artist_id,), daemon=True).start()
    return True


def queue_liked_tracks():
    music.hold_duck()
    try:
        print("Загружаю 'Мне нравится'...")
        memory.remember_search(query="мою музыку", found_artist="Мне нравится", success=True)
        if state.client is None:
            return False
        try:
            liked = state.client.users_likes_tracks()
            if not liked or not liked.tracks:
                return False
            state.liked_short_tracks = liked.tracks
            state.liked_offset = 0
            _reset_source_state()
            with state.playlist_lock:
                state.playlist = []
                state.prefetched = {}
            state.playlist_index = 0
            state.playlist_mode = "liked"
            if not refill_one():
                return False
            return True
        except Exception as e:
            print(f"Ошибка: {e}")
            return False
    finally:
        music.release_duck()


def queue_wave(language=None):
    """Запустить «Мою волну». language=None — обычная, "russian" — русская."""
    music.hold_duck()
    try:
        if language == "russian":
            print("Загружаю 'Русскую волну'...")
            memory.remember_search(query="включи русское", found_artist="Русская волна", success=True)
        else:
            print("Загружаю 'Мою волну'...")
            memory.remember_search(query="включи музыку", found_artist="Моя волна", success=True)

        _reset_source_state()
        with state.playlist_lock:
            state.playlist = []
            state.prefetched = {}
        state.playlist_index = 0
        state.playlist_mode = "wave"
        state.wave_language = language
        if not refill_one():
            return False
        return True
    finally:
        music.release_duck()


def start_genre(station_id, genre_name):
    print(f"🎸 Режим: жанр «{genre_name}» ({station_id})")
    music.hold_duck()
    try:
        memory.remember_search(
            query=genre_name,
            found_artist=f"жанр {genre_name}",
            success=True,
        )
        _reset_source_state()
        with state.playlist_lock:
            state.playlist = []
            state.prefetched = {}
        state.playlist_index = 0
        state.playlist_mode = "genre"
        state.genre_station_id = station_id
        state.genre_name = genre_name
        if not refill_one():
            return False
        return True
    finally:
        music.release_duck()