"""
workers/music/main.py — Music worker.

Принимает команды из music_inbox, выполняет.

── FIX (v8.9.1, 2026-11-13): импорты + playlist_mode ──────────────
  search_local_artist_tracks — в local.py, не в search.py.
  Раньше: search.search_local_artist_tracks → AttributeError.
  Теперь: from workers.music.local import search_local_artist_tracks.

  _play_local_artist: playlist_mode = "local_search" (вместо "local_artist").
  sources.py уже умеет "local_search" — при конце плейлиста докидывает
  похожие треки артиста из local_index, потом wave. Музыка не
  останавливается, когда треки артиста кончились.

── FIX (v8.9, 2026-11-13): локальный артист первым ────────────────
  _handle_play("artist"): сначала search_local_artist_tracks (local_index.json,
  быстро, без сети), играем локальные треки артиста. Если локально нет —
  Яндекс fallback (search_artist).

  _handle_play("search"): локальный артист → локальный трек →
  Яндекс-артист → Яндекс-трек.

── FIX (v8.4, 2026-11-13): pre-duck в _handle_play("search") ─────
  pre-duck срабатывает ТОЛЬКО если TTS говорит СЕЙЧАС.
  DUCK_MAX_SEC = 15 (вместо 60) — watchdog быстрее.

── FIX (этап 0.1): music_playing — см. playback.py ────────────────
── FIX (этап 0.2): MUSIC_AVAILABLE ────────────────────────────────
── FIX (этап 0.5): warm_up_prefetch ───────────────────────────────
── FIX (этап 2.3): memory.start() явно ────────────────────────────

A2 (день 29): _handle_play: перед play_index — music.stop().

MOOD: _handle_play(name="mood") → client.metatag(mood_tag) → station_id.

DUCKING (день 29, вечер):
  - duck_on/duck_off → music.duck()/music.restore().
  - duck_on приходит от ASR (микрофон ВЫКЛ = TTS идёт).
  - check_duck_watchdog() при пустой очереди.

FIX (день 29, вечер, #2): _handle_play в отдельном потоке.

── LOCAL_LIKED (v6.5) ─────────────────────────────────────────────
── SEARCH ARTIST-FIRST (v6.8) ─────────────────────────────────────
"""

import sys
import threading
import time

from shared import ipc
from shared import protocol
from shared import config as cfg
from shared.log import log

from workers.music import state
from workers.music.player import music
from workers.music.playback import warm_up_prefetch


def _reset_playback_state():
    """Очистка playlist/queued/индекса."""
    with state.playlist_lock:
        state.playlist.clear()
        state.prefetched.clear()
    with state.queued_lock:
        state.queued_track_ids.clear()
    state.playlist_index = 0
    state.playlist_mode = None
    warm_up_prefetch()


def _stop_old_track():
    """A2: остановить старый трек перед новой командой play."""
    try:
        music.stop()
    except Exception as e:
        log(f"_stop_old_track: {e}", tag="MUSIC", level="WARN")
    _reset_playback_state()


def _tts_is_speaking_now() -> bool:
    """v8.4: проверить, говорит ли TTS СЕЙЧАС."""
    try:
        from workers.tts.state_file import get_state
        return bool(get_state().get("speaking", False))
    except Exception as e:
        log(f"music: tts state check: {e}", tag="MUSIC", level="DEBUG")
        return False


def _play_local_artist(artist_name: str, local_tracks: list) -> bool:
    """v8.9.1: играть локальные треки артиста (без Яндекса).

    playlist = [LocalTrack(path) for t in local_tracks[:20]]
    playlist_mode = "local_search" — sources.py уже умеет: при конце
    плейлиста докидывает похожие треки артиста из local_index, потом wave.
    Музыка не останавливается.
    """
    from workers.music.local import LocalTrack
    from workers.music import playback

    with state.playlist_lock:
        state.playlist.clear()
        state.prefetched.clear()
    with state.queued_lock:
        state.queued_track_ids.clear()
    state.playlist_index = 0

    added = 0
    for t in local_tracks[:20]:
        p = t.get("path", "")
        if not p:
            continue
        with state.playlist_lock:
            state.playlist.append(LocalTrack(p))
        added += 1

    if added == 0:
        return False

    # v8.9.1: sources.py уже умеет "local_search" — при конце плейлиста
    # докидывает похожие треки артиста из local_index, потом wave.
    state.playlist_mode = "local_search"
    playback.play_index(0)
    log(f"play_artist(«{artist_name}») → локально: {added} треков",
        tag="MUSIC")
    return True


