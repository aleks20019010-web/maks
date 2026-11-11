# workers/music/playback.py
# Воспроизведение: play_index, play_next, play_prev, monitor_player.
# Плюс refill_one / refill_to_target / warm_up_prefetch.
#
# VLC реальный. Music worker играет.
#
# ── A2 (день 29): music_playing — живёт в shared/runtime_state ──
#
# ── FIX (этап 0.5): warm_up_prefetch ─────────────────────────────
#   При старте music (и после _reset_playback_state) кладёт локальный
#   трек в playlist[0] заранее — play_index(0) не ищет 10 сек.
#
# ── FIX (этап 0.5): print → log(tag="MUSIC") ────────────────────
#
# ── FIX (этап 2.6): music_playing — heartbeat, один писатель ────
#   Симптом: hot words («дальше», «стоп») без wake работали первые
#   30 сек трека, потом — только с wake. Причина: TTL_MUSIC_PLAYING
#   = 30 сек, а set_music_playing() вызывался только в play_index
#   (старт трека). Трек 3 мин → флаг протухал через 30 сек → ASR
#   _is_music_playing() = False → hot words не шли в brain.
#
#   Архитектурно правильно: music_playing — heartbeat.
#   Писатели (только в этом файле):
#     - play_index — ИНИЦИАЛИЗАЦИЯ: set_music_playing() после
#       mark_ready (первый heartbeat, hot words в первую секунду).
#     - monitor_player — ПОДДЕРЖАНИЕ: пока state == Playing, раз в
#       MUSIC_HEARTBEAT_SEC продлевает TTL. При != Playing
#       (Paused/Stopped/Ended/Opening/Buffering/Error/None) —
#       clear_music_playing() (если флаг был).
#   Music упал → monitor_player умер → TTL протухнет за
#   TTL_MUSIC_PLAYING (= 3 × heartbeat). Music играет → heartbeat →
#   флаг жив → ASR hot words без wake.
#
#   main.py / _reset_playback_state / play_next / play_prev — флаг НЕ
#   трогают. Только управляют плеером (pause/stop/resume/next/prev).
#   monitor_player видит результат и обновляет флаг. Единственный
#   источник правды по поддержанию — monitor_player.
#
#   TTL выводится из heartbeat: runtime_state.TTL_MUSIC_PLAYING =
#   MUSIC_HEARTBEAT_SEC * 3. Не магическое число.
#
# ── LIKED_TRACK (v6.5) ──────────────────────────────────────────
#   В play_index добавлена ветка isinstance(track, LikedTrack) ПЕРЕД
#   LocalTrack. LikedTrack — ленивая ссылка на лайк (artist, title, id).
#     - сначала search_local(artist + title) → локальный файл;
#     - нет локального → download_by_id(track.id) → файл.
#     - не скачался → skip (idx += 1, attempts += 1).
#     - memory.remember_play(...) один раз после выбора файла.
#   Импорт: from workers.music.local import LocalTrack, LikedTrack.
#
# ── LOCAL_SEARCH_NEXT (2026-11-11) ──────────────────────────────
#   play_next: при переходе «дальше» из режимов local_search / single
#   сбрасывает playlist_mode в "local_search_next" ДО play_index.
#   Зачем: brain/main.py в music_started говорит PLAY_SEARCH_FOUND
#   только для mode in ("single", "local_search"). Если после
#   play_search (mode="local_search") пользователь нажал «дальше»,
#   без сброса режима TTS снова сказал бы «Нашёл: {artist} — {title}».
#   С "local_search_next" TTS молчит (не входит в список).

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

# FIX (этап 2.6): heartbeat music_playing. Единый источник — runtime_state.
# monitor_player продлевает флаг раз в MUSIC_HEARTBEAT_SEC, пока Playing.
# TTL = 3 × heartbeat (30 сек), с запасом на 2 пропущенных тика.
MUSIC_HEARTBEAT_SEC = rt.MUSIC_HEARTBEAT_SEC


# ── FIX (2026-11-07): явный vlc.Instance для music ────────────────
_music_instance = None


def _get_music_instance():
    global _music_instance
    if _music_instance is None:
        _music_instance = vlc.Instance(
            "--no-video",
            "--no-osd",
            "--quiet",
            "--aout=mmdevice",
        )
        log("vlc.Instance создан (music, изолирован от TTS)",
            tag="MUSIC", level="DEBUG")
    return _music_instance


# ── FIX (этап 0.1): music_playing через runtime_state ─────────────
# Единственные вызывающие — play_index (инициализация) и
# monitor_player (поддержание/снятие). См. шапку модуля.
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


# ── FIX (этап 0.5): warm_up_prefetch ────────────────────────
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
    """Воспроизвести трек по индексу. Под state.play_lock.

    Возвращает True, если трек реально начал играть, False — если не удалось.

    FIX (этап 2.6): флаг music_playing ставит ТОЛЬКО этот метод (после
    mark_ready) и monitor_player (heartbeat). При неудаче — НЕ сбрасываем
    здесь: monitor_player увидит != Playing (Ended/None) и сбросит.
    """
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

            # ── LikedTrack (лайк, ленивый) ──────────────────────
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
            # FIX (этап 2.6): ИНИЦИАЛИЗАЦИЯ флага. Первый heartbeat —
            # hot words в первую секунду. Дальше monitor_player раз в
            # MUSIC_HEARTBEAT_SEC продлевает TTL (пока Playing).
            set_music_playing()

            state.current_track_title = title
            state.current_track_id = track.id
            state.playlist_index = idx
            state.last_play_start_time = time.time()
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
    """Перейти к следующему треку.

    FIX (этап 2.6): флаг music_playing НЕ трогаем — monitor_player увидит
    результат (Playing → set, Ended/None → clear).

    FIX (2026-11-11): при переходе «дальше» из режимов local_search /
    single сбрасываем playlist_mode в "local_search_next" ДО play_index.
    Иначе brain/main.py::_handle_event("music_started", mode=...) снова
    сказал бы PLAY_SEARCH_FOUND («Нашёл: ...») при каждом next.
    """
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

    # FIX (2026-11-11): TTS PLAY_SEARCH_FOUND — только для первого
    # трека после play_search. При «дальше»/play_next — сбрасываем
    # режим, чтобы TTS не срабатывал повторно.
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
    """Мониторинг плеера + heartbeat music_playing.

    FIX (этап 2.6): единственный писатель music_playing по поддержанию.
    - state == Playing: раз в MUSIC_HEARTBEAT_SEC → set_music_playing()
      (продление TTL). Первый раз — сразу, если флага не было.
    - state != Playing (Paused/Stopped/Ended/Opening/Buffering/Error):
      clear_music_playing() (если флаг был).
    - st is None (плеера нет): clear_music_playing() (если был).
    - Ended (переход): play_next — как раньше.
    """
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

        now = time.time()

        # ── Heartbeat: Playing → продлевать TTL ──
        if st == vlc.State.Playing:
            if (not last_flag_alive) or ((now - last_playing_refresh) >= MUSIC_HEARTBEAT_SEC):
                set_music_playing()
                last_playing_refresh = now
                last_flag_alive = True
        else:
            # Paused / Stopped / Ended / Opening / Buffering / Error —
            # музыка не играет. clear (если флаг был).
            if last_flag_alive:
                clear_music_playing()
                last_flag_alive = False

        # ── Ended: переход к следующему треку ──
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