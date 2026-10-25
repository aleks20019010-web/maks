# workers/music/sources.py
# Источники треков: chart, feed, rotor, similar.
# Blacklist: треки с флагом blacklisted пропускаются в автовыборе.

import time
import random
import threading

from shared import config as cfg
from workers.music import state
from workers.music.local import (
    LocalTrack, get_local_by_genre, get_local_random_track,
)
from workers.music.similar import fetch_similar_background


def _is_blacklisted(track_id) -> bool:
    try:
        from memory import memory
        return memory.is_blacklisted(track_id)
    except Exception:
        return False


# ============================================================
# ARTISTS_TRACKS
# ============================================================
def get_artist_tracks_page(artist_id, page, page_size=cfg.ARTIST_PAGE_SIZE):
    cache_key = (artist_id, page)
    if cache_key in state.artist_tracks_cache:
        return state.artist_tracks_cache[cache_key]
    if state.artist_tracks_exhausted.get(artist_id, -1) >= page:
        return []

    if state.client is None:
        return []

    try:
        result = state.client.artists_tracks(artist_id, page=page, page_size=page_size)
        tracks = list(result.tracks) if (result and result.tracks) else []
    except Exception as e:
        print(f"   Ошибка artists_tracks: {e}")
        tracks = []
    state.artist_tracks_cache[cache_key] = tracks
    if len(tracks) == 0:
        state.artist_tracks_exhausted[artist_id] = page
    return tracks


def get_next_unplayed_track_from_page(tracks):
    with state.played_lock, state.queued_lock:
        for t in tracks:
            if t.id in state.played_track_ids:
                continue
            if t.id in state.queued_track_ids:
                continue
            if _is_blacklisted(t.id):
                print(f"   ⛔ blacklist: {t.title}")
                continue
            return t
    return None


# ============================================================
# ИСТОЧНИКИ: CHART, FEED
# ============================================================
def _try_chart():
    if state.client is None:
        return None
    with state._source_cache_lock:
        if 'chart' not in state._source_cache:
            try:
                chart = state.client.chart()
                tracks = []
                if chart and chart.chart and chart.chart.tracks:
                    for item in chart.chart.tracks:
                        try:
                            t = item.track
                            with state.played_lock, state.queued_lock:
                                skip = (t.id in state.played_track_ids or
                                        t.id in state.queued_track_ids or
                                        _is_blacklisted(t.id))
                            if not skip:
                                tracks.append(t)
                        except Exception:
                            pass
                state._source_cache['chart'] = tracks
                print(f"   📊 Chart: {len(tracks)} треков загружено")
            except Exception as e:
                print(f"   ✗ Chart: {e}")
                state._source_cache['chart'] = []

        chart_tracks = state._source_cache.get('chart', [])
        if chart_tracks:
            return chart_tracks.pop(0)
    return None


def _try_feed_tracks():
    if state.client is None:
        return None
    with state._source_cache_lock:
        if 'feed_tracks' not in state._source_cache:
            try:
                feed = state.client.feed()
                tracks = []
                if feed and feed.days:
                    for day in feed.days:
                        for event in day.events:
                            if event.type == 'tracks' and hasattr(event, 'tracks') and event.tracks:
                                for t in event.tracks:
                                    try:
                                        with state.played_lock, state.queued_lock:
                                            skip = (t.id in state.played_track_ids or
                                                    t.id in state.queued_track_ids or
                                                    _is_blacklisted(t.id))
                                        if not skip:
                                            tracks.append(t)
                                    except Exception:
                                        pass
                state._source_cache['feed_tracks'] = tracks
                print(f"   📰 Feed tracks: {len(tracks)} треков загружено")
            except Exception as e:
                print(f"   ✗ Feed tracks: {e}")
                state._source_cache['feed_tracks'] = []

        feed_tracks = state._source_cache.get('feed_tracks', [])
        if feed_tracks:
            return feed_tracks.pop(0)
    return None


def _try_feed_albums():
    if state.client is None:
        return None
    with state._source_cache_lock:
        if 'feed_albums' not in state._source_cache:
            try:
                feed = state.client.feed()
                tracks = []
                if feed and feed.days:
                    for day in feed.days:
                        for event in day.events:
                            if event.type == 'albums' and hasattr(event, 'albums') and event.albums:
                                for album in event.albums[:3]:
                                    try:
                                        full = state.client.albums_with_tracks(album.id)
                                        if full and full.volumes:
                                            for volume in full.volumes:
                                                for t in volume[:3]:
                                                    try:
                                                        with state.played_lock, state.queued_lock:
                                                            skip = (t.id in state.played_track_ids or
                                                                    t.id in state.queued_track_ids or
                                                                    _is_blacklisted(t.id))
                                                        if not skip:
                                                            tracks.append(t)
                                                    except Exception:
                                                        pass
                                    except Exception:
                                        pass
                state._source_cache['feed_albums'] = tracks
                print(f"   📰 Feed albums: {len(tracks)} треков загружено")
            except Exception as e:
                print(f"   ✗ Feed albums: {e}")
                state._source_cache['feed_albums'] = []

        feed_albums = state._source_cache.get('feed_albums', [])
        if feed_albums:
            return feed_albums.pop(0)
    return None


