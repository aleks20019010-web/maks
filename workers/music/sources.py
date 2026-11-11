# workers/music/sources.py
# Источники треков: chart, feed, rotor, similar.
# Blacklist: треки с флагом blacklisted пропускаются в автовыборе.
#
# v5 (2026-11-04): discovery — ротация треков с мин. weight > 0.
#   _try_discovery() — берёт трек через memory.get_discovery_track().
#   Счётчик state.tracks_since_discovery, порог state.DISCOVERY_NEXT
#   (рандомно 10–20, перезаписывается после каждого discovery).
#   Проверка ДО playlist_mode — discovery работает в любом режиме.
#
# ── FIX (этап 0.5): print → log(tag="MUSIC") ────────────────────
#   Раньше sources.py шёл в stderr (print), в jarvis.log его не было.
#   Отладка «почему источник пуст» — только по консоли.
#   Теперь: log(tag="MUSIC", level=...).
#     📊/📰 (загрузка источника) — DEBUG (шумно).
#     ✗ (ошибки Yandex API) — WARN.
#     📻 (переходы режимов: артист кончился → mixed) — INFO.
#     ⛔/⏭/♻ (blacklist, дубли, автосброс) — DEBUG.
#     🔄 DISCOVERY — INFO.
#
# ── P1.5 (2026-11-09): Яндекс — не долбить мёртвый API ──────────
#   _yandex_is_down() / _mark_yandex_fail() / _mark_yandex_ok().
#   Все обращения к Яндекс-источникам обёрнуты проверкой.
#   5 фейлов подряд → блок на YANDEX_BLOCK_SEC.
#
# ── LOCAL_LIKED (v6.5) ──────────────────────────────────────────
#   playlist_mode == "local_liked" — ленивый источник лайков.
#   modes.queue_local_liked() кладёт state.liked_queue = list(liked),
#   state.liked_cursor = 0. Здесь берём по одному:
#     item = state.liked_queue[state.liked_cursor]
#     state.liked_cursor += 1
#     return LikedTrack(artist, title, track_id)
#   LikedTrack скачивается по требованию в playback.play_index
#   (search_local → download_by_id). Исчерпание → mixed.
#   Discovery для local_liked НЕ работает — нечего ротировать.

import time
import random
import threading

from shared import config as cfg
from shared.log import log
from workers.music import state
from workers.music.local import (
    LocalTrack, LikedTrack, get_local_by_genre, get_local_random_track,
)
from workers.music.similar import fetch_similar_background


# ============================================================
# P1.5: Яндекс — не долбить мёртвый API
# ============================================================
def _yandex_is_down() -> bool:
    """True — Яндекс сейчас не дёргаем (блок или ещё не время)."""
    now = time.time()
    if now < state.yandex_blocked_until:
        return True
    if now < state.yandex_next_try_ts:
        return True
    return False


def _mark_yandex_fail():
    """Фейл: +1 к счётчику. 5 подряд — блок на час."""
    state.yandex_fail_count += 1
    if state.yandex_fail_count >= cfg.YANDEX_FAIL_MAX:
        state.yandex_blocked_until = time.time() + cfg.YANDEX_BLOCK_SEC
        state.yandex_next_try_ts = 0.0
        log(f"Яндекс: {state.yandex_fail_count} фейлов подряд — "
            f"блок на {cfg.YANDEX_BLOCK_SEC/60:.0f} мин",
            tag="MUSIC", level="WARN")
    else:
        state.yandex_next_try_ts = time.time() + cfg.YANDEX_RETRY_INTERVAL_SEC
        log(f"Яндекс: фейл {state.yandex_fail_count}/{cfg.YANDEX_FAIL_MAX}, "
            f"следующая попытка через {cfg.YANDEX_RETRY_INTERVAL_SEC:.0f}с",
            tag="MUSIC", level="WARN")


def _mark_yandex_ok():
    """Успех: сброс счётчика и блока. Лог — только если был сброс."""
    if state.yandex_fail_count > 0 or state.yandex_blocked_until > 0:
        log("Яндекс: снова доступен, сброс", tag="MUSIC", level="INFO")
    state.yandex_fail_count = 0
    state.yandex_next_try_ts = 0.0
    state.yandex_blocked_until = 0.0


