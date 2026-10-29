"""
workers/music/main.py — Music worker.

Принимает команды из music_inbox, выполняет.

A2 (день 29):
  - music_playing.flag — пишем при start (play_index), удаляем при
    stop / pause / music_stopped. При старте удаляем залипший флаг
    (музыка не играет после рестарта).
  - _handle_play: перед play_index — music.stop() (старый трек СТОП,
    не пауза). TTS уже был до (brain _dispatch). Музыка стартует после
    TTS. Старый трек не возобновляется. TTS не поверх музыки.
  - Убран music.hold_duck() из _handle_play (stop достаточно).
"""

import sys
import threading

from shared import ipc
from shared import protocol
from shared import config as cfg
from shared.log import log

from workers.music import state
from workers.music.player import music


_MUSIC_PLAYING_FLAG = cfg.DATA_DIR / "music_playing.flag"


def _set_music_playing():
    try:
        _MUSIC_PLAYING_FLAG.parent.mkdir(parents=True, exist_ok=True)
        _MUSIC_PLAYING_FLAG.write_text(str(__import__("time").time()), encoding="utf-8")
    except Exception as e:
        log(f"music: set music_playing.flag: {e}", tag="MUSIC", level="WARN")


def _clear_music_playing():
    try:
        if _MUSIC_PLAYING_FLAG.exists():
            _MUSIC_PLAYING_FLAG.unlink()
    except Exception as e:
        log(f"music: clear music_playing.flag: {e}", tag="MUSIC", level="WARN")


def _stop_old_track():
    """A2: остановить старый трек перед новой командой play.

    stop (не pause) — старый трек не возобновляется. _clear_music_playing —
    флаг снят. TTS уже был до (brain _dispatch), музыка стартует после TTS.
    """
    try:
        music.stop()
        music.mark_user_paused()
    except Exception as e:
        log(f"music: _stop_old_track: {e}", tag="MUSIC", level="WARN")
    with state.playlist_lock:
        state.playlist.clear()
        state.prefetched.clear()
    with state.queued_lock:
        state.queued_track_ids.clear()
    state.playlist_index = 0
    state.playlist_mode = None
    _clear_music_playing()


def _handle_play(name: str, data: dict):
    from workers.music import modes, radio, search, playback

    # A2: старый трек — СТОП (не пауза). TTS уже был до.
    _stop_old_track()

    if name == "artist":
        artist_name = data.get("artist", "")
        if not artist_name:
            return
        artist = search.search_artist(artist_name)
        if not artist:
            log(f"music: артист «{artist_name}» не найден", tag="MUSIC", level="WARN")
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
            log(f"music: трек «{track_name}» не найден", tag="MUSIC", level="WARN")
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

    elif name == "wave":
        language = data.get("language")
        if modes.queue_wave(language=language):
            playback.play_index(0)

    elif name == "liked":
        if modes.queue_liked_tracks():
            playback.play_index(0)

    elif name == "radio":
        station_key = data.get("station", "")
        if station_key and radio.start_radio(station_key):
            pass


def _handle_control(name: str, data: dict):
    from workers.music import playback

    if name == "next":
        playback.play_next(from_finish=False)
    elif name == "prev":
        playback.play_prev()
    elif name == "pause":
        music.pause()
        music.mark_user_paused()
        _clear_music_playing()
    elif name == "resume":
        music.resume()
        music.mark_user_paused()
        _set_music_playing()
    elif name == "stop":
        music.stop()
        music.mark_user_paused()
        with state.playlist_lock:
            state.playlist.clear()
            state.prefetched.clear()
        with state.queued_lock:
            state.queued_track_ids.clear()
        state.playlist_index = 0
        state.playlist_mode = None
        _clear_music_playing()
        ipc.put(protocol.BRAIN_INBOX, protocol.event_music_stopped())
    elif name == "volume_up":
        music.adjust_volume(+10)
    elif name == "volume_down":
        music.adjust_volume(-10)
    elif name == "volume_set":
        val = data.get("value", 50)
        music.set_volume(val)
    elif name == "hold_duck":
        music.hold_duck()
    elif name == "release_duck":
        music.release_duck()
    elif name == "duck_on":
        music.duck()
    elif name == "duck_off":
        music.restore()
    elif name == "dislike":
        track_id = state.current_track_id
        title = state.current_track_title or ""
        if track_id:
            from memory import memory
            memory.blacklist_track(track_id, reason='user_dislike')
            log(f"music: blacklist {track_id} — «{title}»", tag="MUSIC")
            log(f"music: dislike → play_next", tag="MUSIC")
            playback.play_next(from_finish=False)
            log(f"music: dislike → play_next done", tag="MUSIC")
        else:
            log("music: dislike — нет текущего трека", tag="MUSIC", level="WARN")
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

    # A2: при старте флаг music_playing невалиден (музыка не играет).
    try:
        if _MUSIC_PLAYING_FLAG.exists():
            _MUSIC_PLAYING_FLAG.unlink()
            log("music: удалил залипший music_playing.flag", tag="MUSIC", level="WARN")
    except Exception as e:
        log(f"music: clear music_playing.flag: {e}", tag="MUSIC", level="WARN")

    from shared import yandex
    if yandex.init():
        state.client = yandex.get_client()
        state.TOKEN = yandex.get_token()
        log("music: Яндекс.Музыка подключена", tag="MUSIC")
    else:
        log("music: Яндекс.Музыка НЕ подключена", tag="MUSIC", level="WARN")

    from workers.music.playback import monitor_player
    threading.Thread(target=monitor_player, daemon=True).start()
    log("music: monitor_player запущен", tag="MUSIC")

    log("music: слушаю music_inbox", tag="MUSIC")

    while True:
        msg = ipc.get(protocol.MUSIC_INBOX, timeout=1.0)
        if msg is None:
            continue

        try:
            if msg.type == "play":
                state.switching = True
                try:
                    _handle_play(msg.name, msg.data)
                finally:
                    state.switching = False
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
                log(f"music: неизвестный тип {msg.type}", tag="MUSIC", level="WARN")
        except Exception as e:
            log(f"music: ошибка обработки {msg.type}/{msg.name}: {e}", tag="MUSIC", level="ERROR")
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