def _try_rotor(source_id):
    if state.client is None:
        return None

    if state.wave_exhausted:
        if (time.time() - state.wave_exhausted_time) > cfg.WAVE_RESET_SEC:
            print(f"  ♻ Автосброс wave_exhausted")
            state.wave_exhausted = False
            state.wave_exhausted_time = 0.0
            state._consecutive_empty = 0
        else:
            return None

    with state.source_buffer_lock:
        if state.source_buffer:
            return state.source_buffer.pop(0)

    source_name = "Моя волна" if source_id == cfg.MY_WAVE_ID else source_id

    try:
        tracks_result = state.client.rotor_station_tracks(source_id)
    except Exception as e:
        print(f"   ✗ Rotor ({source_name}): {e}")
        return None

    if not tracks_result or not tracks_result.sequence:
        print(f"   ✗ Пусто ({source_name})")
        return None

    with state.source_buffer_lock:
        added = 0
        for item in tracks_result.sequence:
            try:
                t = item.track
                with state.played_lock, state.queued_lock:
                    skip = (t.id in state.played_track_ids or
                            t.id in state.queued_track_ids or
                            _is_blacklisted(t.id))
                if not skip:
                    state.source_buffer.append(t)
                    added += 1
            except Exception:
                pass

    if added == 0:
        print(f"   ⏭ Все дубли ({source_name})")
        return None

    with state.source_buffer_lock:
        return state.source_buffer.pop(0)


def _try_rotor_session(seeds, label="Rotor session"):
    if state.client is None:
        return None

    with state.source_buffer_lock:
        if state.source_buffer:
            return state.source_buffer.pop(0)

    try:
        session = state.client.rotor_session_new(seeds=seeds)
        if not session or not session.radio_session_id:
            print(f"   ✗ {label}: сессия не создалась")
            return None
        tracks_result = state.client.rotor_session_tracks(session.radio_session_id)
    except Exception as e:
        print(f"   ✗ {label}: {e}")
        return None

    if not tracks_result or not tracks_result.sequence:
        print(f"   ✗ {label}: пусто")
        return None

    with state.source_buffer_lock:
        added = 0
        for item in tracks_result.sequence:
            try:
                t = item.track
                with state.played_lock, state.queued_lock:
                    skip = (t.id in state.played_track_ids or
                            t.id in state.queued_track_ids or
                            _is_blacklisted(t.id))
                if not skip:
                    state.source_buffer.append(t)
                    added += 1
            except Exception:
                pass

    if added == 0:
        print(f"   ⏭ {label}: все дубли")
        return None

    with state.source_buffer_lock:
        return state.source_buffer.pop(0)


def _try_yandex_source(source_id):
    if source_id == 'chart':
        return _try_chart()
    elif source_id == 'feed_tracks':
        return _try_feed_tracks()
    elif source_id == 'feed_albums':
        return _try_feed_albums()
    else:
        return _try_rotor(source_id)


def _try_similar():
    while not state.radio_similar_exhausted:
        if state.radio_similar_offset >= len(state.radio_similar_list):
            state.radio_similar_exhausted = True
            return None
        sa = state.radio_similar_list[state.radio_similar_offset]
        page_tracks = get_artist_tracks_page(sa.id, state.radio_similar_page)
        if not page_tracks:
            state.radio_similar_offset += 1
            state.radio_similar_page = 0
            continue
        track = get_next_unplayed_track_from_page(page_tracks)
        if track:
            return track
        state.radio_similar_page += 1
    return None


