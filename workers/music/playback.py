# workers/music/playback.py
# Воспроизведение: play_index, play_next, play_prev, monitor_player.
# Плюс refill_one / refill_to_target / warm_up_prefetch.

import os
import time
import threading

import vlc

from shared import config as cfg
from shared import ipc
from shared import protocol
from shared import runtime_state as rt
from shared.audio_cache import find_local_by_track
from shared.log import log
from memory import memory
from workers.music import state
from workers.music.player import music
from workers.music.local import LocalTrack, LikedTrack, get_local_random_track
from workers.music.download import (
    download_track, start_prefetch, _append_and_prefetch,
)
from workers.music.sources import get_next_source_track


PREFETCH_WAIT_SEC = 10.0
MAX_PLAY_ATTEMPTS = 3

DISCOVERY_WAIT_SEC = 3.0
MUSIC_HEARTBEAT_SEC = rt.MUSIC_HEARTBEAT_SEC

_music_instance = None
_audio_meta_cache: tuple[str, dict] = ("", {})


def _get_music_instance():
    global _music_instance
    if _music_instance is None:
        _music_instance = vlc.Instance(
            "--no-video",
            "--no-osd",
            "--quiet",
            "--aout=wasapi",
        )
        log("vlc.Instance создан (music, изолирован от TTS)",
            tag="MUSIC", level="DEBUG")
    return _music_instance


def _get_track_audio_meta(file_path: str) -> dict:
    """Прочитать реальные (bitrate, sample_rate, codec, album, year) из аудиофайла."""
    global _audio_meta_cache
    if not file_path:
        return {}
    if _audio_meta_cache[0] == file_path:
        return _audio_meta_cache[1]

    meta: dict = {}
    if os.path.exists(file_path):
        ext = os.path.splitext(file_path)[1].lstrip(".").upper()
        if ext:
            meta["codec"] = ext
        try:
            import mutagen
            raw = mutagen.File(file_path)
            info = getattr(raw, "info", None)
            if info:
                br = int(getattr(info, "bitrate", 0) or 0)
                sr = int(getattr(info, "sample_rate", 0) or 0)
                if br > 0:
                    meta["bitrate"] = f"{round(br / 1000)} kbps"
                elif ext:
                    meta["bitrate"] = ext
                if sr > 0:
                    sr_khz = f"{sr / 1000:.1f}".rstrip("0").rstrip(".")
                    meta["sample_rate"] = f"{sr_khz} kHz"

            easy = mutagen.File(file_path, easy=True)
            if easy:
                album = easy.get("album")
                date = easy.get("date")
                if album:
                    meta["album"] = str(album[0]).strip()
                if date:
                    meta["year"] = str(date[0]).strip()[:4]
        except Exception:
            pass

    if ("bitrate" not in meta or "sample_rate" not in meta) and music._media is not None:
        try:
            tracks = music._media.get_tracks_info() or []
            for t in tracks:
                if getattr(t, "type", None) == vlc.TrackType.audio:
                    br = int(getattr(t, "bitrate", 0) or 0)
                    sr = int(getattr(t, "rate", 0) or 0)
                    if br > 0 and "bitrate" not in meta:
                        meta["bitrate"] = f"{round(br / 1000)} kbps"
                    if sr > 0 and "sample_rate" not in meta:
                        sr_khz = f"{sr / 1000:.1f}".rstrip("0").rstrip(".")
                        meta["sample_rate"] = f"{sr_khz} kHz"
                    break
        except Exception:
            pass

    _audio_meta_cache = (file_path, meta)
    return meta


def set_music_playing():
    rt.set(rt.FLAG_MUSIC_PLAYING, True, ttl=rt.TTL_MUSIC_PLAYING)


def clear_music_playing():
    rt.clear(rt.FLAG_MUSIC_PLAYING)


def _mind():
    try:
        from personality_core import mind_stream as _ms
        return _ms.get()
    except Exception:
        return None


def _tts_speaking() -> bool:
    try:
        from workers.tts.state_file import get_state
        return bool(get_state().get("speaking", False))
    except Exception:
        return False


def _wait_discovery_tts(timeout: float = DISCOVERY_WAIT_SEC) -> bool:
    waited = 0.0
    while waited < timeout:
        if _tts_speaking():
            return True
        time.sleep(0.1)
        waited += 0.1
    return False


def warm_up_prefetch():
    if not cfg.MUSIC_AVAILABLE:
        return
    try:
        with state.playlist_lock:
            if state.playlist:
                return
        local_path = get_local_random_track()
        if not local_path:
            log("warm_up: локальных треков нет", tag="MUSIC", level="DEBUG")
            return
        with state.playlist_lock:
            if state.playlist:
                return
        _append_and_prefetch(LocalTrack(local_path))
        log("warm_up: первый локальный трек в playlist[0]",
            tag="MUSIC", level="DEBUG")
    except Exception as e:
        log(f"warm_up: {e}", tag="MUSIC", level="WARN")