def _play_mood(mood_tag: str):
    """Запустить станцию по настроению."""
    from shared import yandex
    from workers.music import modes, playback

    client = yandex.get_client()
    if client is None:
        log(f"play_mood({mood_tag}) — нет клиента Яндекса",
            tag="MUSIC", level="WARN")
        return

    try:
        mt = client.metatag(mood_tag)
    except Exception as e:
        log(f"play_mood({mood_tag}) metatag error: {e}",
            tag="MUSIC", level="WARN")
        return

    station_id = getattr(mt, "station_id", None)
    if not station_id:
        playlists = getattr(mt, "playlists", []) or []
        if playlists:
            first_title = getattr(playlists[0], "title", "<no title>")
            log(f"play_mood({mood_tag}) — нет station_id, "
                f"есть плейлист «{first_title}»",
                tag="MUSIC", level="WARN")
        else:
            log(f"play_mood({mood_tag}) — нет station_id и плейлистов",
                tag="MUSIC", level="WARN")
        return

    if modes.start_genre(station_id, mood_tag):
        playback.play_index(0)
        log(f"play_mood({mood_tag}) → start_genre({station_id})", tag="MUSIC")
    else:
        log(f"play_mood({mood_tag}) — start_genre({station_id}) не сработал",
            tag="MUSIC", level="WARN")


def _handle_play(name: str, data: dict):
    from workers.music import modes, radio, search, playback

    if not cfg.MUSIC_AVAILABLE:
        log(f"play({name}) — музыка недоступна (MUSIC_DIR отключён)",
            tag="MUSIC", level="WARN")
        return

    _stop_old_track()

    if name == "artist":
        artist_name = data.get("artist", "")
        if not artist_name:
            return

        # v8.9: сначала локально (быстро, без сети), потом Яндекс.
        from workers.music.local import search_local_artist_tracks
        local_tracks = search_local_artist_tracks(artist_name)
        if local_tracks:
            if _play_local_artist(artist_name, local_tracks):
                return

        # Яндекс fallback
        artist = search.search_artist(artist_name)
        if not artist:
            log(f"артист «{artist_name}» не найден", tag="MUSIC", level="WARN")
            return
        from workers.music.aliases import remember_alias
        remember_alias(artist_name, artist.id, artist.name)
        if modes.start_artist_only(artist):
            playback.play_index(0)

    elif name == "track":
        track_name = data.get("track", "")
        if not track_name:
            return
        track = search.search_track(track_name)
        if not track:
            log(f"трек «{track_name}» не найден", tag="MUSIC", level="WARN")
            return
        if modes.start_single_track(track):
            playback.play_index(0)

    elif name == "genre":
        genre = data.get("genre", "")
        station = data.get("station", "")
        if not genre:
            return
        if not station:
            from workers.music._shared import GENRE_ALIASES
            station = GENRE_ALIASES.get(genre, f"genre:{genre}")
        if modes.start_genre(station, genre):
            playback.play_index(0)

    elif name == "mood":
        mood_tag = data.get("mood", "")
        if not mood_tag:
            return
        _play_mood(mood_tag)

    elif name == "wave":
        language = data.get("language")
        if modes.queue_wave(language=language):
            playback.play_index(0)

    elif name == "liked":
        if modes.queue_liked_tracks():
            playback.play_index(0)

    elif name == "local_liked":
        if modes.queue_local_liked():
            playback.play_index(0)

    elif name == "search":
        query = data.get("query", "")
        if not query:
            return
        from workers.music.search import search_local, search_artist, search_track
        from workers.music.local import search_local_artist_tracks

        # v8.4: pre-duck ТОЛЬКО если TTS реально говорит СЕЙЧАС.
        if _tts_is_speaking_now():
            try:
                music.duck(30)
                log("search: pre-duck (TTS идёт)", tag="MUSIC", level="DEBUG")
            except Exception as e:
                log(f"search: duck pre-play: {e}", tag="MUSIC", level="WARN")
        else:
            log("search: pre-duck пропущен (TTS не говорит)",
                tag="MUSIC", level="DEBUG")

        # ── 1. ЛОКАЛЬНЫЙ АРТИСТ (v8.9) ──
        local_artist_tracks = search_local_artist_tracks(query)
        if local_artist_tracks:
            if _play_local_artist(query, local_artist_tracks):
                return

        # ── 2. ЛОКАЛЬНЫЙ трек ──
        local = search_local(query)
        if local:
            from workers.music.local import LocalTrack, _load_index_sync
            with state.playlist_lock:
                state.playlist.clear()
                state.prefetched.clear()
            with state.queued_lock:
                state.queued_track_ids.clear()
            state.playlist_index = 0
            with state.playlist_lock:
                state.playlist.append(LocalTrack(local["path"]))
            state.playlist_mode = "local_search"

            try:
                data_idx = _load_index_sync()
                all_tracks = data_idx.get("tracks", []) if isinstance(data_idx, dict) else []
                artist_name = local.get("artist", "").lower()
                added = 0
                existing = {local["path"]}
                for t in all_tracks:
                    if t.get("artist", "").lower() != artist_name:
                        continue
                    p = t.get("path", "")
                    if not p or p in existing:
                        continue
                    with state.playlist_lock:
                        state.playlist.append(LocalTrack(p))
                    existing.add(p)
                    added += 1
                    if added >= 10:
                        break
                log(f"search: добавил {added} треков {artist_name} в playlist",
                    tag="MUSIC")
            except Exception as e:
                log(f"search: похожие: {e}", tag="MUSIC", level="WARN")

            playback.play_index(0)
            log(f"play(search) «{query}» → локально: "
                f"{local.get('artist')} — {local.get('title')}", tag="MUSIC")
            return

        # ── 3. ЯНДЕКС артист ──
        artist = search_artist(query)
        if artist:
            from workers.music.aliases import remember_alias
            remember_alias(query, artist.id, artist.name)
            if modes.start_artist_only(artist):
                playback.play_index(0)
                log(f"play(search) «{query}» → артист: {artist.name}",
                    tag="MUSIC")
                return

        # ── 4. ЯНДЕКС трек ──
        track = search_track(query)
        if track:
            if modes.start_single_track(track):
                playback.play_index(0)
            log(f"play(search) «{query}» → яндекс: {track.title}", tag="MUSIC")
            return
        log(f"play(search) «{query}» — не найдено", tag="MUSIC", level="WARN")

    elif name == "radio":
        station_key = data.get("station", "")
        if not station_key:
            return
        if radio.start_radio(station_key):
            log(f"radio {station_key} → start_radio ok", tag="MUSIC")
        else:
            log(f"radio {station_key} — start_radio не сработал",
                tag="MUSIC", level="WARN")


