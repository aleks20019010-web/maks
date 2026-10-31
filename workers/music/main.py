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

MOOD (новое):
  - _handle_play(name="mood") → client.metatag(mood_tag) → station_id
    (mood:energetic) → modes.start_genre(station_id, mood_tag).
  - modes.start_genre уже умеет играть любую станцию по station_id
    (внутри — refill_one → rotor_station_tracks). Название «genre» —
    историческое, но работает и для mood.
  - НЕ используем client.station() — такого метода нет в yandex-music.
  - НЕ используем radio.start_radio() — он для живых потоков из
    radio_stations.json, а mood-станция — не поток.

DUCKING (день 29, вечер):
  - duck_on/duck_off → music.duck()/music.restore() — приглушение
    громкости, а не пауза.
  - duck_on приходит от ASR (микрофон ВЫКЛ = TTS идёт). TTS worker
    больше НЕ шлёт duck_on/duck_off.
  - timeout ipc.get — cfg.MUSIC_INBOX_TIMEOUT (0.05), чтобы duck_on
    применялся быстро.
  - check_duck_watchdog() вызывается в главном цикле при пустой
    очереди — снимает залипший ducking.

FIX (день 29, вечер):
  - _stop_old_track: убран mark_user_paused().
  - _handle_control("resume"): убран mark_user_paused().
  - _reset_playback_state(): общий блок.
  - resume: _set_music_playing() только если есть плеер.
  - check_duck_watchdog в главном цикле.

