"""
workers/music/main.py — Music worker.

Принимает команды из music_inbox, выполняет.

── FIX (этап 0.1): music_playing через runtime_state ──────────────
  Раньше _set_music_playing / _clear_music_playing были СКОПИРОВАНЫ
  здесь и в playback.py — два места писали один флаг по-разному.
  Теперь: set_music_playing / clear_music_playing импортируются
  из playback.py (единственный источник правды).

── FIX (этап 0.2): MUSIC_AVAILABLE ────────────────────────────────
  Если внешний диск (E:\\Music) отключён, cfg.MUSIC_AVAILABLE = False.
  Music-воркер при старте это видит, логирует WARN и продолжает
  работать «вхолостую»: команды принимает, но на play отвечает в лог
  «музыка недоступна». Не выходим через sys.exit(1) — иначе supervisor
  увидит returncode != 0 и будет рестартить в цикле, забивая лог.

── FIX (этап 0.5): warm_up_prefetch — первый трек готов заранее ──
  _reset_playback_state (из _stop_old_track, _handle_control("stop"))
  вызывает warm_up_prefetch СИНХРОННО (0.21 сек): get_local_random_track
  + _append_and_prefetch → playlist[0] = локальный. Дальше queue_wave
  (modes.py) НЕ чистит playlist и НЕ делает refill_one — play_index(0)
  берёт готовый локальный → ▶ за 0.5 сек.
  Синхронно, не в потоке: если в потоке, play_index(0) может успеть
  раньше, чем warm_up положит трек → «Очередь закончилась». 0.21 сек
  в _reset_playback_state — приемлемо (не 10 секунд).

A2 (день 29):
  - music_playing — пишем при start (play_index), удаляем при
    stop / pause / music_stopped. При старте — clear.
  - _handle_play: перед play_index — music.stop() (старый трек СТОП,
    не пауза). TTS уже был до (brain _dispatch). Музыка стартует после
    TTS. Старый трек не возобновляется.

MOOD:
  - _handle_play(name="mood") → client.metatag(mood_tag) → station_id
    (mood:energetic) → modes.start_genre(station_id, mood_tag).
  - НЕ используем client.station() — такого метода нет в yandex-music.
  - НЕ используем radio.start_radio() — он для живых потоков.

DUCKING (день 29, вечер):
  - duck_on/duck_off → music.duck()/music.restore().
  - duck_on приходит от ASR (микрофон ВЫКЛ = TTS идёт).
  - timeout ipc.get — cfg.MUSIC_INBOX_TIMEOUT (0.05).
  - check_duck_watchdog() при пустой очереди — снимает залипший ducking.

FIX (день 29, вечер, #2): _handle_play в отдельном потоке.
  Раньше: play_wave/start_genre/play_mood грузят трек 5–10 сек
  синхронно в главном цикле — music_inbox НЕ читается. duck_on от ASR
  лежит в очереди до конца загрузки.
  Теперь: _handle_play в threading.Thread(daemon=True).
  music_inbox читается сразу.

── FIX (этап 0.5): print → log(tag="MUSIC") ────────────────────
  Раньше main.py уже использовал log, но проверил — да, всё ок.
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
# FIX (этап 0.1): единственный источник правды для music_playing.
# FIX (этап 0.5): warm_up_prefetch — первый трек готов заранее.
from workers.music.playback import (
    set_music_playing, clear_music_playing, warm_up_prefetch,
)


def _reset_playback_state():
    """Очистка playlist/queued/индекса + снятие music_playing.

    FIX (этап 0.5): после очистки — warm_up_prefetch СИНХРОННО
    (0.21 сек): get_local_random_track + _append_and_prefetch →
    playlist[0] = локальный. Дальше queue_wave (modes.py) НЕ чистит
    playlist — play_index(0) берёт готовый.

    Синхронно, не в потоке: если в потоке, play_index(0) может успеть
    раньше, чем warm_up положит трек → «Очередь закончилась».

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
    clear_music_playing()
    # FIX (этап 0.5): синхронный warm_up — см. docstring модуля.
    warm_up_prefetch()


def _stop_old_track():
    """A2: остановить старый трек перед новой командой play.

    ducking-флаги НЕ сбрасываем: если TTS ещё говорит, MusicManager.set_player
    создаст новый плеер сразу приглушённым.
    """
    try:
        music.stop()
    except Exception as e:
        log(f"_stop_old_track: {e}", tag="MUSIC", level="WARN")
    _reset_playback_state()


def _play_mood(mood_tag: str):
    """Запустить станцию по настроению.

    mood_tag — tag метатега («Энергичное»).
    """
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
                f"есть плейлист «{first_title}», но modes не умеет play_playlist",
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

    # FIX (этап 0.2): если MUSIC_DIR недоступен — не пытаемся качать/играть.
    if not cfg.MUSIC_AVAILABLE:
        log(f"play({name}) — музыка недоступна (MUSIC_DIR отключён)",
            tag="MUSIC", level="WARN")
        return

    _stop_old_track()

    if name == "artist":
        artist_name = data.get("artist", "")
        if not artist_name:
            return
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
    """Выполнить _handle_play в фоне под флагом state.switching.

    music_inbox продолжает читаться: duck_on/duck_off от ASR
    обрабатываются в пределах MUSIC_INBOX_TIMEOUT (0.05 сек),
    пока загрузка трека идёт.
    """
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
        clear_music_playing()
    elif name == "resume":
        music.resume()
        if music.has_player():
            set_music_playing()
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

    # FIX (этап 0.2): MUSIC_DIR мог быть недоступен при импорте config.py.
    if not cfg.MUSIC_AVAILABLE:
        log(f"music: MUSIC_DIR недоступен ({cfg.MUSIC_DIR}). "
            f"Воркер работает вхолостую: команды принимает, play — отказ.",
            tag="MUSIC", level="WARN")

    # FIX (2026-11-08): прогреть VLC instance — первый _get_music_instance
    # создаёт vlc.Instance (2 сек). Иначе первый «включи музыку» ждёт 2 сек.
    try:
        from workers.music.playback import _get_music_instance
        _get_music_instance()
        log("music: VLC instance прогрет", tag="MUSIC")
    except Exception as e:
        log(f"music: VLC warmup: {e}", tag="MUSIC", level="WARN")

    # FIX (этап 0.1): залипший music_playing мог остаться от прошлого запуска.
    clear_music_playing()
    log("music: сбросил music_playing при старте", tag="MUSIC")

    # FIX (этап 0.2): если MUSIC_DIR нет — yandex.init() не пытаемся.
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

    # FIX (этап 0.5): warm_up_prefetch при старте — в фоне.
    # К моменту первой команды playlist[0] уже локальный.
    # В фоне (не синхронно): старт не ждёт 0.21 сек. Если пользователь
    # скажет «включи» сразу — _reset_playback_state сделает warm_up
    # синхронно (см. выше). Двойной вызов безопасен (идемпотентно).
    threading.Thread(target=warm_up_prefetch, daemon=True).start()

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