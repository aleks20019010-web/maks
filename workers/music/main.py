"""
workers/music/main.py — Music worker.

Принимает команды из music_inbox, выполняет.
VLC реальный. Очередь, источники, скачивание — здесь.

TTS-duck: TTS worker сам шлёт duck_on / duck_off в music_inbox.
  duck_on  → music.duck() (pause)
  duck_off → music.restore() (resume)

Dislike: brain шлёт dislike → music.blacklist текущего трека + skip.
  Трек убирается сразу, в автовыборе больше не появится.

Защита от гонки: state.switching=True вокруг _handle_play / next / prev / dislike,
чтобы monitor_player не дёргал play_next, пока идёт переключение.
"""

import sys
import threading

from shared import ipc
from shared import protocol
from shared.log import log

from workers.music import state
from workers.music.player import music


# ── Обработчики команд ────────────────────────────────────────
def _handle_play(name: str, data: dict):
    from workers.music import modes, radio, search, playback

    music.hold_duck()

    if name == "artist":
        artist_name = data.get("artist", "")
        if not artist_name:
            music.release_duck()
            return
        artist = search.search_artist(artist_name)
        if not artist:
            log(f"music: артист «{artist_name}» не найден", tag="MUSIC", level="WARN")
            music.release_duck()
            return
        from workers.music.aliases import remember_alias
        remember_alias(artist_name, artist.id, artist.name)
        if modes.start_artist_only(artist):
            playback.play_index(0)
        else:
            music.release_duck()

    elif name == "track":
        track_name = data.get("track", "")
        if not track_name:
            music.release_duck()
            return
        track = search.search_track(track_name)
        if not track:
            log(f"music: трек «{track_name}» не найден", tag="MUSIC", level="WARN")
            music.release_duck()
            return
        if modes.start_single_track(track):
            playback.play_index(0)
        else:
            music.release_duck()

    elif name == "genre":
        genre = data.get("genre", "")
        station = data.get("station", "")
        if not genre:
            music.release_duck()
            return
        if not station:
            from workers.music._shared import GENRE_ALIASES
            station = GENRE_ALIASES.get(genre, f"genre:{genre}")
        if modes.start_genre(station, genre):
            playback.play_index(0)
        else:
            music.release_duck()

    elif name == "wave":
        language = data.get("language")
        if modes.queue_wave(language=language):
            playback.play_index(0)
        else:
            music.release_duck()

    elif name == "liked":
        if modes.queue_liked_tracks():
            playback.play_index(0)
        else:
            music.release_duck()

    elif name == "radio":
        station_key = data.get("station", "")
        if station_key and radio.start_radio(station_key):
            pass
        else:
            music.release_duck()


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
        music.mark_user_paused()
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
            # Пропустить трек сразу
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


# ── Главный цикл ──────────────────────────────────────────────
def main():
    log("music: старт", tag="MUSIC")
    ipc.init()

    # Яндекс
    from shared import yandex
    if yandex.init():
        state.client = yandex.get_client()
        state.TOKEN = yandex.get_token()
        log("music: Яндекс.Музыка подключена", tag="MUSIC")
    else:
        log("music: Яндекс.Музыка НЕ подключена", tag="MUSIC", level="WARN")

    # Monitor player (Ended → play_next)
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
                # next / prev / dislike — под switching (создают плеер / skip)
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