def _is_blacklisted(track_id) -> bool:
    try:
        from memory import memory
        return memory.is_blacklisted(track_id)
    except Exception:
        return False


# ============================================================
# DISCOVERY (v5) — ротация: мин. weight > 0
# ============================================================
def _try_discovery():
    """Трек с мин. weight > 0 (ротация снизу вверх). Рандомно каждые 10–20.

    1. Локальный файл — find_local_by_track(fake_track) по artist+title.
    2. Яндекс — tracks([int(id)]) для цифровых id.
    """
    try:
        from memory import memory
        row = memory.get_discovery_track(
            min_hours=getattr(state, "DISCOVERY_MIN_HOURS", 0)
        )
        if not row:
            return None
        track_id = row.get("track_id")
        artist = row.get("artist", "")
        title = row.get("title", "")
        if not track_id:
            return None

        # 1. Локальный файл — через fake track (find_local_by_track)
        try:
            from types import SimpleNamespace
            from shared.audio_cache import find_local_by_track
            fake_track = SimpleNamespace(
                id=track_id,
                title=title,
                artists=[SimpleNamespace(name=artist)],
            )
            local_path = find_local_by_track(fake_track)
            if local_path:
                lt = LocalTrack(local_path)
                lt._is_discovery = True
                lt._discovery_kind = "rotation"
                return lt
        except Exception as e:
            log(f"discovery local: {e}", tag="MUSIC", level="DEBUG")

        # 2. Яндекс — tracks([int(id)]), только цифровые
        tid_str = str(track_id)
        if state.client is not None and tid_str.isdigit():
            try:
                result = state.client.tracks([int(track_id)])
                if result:
                    track = result[0]
                    track._is_discovery = True
                    track._discovery_kind = "rotation"
                    return track
            except Exception as e:
                log(f"discovery fetch: {e}", tag="MUSIC", level="DEBUG")
    except Exception as e:
        log(f"discovery: {e}", tag="MUSIC", level="WARN")
    return None


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
        log(f"artists_tracks: {e}", tag="MUSIC", level="WARN")
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
                log(f"blacklist: {t.title}", tag="MUSIC", level="DEBUG")
                continue
            return t
    return None


# ============================================================
# ИСТОЧНИКИ: CHART, FEED
# ============================================================
def _try_chart():
    if state.client is None:
        return None
    if _yandex_is_down():
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
                log(f"Chart: {len(tracks)} треков загружено",
                    tag="MUSIC", level="DEBUG")
            except Exception as e:
                log(f"Chart: {e}", tag="MUSIC", level="WARN")
                state._source_cache['chart'] = []
                _mark_yandex_fail()

        chart_tracks = state._source_cache.get('chart', [])
        if chart_tracks:
            _mark_yandex_ok()
            return chart_tracks.pop(0)
    return None


def _try_feed_tracks():
    if state.client is None:
        return None
    if _yandex_is_down():
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
                log(f"Feed tracks: {len(tracks)} треков загружено",
                    tag="MUSIC", level="DEBUG")
            except Exception as e:
                log(f"Feed tracks: {e}", tag="MUSIC", level="WARN")
                state._source_cache['feed_tracks'] = []
                _mark_yandex_fail()

        feed_tracks = state._source_cache.get('feed_tracks', [])
        if feed_tracks:
            _mark_yandex_ok()
            return feed_tracks.pop(0)
    return None


def _try_feed_albums():
    if state.client is None:
        return None
    if _yandex_is_down():
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
                log(f"Feed albums: {len(tracks)} треков загружено",
                    tag="MUSIC", level="DEBUG")
            except Exception as e:
                log(f"Feed albums: {e}", tag="MUSIC", level="WARN")
                state._source_cache['feed_albums'] = []
                _mark_yandex_fail()

        feed_albums = state._source_cache.get('feed_albums', [])
        if feed_albums:
            _mark_yandex_ok()
            return feed_albums.pop(0)
    return None


