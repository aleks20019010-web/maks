# workers/music/playback.py
# Воспроизведение: play_index, play_next, play_prev, monitor_player.
# Плюс refill_one / refill_to_target / warm_up_prefetch.
#
# VLC реальный. Music worker играет.
#
# ── A2 (день 29): music_playing — живёт в shared/runtime_state ──
#   FIX (этап 0.1): раньше флаг music_playing.flag писали ЗДЕСЬ и в
#   music/main.py (дублирование), формат — write_text(str(time.time())),
#   проверка в ASR — exists() БЕЗ TTL. Если music-воркер падал с флагом,
#   ASR навсегда считал, что музыка играет.
#   Теперь: set_music_playing() / clear_music_playing() — единственный
#   источник правды (main.py импортирует отсюда), через runtime_state.
#
# ── FIX (этап 0.5): warm_up_prefetch ─────────────────────────────
#   Симптом: «включи музыку» → queue_wave → refill_one →
#   get_next_source_track (Yandex API: feed() 10–12 сек) →
#   play_index(0) → ▶. 10 секунд тишины после TTS.
#   Решение: warm_up_prefetch при старте music (и после
#   _reset_playback_state) кладёт один локальный трек в playlist[0]
#   ЗАРАНЕЕ. К моменту «включи музыку» play_index(0) берёт готовый —
#   0 секунд. Yandex-загрузка уходит в фон для следующих.
#
#   Ограничение: warm_up не решает «включи арию» (search_artist —
#   Yandex API, не локальная папка) и случай «локальных нет».
#
# Защита от гонки: state.play_lock на всё переключение.
# Флаг state.switching: monitor_player не дёргает play_next,
# пока идёт переключение из main.
#
# События в brain:
#   music_started  — после mark_ready() в play_index
#   track_finished — в monitor_player на Ended
#   discovery      — v6: перед discovery-треком, brain скажет фразу
#
# ── FIX (этап 0.5): print → log(tag="MUSIC") ────────────────────
#   Раньше музыка логировалась ТОЛЬКО в stderr (print), в jarvis.log
#   её не было. Отладка музыки — только по консоли.

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
from workers.music.local import LocalTrack, get_local_random_track
from workers.music.download import (
    download_track, start_prefetch, _append_and_prefetch,
)
from workers.music.sources import get_next_source_track


PREFETCH_WAIT_SEC = 10.0
MAX_PLAY_ATTEMPTS = 3

# v6: сколько ждать, пока TTS начнёт discovery-фразу.
DISCOVERY_WAIT_SEC = 3.0


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
def set_music_playing():
    """Музыка реально играет. TTL — 30 сек."""
    rt.set(rt.FLAG_MUSIC_PLAYING, True, ttl=rt.TTL_MUSIC_PLAYING)


def clear_music_playing():
    """Музыка остановлена / на паузе / очередь кончилась."""
    rt.clear(rt.FLAG_MUSIC_PLAYING)


def _mind():
    """Ленивая ссылка на mind_stream."""
    try:
        from personality_core import mind_stream as _ms
        return _ms.get()
    except Exception:
        return None


def _tts_speaking() -> bool:
    """TTS говорит? Через state_file (speaking + last_text + ts).

    Не через runtime_state: tts_state.json — структурированный файл,
    runtime_state — про bool-флаги. ASR тоже читает tts_state.json
    (для echo-фильтра), так что формат общий.
    """
    try:
        from workers.tts.state_file import get_state
        return bool(get_state().get("speaking", False))
    except Exception:
        return False


def _wait_discovery_tts(timeout: float = DISCOVERY_WAIT_SEC) -> bool:
    """v6: дождаться, пока TTS начнёт discovery-фразу."""
    waited = 0.0
    while waited < timeout:
        if _tts_speaking():
            return True
        time.sleep(0.1)
        waited += 0.1
    return False