def _run_play_async(name: str, data: dict):
    """Выполнить _handle_play в фоне под флагом state.switching."""
    state.switching = True
    try:
        _handle_play(name, data)
    except Exception as e:
        log(f"_handle_play({name}) error: {e}", tag="MUSIC", level="ERROR")
        try:
            music.release_duck()
        except Exception:
            pass
    finally:
        state.switching = False


def _level_to_percent(level) -> int:
    f = float(level)
    if 0.0 <= f <= 1.0:
        return max(0, min(100, int(round(f * 100))))
    if 0.0 <= f <= 100.0:
        return max(0, min(100, int(round(f))))
    return 0


def _handle_control(name: str, data: dict):
    from workers.music import playback

    if name == "next":
        playback.play_next(from_finish=False)
    elif name == "prev":
        playback.play_prev()
    elif name == "pause":
        music.pause()
        music.mark_user_paused()
    elif name == "resume":
        music.resume()
    elif name == "stop":
        try:
            music.stop()
        except Exception as e:
            log(f"stop: {e}", tag="MUSIC", level="WARN")
        music.mark_user_paused()
        _reset_playback_state()
        ipc.put(protocol.BRAIN_INBOX, protocol.event_music_stopped())
    elif name == "volume_up":
        music.adjust_volume(+10)
    elif name == "volume_down":
        music.adjust_volume(-10)
    elif name == "volume_set":
        val = data.get("value")
        if val is None:
            log("volume_set без value", tag="MUSIC", level="WARN")
        else:
            music.set_volume(val)
    elif name == "hold_duck":
        music.hold_duck()
    elif name == "release_duck":
        music.release_duck()
    elif name == "duck_on":
        level = data.get("level")
        if level is None:
            music.duck()
        else:
            try:
                target = _level_to_percent(level)
            except (TypeError, ValueError) as e:
                log(f"duck_on bad level={level!r}: {e}",
                    tag="MUSIC", level="WARN")
                return
            music.duck(target)
    elif name == "duck_off":
        music.restore()
    elif name == "dislike":
        track_id = state.current_track_id
        title = state.current_track_title or ""
        if track_id:
            from memory import memory
            memory.blacklist_track(track_id, reason='user_dislike')
            log(f"blacklist {track_id} — «{title}»", tag="MUSIC")
            playback.play_next(from_finish=False)
        else:
            log("dislike — нет текущего трека", tag="MUSIC", level="WARN")
    elif name == "now_playing_request":
        title = state.current_track_title or ""
        if " — " in title:
            parts = title.split(" — ", 1)
            t_title = parts[0].strip()
            t_artist = parts[1].strip()
        else:
            t_title = title
            t_artist = ""
        ipc.put(protocol.BRAIN_INBOX, protocol.event_now_playing(t_artist, t_title))


