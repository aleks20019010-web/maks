# workers/music/modes.py
# Режимы воспроизведения: артист, трек артиста, одиночный трек,
# лайки, волна, жанр.
#
# Music worker НЕ говорит. Brain скажет сам, до отправки в music_inbox.
#
# ── FIX (этап 0.5): queue_wave / start_genre / queue_liked ──────────
#   Симптом: «включи музыку» → queue_wave → refill_one →
#   get_next_source_track (Yandex API, feed() 10–12 сек) →
#   play_index(0) → ▶. 10 секунд тишины после TTS.
#
#   Причина: queue_wave делал playlist.clear() (дублировал
#   _reset_playback_state из _stop_old_track) и refill_one()
#   (синхронный Yandex API: feed(), chart(), albums_with_tracks())
#   ДО play_index(0).
#
#   Решение (архитектурное):
#     - _stop_old_track (в _handle_play) → _reset_playback_state →
#       playlist.clear() + warm_up_prefetch (СИНХРОННО, 0.21 сек).
#       warm_up_prefetch кладёт ОДИН локальный трек в playlist[0].
#     - queue_wave / start_genre / queue_liked — БЕЗ playlist.clear()
#       (уже очищено) и БЕЗ refill_one() (это refill_to_target в фоне
#       после play_index(0)). Только _reset_source_state() +
#       playlist_mode = ... — 0.001 сек.
#     - play_index(0) берёт warm_up-локальный из playlist[0] → ▶ 0.5 сек.
#     - play_index(0) → threading.Thread(refill_to_target) → Yandex
#       для playlist[1..N] (10–12 сек, в фоне, пользователь слушает).
#
#   Для start_artist_only / start_artist_track / start_single_track
#   warm_up не нужен — они явно делают playlist = [track]
#   (режим «только этот трек»). warm_up-трек будет затёрт — 0.21 сек
#   впустую, не критично.
#
# DUCKING (день 29, вечер): ducking привязан к микрофону ASR.
#   hold_duck/release_duck здесь — это «пока грузим данные, не
#   восстанавливай громкость». С TTS-ducking они не пересекаются
#   (см. player.py). Прямых music.duck()/music.restore() тут нет
#   и не должно быть — ducking включает/снимает только ASR
#   через mic_off/mic_on.
#
# FIX (2026-10-31): hold_duck/release_duck парны на ВСЕХ путях выхода
#   (try/finally).
#
# ── FIX (этап 0.5): print → log(tag="MUSIC") ────────────────────
#   Раньше все сообщения modes.py шли в stderr (print), в jarvis.log
#   их не было. Отладка «почему режим не сработал» — только по консоли.

import threading
import time

from shared.log import log
from workers.music import state
from workers.music.player import music
from memory import memory
from workers.music.sources import (
    get_artist_tracks_page, get_next_unplayed_track_from_page,
)
from workers.music.search import find_artist_track
from workers.music.similar import fetch_similar_background
from workers.music.reset import _reset_source_state
# refill_one больше НЕ импортируется: queue_wave / start_genre /
# queue_liked не делают синхронный refill. refill_to_target в
# playback.py (после play_index(0)) делает это в фоне.


def start_artist_only(artist):
    log(f"режим artist_only: {artist.name}", tag="MUSIC", level="DEBUG")
    state.radio_artist_id = artist.id
    state.radio_artist_name = artist.name
    _reset_source_state()
    first_page = get_artist_tracks_page(artist.id, 0)
    first_track = get_next_unplayed_track_from_page(first_page)
    if not first_track:
        log(f"artist_only: у «{artist.name}» нет треков", tag="MUSIC", level="WARN")
        return False
    with state.played_lock:
        state.played_track_ids.add(first_track.id)
    with state.playlist_lock:
        state.playlist = [first_track]
        state.prefetched = {}
    state.playlist_index = 0
    state.playlist_mode = "artist_only"
    log(f"artist_only: первый трек — {first_track.title}", tag="MUSIC")
    return True


def start_artist_track(artist, track_query):
    log(f"режим artist_track: «{track_query}» у {artist.name}",
        tag="MUSIC", level="DEBUG")
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
    threading.Thread(target=fetch_similar_background,
                     args=(artist.id,), daemon=True).start()
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
        page_tracks = get_artist_tracks_page(
            state.radio_artist_id, state.radio_artist_page
        )
        if not page_tracks:
            return None
        t = get_next_unplayed_track_from_page(page_tracks)
        if t:
            return t
        state.radio_artist_page += 1


def start_single_track(track):
    log(f"режим single: {track.title}", tag="MUSIC", level="DEBUG")
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
    """FIX (этап 0.5): без playlist.clear() и без refill_one().

    playlist уже очищен в _reset_playback_state (из _stop_old_track).
    warm_up_prefetch положил локальный в playlist[0] — не трогаем.
    Yandex-лайки — в фоне через refill_to_target после play_index(0).
    """
    music.hold_duck()
    try:
        memory.remember_search(
            query="мою музыку", found_artist="Мне нравится", success=True,
        )
        if state.client is None:
            log("queue_liked: нет клиента Яндекса", tag="MUSIC", level="WARN")
            return False
        try:
            liked = state.client.users_likes_tracks()
            if not liked or not liked.tracks:
                log("queue_liked: лайков нет", tag="MUSIC", level="WARN")
                return False
            state.liked_short_tracks = liked.tracks
            state.liked_offset = 0
            _reset_source_state()
            state.playlist_mode = "liked"
            log(f"режим liked: {len(liked.tracks)} треков в источнике",
                tag="MUSIC")
            return True
        except Exception as e:
            log(f"queue_liked: {e}", tag="MUSIC", level="WARN")
            return False
    finally:
        music.release_duck()


def queue_wave(language=None):
    """FIX (этап 0.5): без playlist.clear() и без refill_one().

    playlist уже очищен в _reset_playback_state (из _stop_old_track).
    warm_up_prefetch положил локальный в playlist[0] — не трогаем.
    Yandex-волна — в фоне через refill_to_target после play_index(0).
    """
    music.hold_duck()
    try:
        if language == "russian":
            memory.remember_search(
                query="включи русское", found_artist="Русская волна", success=True,
            )
        else:
            memory.remember_search(
                query="включи музыку", found_artist="Моя волна", success=True,
            )

        _reset_source_state()
        state.playlist_mode = "wave"
        state.wave_language = language
        log(f"режим wave (language={language!r})", tag="MUSIC")
        return True
    finally:
        music.release_duck()


def start_genre(station_id, genre_name):
    """FIX (этап 0.5): без playlist.clear() и без refill_one()."""
    music.hold_duck()
    try:
        memory.remember_search(
            query=genre_name,
            found_artist=f"жанр {genre_name}",
            success=True,
        )
        _reset_source_state()
        state.playlist_mode = "genre"
        state.genre_station_id = station_id
        state.genre_name = genre_name
        log(f"режим genre: «{genre_name}» ({station_id})", tag="MUSIC")
        return True
    finally:
        music.release_duck()