def _try_rotor(source_id):
    if state.client is None:
        return None
    if _yandex_is_down():
        return None

    if state.wave_exhausted:
        if (time.time() - state.wave_exhausted_time) > cfg.WAVE_RESET_SEC:
            log("Автосброс wave_exhausted", tag="MUSIC", level="DEBUG")
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
        log(f"Rotor ({source_name}): {e}", tag="MUSIC", level="WARN")
        _mark_yandex_fail()
        return None

    if not tracks_result or not tracks_result.sequence:
        log(f"Пусто ({source_name})", tag="MUSIC", level="DEBUG")
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
        log(f"Все дубли ({source_name})", tag="MUSIC", level="DEBUG")
        return None

    _mark_yandex_ok()
    with state.source_buffer_lock:
        return state.source_buffer.pop(0)


def _try_rotor_session(seeds, label="Rotor session"):
    if state.client is None:
        return None
    if _yandex_is_down():
        return None

    with state.source_buffer_lock:
        if state.source_buffer:
            return state.source_buffer.pop(0)

    try:
        session = state.client.rotor_session_new(seeds=seeds)
        if not session or not session.radio_session_id:
            log(f"{label}: сессия не создалась", tag="MUSIC", level="WARN")
            return None
        tracks_result = state.client.rotor_session_tracks(session.radio_session_id)
    except Exception as e:
        log(f"{label}: {e}", tag="MUSIC", level="WARN")
        _mark_yandex_fail()
        return None

    if not tracks_result or not tracks_result.sequence:
        log(f"{label}: пусто", tag="MUSIC", level="DEBUG")
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
        log(f"{label}: все дубли", tag="MUSIC", level="DEBUG")
        return None

    _mark_yandex_ok()
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
# LOCAL_LIKED (v6.5) — ленивый источник лайков
# ============================================================
def _try_local_liked():
    """Взять следующий лайк из state.liked_queue (лениво).

    modes.queue_local_liked() наполнил liked_queue и сбросил liked_cursor.
    Здесь берём по одному: LikedTrack(artist, title, track_id).
    LikedTrack скачивается по требованию в playback.play_index.
    """
    queue = getattr(state, "liked_queue", None)
    if not queue:
        return None
    cursor = getattr(state, "liked_cursor", 0)
    if cursor >= len(queue):
        return None
    item = queue[cursor]
    state.liked_cursor = cursor + 1

    # item может быть dict (из liked_tracks.json) или уже LikedTrack
    if isinstance(item, LikedTrack):
        track = item
    else:
        artist = item.get("artist", "") if isinstance(item, dict) else ""
        title = item.get("title", "") if isinstance(item, dict) else ""
        track_id = item.get("track_id", "") if isinstance(item, dict) else ""
        track = LikedTrack(artist=artist, title=title, track_id=str(track_id))

    # Дубли/blacklist — по track_id (если он есть).
    tid = str(track.id) if track.id else ""
    if tid:
        with state.played_lock, state.queued_lock:
            skip = (tid in state.played_track_ids or
                    tid in state.queued_track_ids or
                    _is_blacklisted(tid))
        if skip:
            log(f"local_liked: дубль/blacklist «{track.artist} — {track.title}»",
                tag="MUSIC", level="DEBUG")
            return _try_local_liked()

    return track