FIX (день 29, вечер, #2): _handle_play в отдельном потоке.
  Раньше: play_wave/start_genre/play_mood грузят трек 5–10 сек
  синхронно в главном цикле — music_inbox НЕ читается. duck_on от ASR
  лежит в очереди до конца загрузки. В логе 15:39: duck_on пришёл в
  15:39:21 (микрофон ВЫКЛ), а music обработал его в 15:39:31 — ducking
  опоздал на 10 сек, TTS уже сказал фразу.
  Теперь: _handle_play в threading.Thread(daemon=True). music_inbox
  читается сразу, duck_on/duck_off обрабатываются в пределах 0.05 сек.
  state.switching ставится/снимается внутри потока — monitor_player
  по-прежнему не дёргает play_next во время переключения.
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


_MUSIC_PLAYING_FLAG = cfg.DATA_DIR / "music_playing.flag"


def _set_music_playing():
    try:
        _MUSIC_PLAYING_FLAG.parent.mkdir(parents=True, exist_ok=True)
        _MUSIC_PLAYING_FLAG.write_text(str(time.time()), encoding="utf-8")
    except Exception as e:
        log(f"music: set music_playing.flag: {e}", tag="MUSIC", level="WARN")


def _clear_music_playing():
    try:
        if _MUSIC_PLAYING_FLAG.exists():
            _MUSIC_PLAYING_FLAG.unlink()
    except Exception as e:
        log(f"music: clear music_playing.flag: {e}", tag="MUSIC", level="WARN")


def _reset_playback_state():
    """Очистка playlist/queued/индекса + снятие music_playing.flag.

    Общий блок для _stop_old_track() и _handle_control("stop").
    Ducking-флаги НЕ трогает.
    """
    with state.playlist_lock:
        state.playlist.clear()
        state.prefetched.clear()
    with state.queued_lock:
        state.queued_track_ids.clear()
    state.playlist_index = 0
    state.playlist_mode = None
    _clear_music_playing()


def _stop_old_track():
    """A2: остановить старый трек перед новой командой play.

    ducking-флаги НЕ сбрасываем: если TTS ещё говорит, MusicManager.set_player
    создаст новый плеер сразу приглушённым.
    """
    try:
        music.stop()
    except Exception as e:
        log(f"music: _stop_old_track: {e}", tag="MUSIC", level="WARN")
    _reset_playback_state()


def _play_mood(mood_tag: str):
    """Запустить станцию по настроению.

    mood_tag — tag метатега («Энергичное»).
    """
    from shared import yandex
    from workers.music import modes, playback

    client = yandex.get_client()
    if client is None:
        log(f"music: play_mood({mood_tag}) — нет клиента Яндекса", tag="MUSIC", level="WARN")
        return

    try:
        mt = client.metatag(mood_tag)
    except Exception as e:
        log(f"music: play_mood({mood_tag}) metatag error: {e}", tag="MUSIC", level="WARN")
        return

    station_id = getattr(mt, "station_id", None)
    if not station_id:
        playlists = getattr(mt, "playlists", []) or []
        if playlists:
            first_title = getattr(playlists[0], "title", "<no title>")
            log(
                f"music: play_mood({mood_tag}) — нет station_id, "
                f"есть плейлист «{first_title}», но modes не умеет play_playlist",
                tag="MUSIC", level="WARN",
            )
        else:
            log(f"music: play_mood({mood_tag}) — нет station_id и плейлистов",
                tag="MUSIC", level="WARN")
        return

    if modes.start_genre(station_id, mood_tag):
        playback.play_index(0)
        log(f"music: play_mood({mood_tag}) → start_genre({station_id})", tag="MUSIC")
    else:
        log(
            f"music: play_mood({mood_tag}) — start_genre({station_id}) не сработал",
            tag="MUSIC", level="WARN",
        )


def _handle_play(name: str, data: dict):
    from workers.music import modes, radio, search, playback

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

    elif name == "radio":
        station_key = data.get("station", "")
        if not station_key:
            return
        if radio.start_radio(station_key):
            log(f"music: radio {station_key} → start_radio ok", tag="MUSIC")
        else:
            log(f"music: radio {station_key} — start_radio не сработал",
                tag="MUSIC", level="WARN")


def _run_play_async(name: str, data: dict):
    """Выполнить _handle_play в фоне под флагом state.switching.

    music_inbox продолжает читаться: duck_on/duck_off от ASR
    обрабатываются в пределах MUSIC_INBOX_TIMEOUT (0.05 сек),
    пока загрузка трека идёт.
    """
    state.switching = True
    try:
        _handle_play(name, data)
    except Exception as e:
        log(f"music: _handle_play({name}) error: {e}",
            tag="MUSIC", level="ERROR")
        try:
            music.release_duck()
        except Exception:
            pass
    finally:
        state.switching = False


def _level_to_percent(level) -> int:
    """data["level"] из duck_on — доля 0.0–1.0. Конвертируем в 0..100.

    ASR шлёт duck_on БЕЗ level → None → вызывающий берёт дефолт.
    """
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
        _clear_music_playing()
    elif name == "resume":
        music.resume()
        if music.has_player():
            _set_music_playing()
    elif name == "stop":
        try:
            music.stop()
        except Exception as e:
            log(f"music: stop: {e}", tag="MUSIC", level="WARN")
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
            log("music: volume_set без value", tag="MUSIC", level="WARN")
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
                log(f"music: duck_on bad level={level!r}: {e}",
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
            log(f"music: blacklist {track_id} — «{title}»", tag="MUSIC")
            playback.play_next(from_finish=False)
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

    try:
        if _MUSIC_PLAYING_FLAG.exists():
            _MUSIC_PLAYING_FLAG.unlink()
            log("music: удалил залипший music_playing.flag",
                tag="MUSIC", level="WARN")
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

    inbox_timeout = getattr(cfg, "MUSIC_INBOX_TIMEOUT", 0.05)
    duck_max_sec = float(getattr(cfg, "DUCK_MAX_SEC", 60.0))

    while True:
        msg = ipc.get(protocol.MUSIC_INBOX, timeout=inbox_timeout)
        if msg is None:
            music.check_duck_watchdog(max_sec=duck_max_sec)
            continue

        try:
            if msg.type == "play":
                # FIX: в отдельном потоке, чтобы music_inbox не блокировался
                # на время загрузки трека (play_wave/start_genre/play_mood).
                # duck_on/duck_off от ASR обрабатываются сразу.
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
                log(f"music: неизвестный тип {msg.type}", tag="MUSIC", level="WARN")
        except Exception as e:
            log(f"music: ошибка обработки {msg.type}/{msg.name}: {e}",
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