# workers/music/reset.py
# Сброс состояния источников. Вызывается при смене режима.

from workers.music import state


def _reset_source_state():
    with state.source_buffer_lock:
        state.source_buffer.clear()
    with state.queued_lock:
        state.queued_track_ids.clear()
    state.radio_artist_page = 0
    state.radio_artist_exhausted = False
    state.radio_similar_offset = 0
    state.radio_similar_page = 0
    state.radio_similar_exhausted = False
    state.wave_exhausted = False
    state.wave_exhausted_time = 0.0
    state.single_artist_id = None
    state.current_source_idx = 0
    state.current_source_portions = 0
    state._consecutive_empty = 0
    state.local_played_count = 0
    state.local_wait_until = 0.0
    state.genre_station_id = None
    state.genre_name = None
    with state.session_played_lock:
        state.session_played_ids.clear()
    with state._source_cache_lock:
        state._source_cache.clear()