def refill_one():
    with state.refill_lock:
        with state.playlist_lock:
            ahead = len(state.playlist) - state.playlist_index - 1
        if ahead >= cfg.TARGET_AHEAD:
            return False

        with state.playlist_lock:
            playlist_empty = len(state.playlist) == 0

        if playlist_empty:
            local_path = get_local_random_track()
            if local_path:
                _append_and_prefetch(LocalTrack(local_path))
                log("Первый трек — локальный (быстрый старт)", tag="MUSIC")
                return True

        track = get_next_source_track()
        if not track:
            return False
        _append_and_prefetch(track)
        return True


def refill_to_target():
    while True:
        with state.playlist_lock:
            ahead = len(state.playlist) - state.playlist_index - 1
        if ahead >= cfg.TARGET_AHEAD:
            break
        if not refill_one():
            break


def play_index(idx, announce=False):
    """Воспроизвести трек по индексу. Под state.play_lock."""
    with state.play_lock:
        attempts = 0
        while attempts < MAX_PLAY_ATTEMPTS:
            with state.playlist_lock:
                plen = len(state.playlist)
            if idx >= plen:
                log("Очередь закончилась.", tag="MUSIC")
                return False
            with state.playlist_lock:
                track = state.playlist[idx]

            _artist_for_mind = ""
            _title_for_mind = ""
            _is_discovery = getattr(track, "_is_discovery", False)

            if isinstance(track, LikedTrack):
                from workers.music.search import search_local
                query = f"{track.artist} {track.title}".strip()
                local = search_local(query)
                if local:
                    file_path = local["path"]
                    title = f"{track.title} — {track.artist}"
                    _artist_for_mind = track.artist
                    _title_for_mind = track.title
                    log(f"[{idx+1}/{plen}] Локально (лайк): {title}", tag="MUSIC")
                else:
                    log(f"[{idx+1}/{plen}] Скачиваю лайк: {track.title} — {track.artist}",
                        tag="MUSIC")
                    try:
                        from workers.music.download import download_by_id
                        file_path = download_by_id(track.id)
                    except Exception as e:
                        log(f"download_by_id({track.id}) упал: {e}",
                            tag="MUSIC", level="WARN")
                        file_path = None
                    if not file_path:
                        log(f"Не удалось скачать лайк «{track.title}» — пропуск",
                            tag="MUSIC", level="WARN")
                        idx += 1
                        attempts += 1
                        continue
                    title = f"{track.title} — {track.artist}"
                    _artist_for_mind = track.artist
                    _title_for_mind = track.title
                memory.remember_play(
                    track_id=str(track.id),
                    artist=track.artist,
                    title=track.title,
                    source=state.playlist_mode or 'unknown',
                )
            elif isinstance(track, LocalTrack):
                file_path = track.path
                title = f"{track.title} — {track.artists[0].name}"
                _artist_for_mind = track.artists[0].name
                _title_for_mind = track.title
                log(f"[{idx+1}/{plen}] Локально: {title}", tag="MUSIC")
                memory.remember_play(
                    track_id=str(track.id),
                    artist=track.artists[0].name,
                    title=track.title,
                    source=state.playlist_mode or 'unknown',
                )
            else:
                title = f"{track.title} — {', '.join(a.name for a in track.artists)}"
                _artist_for_mind = ', '.join(a.name for a in track.artists)
                _title_for_mind = track.title
                with state.played_lock:
                    state.played_track_ids.add(track.id)
                with state.queued_lock:
                    state.queued_track_ids.discard(track.id)

                local_file = find_local_by_track(track)
                if local_file:
                    file_path = local_file
                    log(f"[{idx+1}/{plen}] Локально: {title}", tag="MUSIC")
                else:
                    with state.playlist_lock:
                        waiting = idx in state.prefetched
                    file_path = None
                    if waiting:
                        log(f"Жду prefetch: {title}", tag="MUSIC", level="DEBUG")
                        waited = 0.0
                        while waited < PREFETCH_WAIT_SEC:
                            with state.playlist_lock:
                                got = state.prefetched.get(idx)
                            if got is not None:
                                file_path = got
                                break
                            time.sleep(0.5)
                            waited += 0.5
                        if not file_path:
                            log(f"prefetch timeout {PREFETCH_WAIT_SEC:.0f}с — "
                                f"пропускаю «{title}»", tag="MUSIC", level="WARN")
                            idx += 1
                            attempts += 1
                            continue
                    if not file_path:
                        log(f"Качаю сейчас: {title}", tag="MUSIC")
                        try:
                            file_path = download_track(track)
                        except Exception as e:
                            log(f"download_track упал: {e}",
                                tag="MUSIC", level="WARN")
                            file_path = None
                        if file_path:
                            with state.playlist_lock:
                                state.prefetched[idx] = file_path

                memory.remember_play(
                    track_id=str(track.id),
                    artist=', '.join(a.name for a in track.artists),
                    title=track.title,
                    source=state.playlist_mode or 'unknown',
                )

            try:
                ms = _mind()
                if ms is not None:
                    ms.on_play(artist=_artist_for_mind, track=_title_for_mind)
            except Exception as e:
                log(f"mind_stream.on_play: {e}", tag="MUSIC", level="WARN")

            if not file_path or not os.path.exists(file_path):
                log(f"Не удалось получить файл. Пропускаю "
                    f"(попытка {attempts+1}/{MAX_PLAY_ATTEMPTS}).",
                    tag="MUSIC", level="WARN")
                idx += 1
                attempts += 1
                continue

            inst = _get_music_instance()
            media = inst.media_new(file_path)
            player = inst.media_player_new()
            player.set_media(media)
            music.set_player(player, media)

            if _is_discovery:
                try:
                    ipc.put(protocol.BRAIN_INBOX,
                            protocol.event_discovery(artist=_artist_for_mind,
                                                     title=_title_for_mind))
                except Exception as e:
                    log(f"event_discovery: {e}", tag="MUSIC", level="WARN")

                if not _wait_discovery_tts():
                    log(f"discovery: TTS не начал за {DISCOVERY_WAIT_SEC:.0f}с "
                        f"(сеть/API?) — играю без фразы",
                        tag="MUSIC", level="WARN")

            try:
                player.play()
            except Exception as e:
                log(f"player.play() упал: {e}", tag="MUSIC", level="WARN")
                return False

            music.mark_ready()
            set_music_playing()

            state.current_track_title = title
            state.current_track_id = track.id
            state.playlist_index = idx
            state.last_play_start_time = time.time()

            cover_url = ""
            raw_cover = getattr(track, "cover_uri", None) or getattr(track, "og_image", None)
            if isinstance(raw_cover, str) and raw_cover:
                cover_url = "https://" + raw_cover.replace("%%", "400x400").lstrip("/")

            state.current_artist = _artist_for_mind
            state.current_title_only = _title_for_mind
            state.current_cover_url = cover_url
            state.current_file_path = file_path or ""

            log(f"▶ [{idx+1}/{plen}] {title}", tag="MUSIC")

            try:
                ipc.put(protocol.BRAIN_INBOX,
                        protocol.event_music_started(
                            artist=_artist_for_mind,
                            title=_title_for_mind,
                            mode=state.playlist_mode or "",
                        ))
            except Exception as e:
                log(f"event_music_started: {e}", tag="MUSIC", level="WARN")

            start_prefetch(idx + 1, count=cfg.PREFETCH_COUNT)
            threading.Thread(target=refill_to_target, daemon=True).start()
            return True

        log(f"play_index: сдался после {MAX_PLAY_ATTEMPTS} попыток",
            tag="MUSIC", level="WARN")
        return False