def main():
    log("music: старт", tag="MUSIC")
    ipc.init()

    try:
        from memory import memory
        memory.start()
        log("music: memory writer запущен", tag="MUSIC")
    except Exception as e:
        log(f"music: memory start: {e}", tag="MUSIC", level="WARN")

    if not cfg.MUSIC_AVAILABLE:
        log(f"music: MUSIC_DIR недоступен ({cfg.MUSIC_DIR}). "
            f"Воркер работает вхолостую: команды принимает, play — отказ.",
            tag="MUSIC", level="WARN")

    try:
        from workers.music.playback import _get_music_instance
        _get_music_instance()
        log("music: VLC instance прогрет", tag="MUSIC")
    except Exception as e:
        log(f"music: VLC warmup: {e}", tag="MUSIC", level="WARN")

    try:
        from workers.music.playback import clear_music_playing
        clear_music_playing()
        log("music: сбросил music_playing при старте", tag="MUSIC")
    except Exception as e:
        log(f"music: clear music_playing: {e}", tag="MUSIC", level="WARN")

    if cfg.MUSIC_AVAILABLE:
        from shared import yandex
        if yandex.init():
            state.client = yandex.get_client()
            state.TOKEN = yandex.get_token()
            log("music: Яндекс.Музыка подключена", tag="MUSIC")
        else:
            log("music: Яндекс.Музыка НЕ подключена",
                tag="MUSIC", level="WARN")
    else:
        log("music: Яндекс.Музыка не инициализируется (MUSIC_DIR недоступен)",
            tag="MUSIC", level="WARN")

    from workers.music.playback import monitor_player
    threading.Thread(target=monitor_player, daemon=True).start()
    log("music: monitor_player запущен", tag="MUSIC")

    threading.Thread(target=warm_up_prefetch, daemon=True).start()

    log("music: слушаю music_inbox", tag="MUSIC")

    inbox_timeout = getattr(cfg, "MUSIC_INBOX_TIMEOUT", 0.05)
    duck_max_sec = float(getattr(cfg, "DUCK_MAX_SEC", 15.0))

    while True:
        msg = ipc.get(protocol.MUSIC_INBOX, timeout=inbox_timeout)
        if msg is None:
            music.check_duck_watchdog(max_sec=duck_max_sec)
            continue

        try:
            if msg.type == "play":
                threading.Thread(
                    target=_run_play_async,
                    args=(msg.name, msg.data),
                    daemon=True,
                ).start()
            elif msg.type == "control":
                if msg.name in ("next", "prev", "dislike"):
                    state.switching = True
                    try:
                        _handle_control(msg.name, msg.data)
                    finally:
                        state.switching = False
                else:
                    _handle_control(msg.name, msg.data)
            else:
                log(f"неизвестный тип {msg.type}", tag="MUSIC", level="WARN")
        except Exception as e:
            log(f"ошибка обработки {msg.type}/{msg.name}: {e}",
                tag="MUSIC", level="ERROR")
            try:
                music.release_duck()
            except Exception:
                pass


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("music: остановка по Ctrl+C", tag="MUSIC")
        sys.exit(0)