# ── FIX (этап 0.5): warm_up_prefetch ────────────────────────
def warm_up_prefetch():
    """Подготовить первый локальный трек в playlist[0] заранее.

    При старте music-воркера (и после _reset_playback_state) кладёт
    один локальный трек в state.playlist[0]. Тогда play_index(0) не
    ищет — берёт готовый. 0 секунд до старта музыки.

    Идемпотентно: если playlist уже не пуст — ничего не делает.
    В фоне, никого не блокирует. Ошибки — WARN, не падаем.

    Почему вызывается вне playlist_lock для _append_and_prefetch:
    внутри _append_and_prefetch → start_prefetch → новый поток, но
    сам _append_and_prefetch берёт playlist_lock на append. Если
    держать лок снаружи — блокируем refill_one/play_index на время
    сканирования. Поэтому: double-check под локом, вызов вне.
    """
    if not cfg.MUSIC_AVAILABLE:
        return
    try:
        with state.playlist_lock:
            if state.playlist:
                return  # уже есть — кто-то положил (prefetch, refill)
        local_path = get_local_random_track()
        if not local_path:
            log("warm_up: локальных треков нет", tag="MUSIC", level="DEBUG")
            return
        with state.playlist_lock:
            # double-check: пока сканировали (0.21 сек), мог положить кто-то ещё
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
    """
    with state.play_lock:
        attempts = 0
        while attempts < MAX_PLAY_ATTEMPTS:
            with state.playlist_lock:
                plen = len(state.playlist)
            if idx >= plen:
                log("Очередь закончилась.", tag="MUSIC")
                clear_music_playing()
                return False
            with state.playlist_lock:
                track = state.playlist[idx]

            _artist_for_mind = ""
            _title_for_mind = ""
            _is_discovery = getattr(track, "_is_discovery", False)

            if isinstance(track, LocalTrack):
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

            # FIX (2026-11-07): явный media через singleton instance.
            inst = _get_music_instance()
            media = inst.media_new(file_path)
            player = inst.media_player_new()
            player.set_media(media)
            music.set_player(player, media)

            # v6: DISCOVERY через brain.
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

            # FIX (2026-11-08): НЕ ждём TTS. Музыка стартует сразу.
            try:
                player.play()
            except Exception as e:
                log(f"player.play() упал: {e}", tag="MUSIC", level="WARN")
                clear_music_playing()
                return False

            music.mark_ready()
            set_music_playing()

            state.current_track_title = title
            state.current_track_id = track.id
            state.playlist_index = idx
            state.last_play_start_time = time.time()
            log(f"▶ [{idx+1}/{plen}] {title}", tag="MUSIC")

            try:
                ipc.put(protocol.BRAIN_INBOX,
                        protocol.event_music_started(artist=_artist_for_mind,
                                                     title=_title_for_mind))
            except Exception as e:
                log(f"event_music_started: {e}", tag="MUSIC", level="WARN")

            start_prefetch(idx + 1, count=cfg.PREFETCH_COUNT)
            threading.Thread(target=refill_to_target, daemon=True).start()
            return True

        log(f"play_index: сдался после {MAX_PLAY_ATTEMPTS} попыток",
            tag="MUSIC", level="WARN")
        clear_music_playing()
        return False


def play_next(from_finish=False):
    """Перейти к следующему треку.

    Возвращает True, если удалось переключиться, False — если нет,
    None — если debounce.
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

    next_idx = state.playlist_index + 1
    with state.playlist_lock:
        plen = len(state.playlist)
    if next_idx < plen:
        ok = play_index(next_idx)
        if not ok:
            log(f"play_next: play_index({next_idx}) вернул False",
                tag="MUSIC", level="WARN")
            clear_music_playing()
        return ok
    if refill_one():
        with state.playlist_lock:
            plen = len(state.playlist)
        if state.playlist_index < plen:
            ok = play_index(state.playlist_index)
        else:
            ok = play_index(state.playlist_index + 1)
        if not ok:
            log("play_next: play_index(refill) вернул False",
                tag="MUSIC", level="WARN")
            clear_music_playing()
        return ok
    log("Очередь закончилась.", tag="MUSIC")
    clear_music_playing()
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
    last_state = None
    while True:
        time.sleep(1)

        if state.switching:
            last_state = None
            continue

        st = music.get_state()
        if st is None:
            last_state = None
            continue

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
                ok = play_next(from_finish=True)
                if not ok:
                    clear_music_playing()
            finally:
                state.switching = False

        last_state = st