def play_next(from_finish=False):
    now = time.time()
    if now - state.last_next_time < cfg.NEXT_DEBOUNCE_SEC:
        log("debounce", tag="MUSIC", level="DEBUG")
        return None
    state.last_next_time = now

    _skip_artist = ""
    _skip_title = ""
    _did_skip = False

    with state.playlist_lock:
        if 0 <= state.playlist_index < len(state.playlist):
            cur = state.playlist[state.playlist_index]
            cur_id = str(cur.id)
            elapsed = 0
            if state.last_play_start_time:
                elapsed = int(time.time() - state.last_play_start_time)

            try:
                if isinstance(cur, LocalTrack):
                    _skip_artist = cur.artists[0].name
                    _skip_title = cur.title
                else:
                    _skip_artist = ', '.join(a.name for a in cur.artists)
                    _skip_title = cur.title
            except Exception:
                pass

            if elapsed > 3 and not from_finish:
                memory.remember_skip(track_id=cur_id, seconds_played=elapsed)
                state._last_skipped_track_id = cur_id
                _did_skip = True
            else:
                state._last_skipped_track_id = cur_id

    if _did_skip:
        try:
            ms = _mind()
            if ms is not None:
                ms.on_skip(artist=_skip_artist, track=_skip_title)
        except Exception as e:
            log(f"mind_stream.on_skip: {e}", tag="MUSIC", level="WARN")

    if state.playlist_mode in ("local_search", "single"):
        state.playlist_mode = "local_search_next"

    next_idx = state.playlist_index + 1
    with state.playlist_lock:
        plen = len(state.playlist)
    if next_idx < plen:
        return play_index(next_idx)
    if refill_one():
        with state.playlist_lock:
            plen = len(state.playlist)
        if state.playlist_index < plen:
            return play_index(state.playlist_index)
        return play_index(state.playlist_index + 1)
    log("Очередь закончилась.", tag="MUSIC")
    return False