# ============================================================
# ГЛАВНАЯ ФУНКЦИЯ
# ============================================================
def get_next_source_track():
    playlist_mode = state.playlist_mode

    # ── LOCAL_LIKED (v6.5): ленивый источник лайков ──
    # discovery НЕ работает для local_liked — нечего ротировать,
    # пользователь явно попросил «мои лайки».
    if playlist_mode == "local_liked":
        track = _try_local_liked()
        if track:
            return track
        log("local_liked исчерпан → mixed", tag="MUSIC", level="INFO")
        state.playlist_mode = "mixed"
        state.local_played_count = 0
        state.local_wait_until = 0.0
        return get_next_source_track()

    # ── DISCOVERY (v5): рандомно каждые 10–20 треков — мин. weight > 0 ──
    state.tracks_since_discovery += 1
    if state.tracks_since_discovery >= state.DISCOVERY_NEXT:
        state.tracks_since_discovery = 0
        state.DISCOVERY_NEXT = random.randint(
            state.DISCOVERY_MIN, state.DISCOVERY_MAX
        )
        disc_track = _try_discovery()
        if disc_track:
            log(f"DISCOVERY ROTATION (next in {state.DISCOVERY_NEXT}): "
                f"{getattr(disc_track, 'title', '?')}",
                tag="MUSIC", level="INFO")
            return disc_track

    if playlist_mode == "single":
        log("Одиночный трек закончился...", tag="MUSIC", level="INFO")
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
        log("Трек артиста закончился, перехожу к похожим...",
            tag="MUSIC", level="INFO")
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
            log("'Мне нравится' закончилось → mixed", tag="MUSIC", level="INFO")
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

        log("Артист закончился → mixed (волна + локальные)",
            tag="MUSIC", level="INFO")
        state.playlist_mode = "mixed"
        state.local_played_count = 0
        state.local_wait_until = 0.0
        return get_next_source_track()

    if playlist_mode == "similar":
        track = _try_similar()
        if track:
            return track
        log("Похожие закончились → mixed", tag="MUSIC", level="INFO")
        state.playlist_mode = "mixed"
        state.local_played_count = 0
        return get_next_source_track()

    if playlist_mode == "genre":
        station = getattr(state, "genre_station_id", None)
        if not station:
            log("Жанр не задан → mixed", tag="MUSIC", level="INFO")
            state.playlist_mode = "mixed"
            return get_next_source_track()

        if not _yandex_is_down():
            track = _try_rotor(station)
            if track:
                return track

        local_path = get_local_by_genre(state.genre_name)
        if local_path:
            return LocalTrack(local_path)

        log(f"Жанр «{state.genre_name}» кончился → mixed",
            tag="MUSIC", level="INFO")
        state.playlist_mode = "mixed"
        state.local_played_count = 0
        state.local_wait_until = 0.0
        return get_next_source_track()

    if playlist_mode == "wave":
        if getattr(state, "wave_language", None) == "russian":
            if not _yandex_is_down():
                track = _try_rotor_session(
                    seeds=['user:onyourwave', 'settingLanguage:russian'],
                    label="Русская волна",
                )
                if track:
                    return track
            log("Русская волна пуста → mixed", tag="MUSIC", level="INFO")
            state.playlist_mode = "mixed"
            state.local_played_count = 0
            state.local_wait_until = 0.0
            return get_next_source_track()

        if not _yandex_is_down():
            for source_id in cfg.SOURCE_LIST:
                track = _try_yandex_source(source_id)
                if track:
                    return track
        log("Все источники пусты → mixed", tag="MUSIC", level="INFO")
        state.playlist_mode = "mixed"
        state.local_played_count = 0
        state.local_wait_until = 0.0
        return get_next_source_track()

    if playlist_mode == "mixed":
        jitter = state.LOCAL_STREAK_JITTER
        limit = state.LOCAL_MAX_STREAK + random.randint(-jitter, jitter)

        if state.local_played_count < limit:
            if (time.time() >= state.local_wait_until
                    and not _yandex_is_down()):
                for source_id in cfg.SOURCE_LIST:
                    track = _try_yandex_source(source_id)
                    if track:
                        state.local_played_count = 0
                        return track
                state.local_wait_until = time.time() + state.LOCAL_RETRY_AFTER_SEC

        local_path = get_local_random_track()
        if local_path:
            state.local_played_count += 1
            log(f"Локальный ({state.local_played_count}/{limit})",
                tag="MUSIC", level="DEBUG")
            return LocalTrack(local_path)

        state.local_played_count = 0
        if not _yandex_is_down():
            for source_id in cfg.SOURCE_LIST:
                track = _try_yandex_source(source_id)
                if track:
                    return track
        return None

    return None