# ============================================================
# ГЛАВНАЯ ФУНКЦИЯ
# ============================================================
def get_next_source_track():
    playlist_mode = state.playlist_mode

    if playlist_mode == "single":
        print("📻 Одиночный трек закончился...")
        if state.single_artist_id and not state.radio_similar_list:
            threading.Thread(target=fetch_similar_background,
                             args=(state.single_artist_id,), daemon=True).start()
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
            state.playlist_mode = "wave"
            state.wave_exhausted = False
            state._consecutive_empty = 0
        return get_next_source_track()

    if playlist_mode == "artist_track":
        print("📻 Трек артиста закончился, перехожу к похожим...")
        if not state.radio_similar_list and state.radio_artist_id:
            threading.Thread(target=fetch_similar_background,
                             args=(state.radio_artist_id,), daemon=True).start()
            waited = 0
            while not state.radio_similar_list and waited < 15:
                time.sleep(0.5)
                waited += 1
        state.playlist_mode = "similar"
        state.radio_similar_offset = 0
        state.radio_similar_page = 0
        state.radio_similar_exhausted = False
        return get_next_source_track()

    if playlist_mode == "liked":
        if state.liked_offset >= len(state.liked_short_tracks):
            with state.source_buffer_lock:
                if state.source_buffer:
                    return state.source_buffer.pop(0)
            print("📻 'Мне нравится' закончилось → mixed")
            state.playlist_mode = "mixed"
            state.local_played_count = 0
            return get_next_source_track()
        with state.source_buffer_lock:
            if state.source_buffer:
                return state.source_buffer.pop(0)
            end = min(state.liked_offset + cfg.LIKED_BATCH, len(state.liked_short_tracks))
            for short in state.liked_short_tracks[state.liked_offset:end]:
                try:
                    t = short.fetch_track()
                    with state.played_lock, state.queued_lock:
                        skip = (t.id in state.played_track_ids or
                                t.id in state.queued_track_ids or
                                _is_blacklisted(t.id))
                    if not skip:
                        state.source_buffer.append(t)
                except Exception:
                    pass
            state.liked_offset = end
            if state.source_buffer:
                return state.source_buffer.pop(0)
        return None

    if playlist_mode == "artist_only":
        while not state.radio_artist_exhausted:
            if state.radio_artist_page >= 30:
                state.radio_artist_exhausted = True
                break
            page_tracks = get_artist_tracks_page(state.radio_artist_id, state.radio_artist_page)
            if not page_tracks:
                state.radio_artist_exhausted = True
                break
            track = get_next_unplayed_track_from_page(page_tracks)
            if track:
                return track
            state.radio_artist_page += 1

        print("📻 Артист закончился → mixed (волна + локальные)")
        state.playlist_mode = "mixed"
        state.local_played_count = 0
        state.local_wait_until = 0.0
        return get_next_source_track()

    if playlist_mode == "similar":
        track = _try_similar()
        if track:
            return track
        print("📻 Похожие закончились → mixed")
        state.playlist_mode = "mixed"
        state.local_played_count = 0
        return get_next_source_track()

    if playlist_mode == "genre":
        station = getattr(state, "genre_station_id", None)
        if not station:
            print("📻 Жанр не задан → mixed")
            state.playlist_mode = "mixed"
            return get_next_source_track()

        track = _try_rotor(station)
        if track:
            return track

        local_path = get_local_by_genre(state.genre_name)
        if local_path:
            return LocalTrack(local_path)

        print(f"📻 Жанр «{state.genre_name}» кончился → mixed")
        state.playlist_mode = "mixed"
        state.local_played_count = 0
        state.local_wait_until = 0.0
        return get_next_source_track()

    if playlist_mode == "wave":
        if getattr(state, "wave_language", None) == "russian":
            track = _try_rotor_session(
                seeds=['user:onyourwave', 'settingLanguage:russian'],
                label="Русская волна",
            )
            if track:
                return track
            print("📻 Русская волна пуста → mixed")
            state.playlist_mode = "mixed"
            state.local_played_count = 0
            state.local_wait_until = 0.0
            return get_next_source_track()

        for source_id in cfg.SOURCE_LIST:
            track = _try_yandex_source(source_id)
            if track:
                return track
        print("📻 Все источники пусты → mixed")
        state.playlist_mode = "mixed"
        state.local_played_count = 0
        state.local_wait_until = 0.0
        return get_next_source_track()

    if playlist_mode == "mixed":
        jitter = state.LOCAL_STREAK_JITTER
        limit = state.LOCAL_MAX_STREAK + random.randint(-jitter, jitter)

        if state.local_played_count < limit:
            if time.time() >= state.local_wait_until:
                for source_id in cfg.SOURCE_LIST:
                    track = _try_yandex_source(source_id)
                    if track:
                        state.local_played_count = 0
                        return track
                state.local_wait_until = time.time() + state.LOCAL_RETRY_AFTER_SEC

        local_path = get_local_random_track()
        if local_path:
            state.local_played_count += 1
            print(f"📻 Локальный ({state.local_played_count}/{limit})")
            return LocalTrack(local_path)

        state.local_played_count = 0
        for source_id in cfg.SOURCE_LIST:
            track = _try_yandex_source(source_id)
            if track:
                return track
        return None

    return None