def play_prev():
    now = time.time()
    if now - state.last_next_time < cfg.NEXT_DEBOUNCE_SEC:
        log("debounce", tag="MUSIC", level="DEBUG")
        return
    state.last_next_time = now

    with state.playlist_lock:
        cur_idx = state.playlist_index

    if cur_idx <= 0:
        log("предыдущего нет — рестарт текущего", tag="MUSIC", level="DEBUG")
        with state.playlist_lock:
            if 0 <= cur_idx < len(state.playlist):
                cur = state.playlist[cur_idx]
                state._last_skipped_track_id = str(cur.id)
        play_index(cur_idx)
        return

    prev_idx = cur_idx - 1
    with state.playlist_lock:
        if 0 <= cur_idx < len(state.playlist):
            cur = state.playlist[cur_idx]
            state._last_skipped_track_id = str(cur.id)

    play_index(prev_idx)


def monitor_player():
    """Мониторинг плеера + heartbeat music_playing + публикация реальных метаданных."""
    last_state = None
    last_playing_refresh = 0.0
    last_flag_alive = False

    while True:
        time.sleep(1)

        if state.switching:
            last_state = None
            continue

        st = music.get_state()
        if st is None:
            if last_flag_alive:
                clear_music_playing()
                last_flag_alive = False
            last_state = None
            continue

        try:
            from shared.ui_bridge import write_music_ui
            pos_ms = 0
            dur_ms = 0
            if music.player is not None:
                try:
                    pos_ms = max(0, int(music.player.get_time()))
                    dur_ms = max(0, int(music.player.get_length()))
                except Exception:
                    pass
            audio_meta = _get_track_audio_meta(state.current_file_path)
            write_music_ui({
                "playing": st == vlc.State.Playing,
                "paused": st == vlc.State.Paused,
                "artist": state.current_artist,
                "title": state.current_title_only,
                "album": audio_meta.get("album", ""),
                "year": audio_meta.get("year", ""),
                "bitrate": audio_meta.get("bitrate", ""),
                "sample_rate": audio_meta.get("sample_rate", ""),
                "codec": audio_meta.get("codec", ""),
                "mode": state.playlist_mode or "",
                "volume": music.get_volume(),
                "cover_url": state.current_cover_url,
                "file_path": state.current_file_path,
                "pos_ms": pos_ms,
                "dur_ms": dur_ms,
                "eq_preset": music.eq_preset,
                "eq_name": music.eq_preset_name,
            })
        except Exception:
            pass

        now = time.time()

        if st == vlc.State.Playing:
            if (not last_flag_alive) or ((now - last_playing_refresh) >= MUSIC_HEARTBEAT_SEC):
                set_music_playing()
                last_playing_refresh = now
                last_flag_alive = True
        else:
            if last_flag_alive:
                clear_music_playing()
                last_flag_alive = False

        if st == vlc.State.Ended and last_state != vlc.State.Ended:
            log("⏭ Трек закончился...", tag="MUSIC")

            cur = None
            with state.playlist_lock:
                if 0 <= state.playlist_index < len(state.playlist):
                    cur = state.playlist[state.playlist_index]

            if cur:
                cur_id = str(cur.id)
                if state._last_skipped_track_id == cur_id:
                    log(f"skip уже записан, finish пропущен для {cur_id}",
                        tag="MUSIC", level="DEBUG")
                    state._last_skipped_track_id = None
                else:
                    duration = 0
                    if state.last_play_start_time:
                        duration = int(time.time() - state.last_play_start_time)
                    memory.remember_finish(track_id=cur_id, duration=duration)

                    _fin_artist = ""
                    _fin_title = ""
                    try:
                        if isinstance(cur, LocalTrack):
                            _fin_artist = cur.artists[0].name
                            _fin_title = cur.title
                        else:
                            _fin_artist = ', '.join(a.name for a in cur.artists)
                            _fin_title = cur.title
                    except Exception:
                        pass

                    try:
                        ms = _mind()
                        if ms is not None:
                            ms.on_finish(artist=_fin_artist, track=_fin_title)
                    except Exception as e:
                        log(f"mind_stream.on_finish: {e}",
                            tag="MUSIC", level="WARN")

                    try:
                        ipc.put(protocol.BRAIN_INBOX,
                                protocol.event_track_finished(artist=_fin_artist,
                                                              title=_fin_title))
                    except Exception as e:
                        log(f"event_track_finished: {e}",
                            tag="MUSIC", level="WARN")

            state.switching = True
            try:
                play_next(from_finish=True)
            finally:
                state.switching = False

        last_state = st