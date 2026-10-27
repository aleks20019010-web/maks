# workers/music/playback.py
# Воспроизведение: play_index, play_next, play_prev, monitor_player.
# Плюс refill_one / refill_to_target — заполнение очереди.
#
# VLC реальный. Music worker играет.
#
# Защита от гонки: state.play_lock на всё переключение.
# Флаг state.switching: monitor_player не дёргает play_next,
# пока идёт переключение из main.
# player.play() в try/except — VLC иногда падает.
#
# События в brain:
#   music_started  — после mark_ready() в play_index (трек реально играет)
#   track_finished — в monitor_player на Ended (до play_next), с артистом/треком
#
# Duck: если TTS говорит — не снимаем паузу. TTS снимет сам после конца фразы.
#
# Prefetch timeout: 10 сек. Если prefetch не успел — пропускаем трек,
# играем следующий готовый. Не блокируем play_lock на минуты.
#
# FIX (день 28, баг из лога): после player.play() ждём State.Playing
# (таймаут 2 сек, шаг 0.1) ПЕРЕД mark_ready() и _release_duck_safe().
# Раньше: play() → сразу mark_ready() → _release_duck_safe(). VLC ещё
# в State.Opening, duck() не ставил паузу, TTS говорил поверх музыки.
# Лог: [DUCK] not playing (state=State.Opening), пауза НЕ поставлена.
# Теперь: ждём Playing, потом mark_ready() + release. Если не дождались
# за 2 сек — лог, но mark_ready() всё равно (иначе плеер «мёртвый»).

import os
import time
import threading

import vlc

from shared import config as cfg
from shared import ipc
from shared import protocol
from shared.audio_cache import find_local_by_track
from memory import memory
from workers.music import state
from workers.music.player import music
from workers.music.local import LocalTrack, get_local_random_track
from workers.music.download import (
    download_track, start_prefetch, _append_and_prefetch,
)
from workers.music.sources import get_next_source_track


PREFETCH_WAIT_SEC = 10.0   # сколько ждём prefetch перед тем, как пропустить трек
PLAYING_WAIT_SEC = 2.0     # сколько ждём State.Playing после player.play()
PLAYING_WAIT_STEP = 0.1


def _mind():
    """Ленивая ссылка на mind_stream (может быть не готова на импорте)."""
    try:
        from personality_core import mind_stream as _ms
        return _ms.get()
    except Exception:
        return None


def _tts_speaking() -> bool:
    """Проверить, говорит ли TTS (через state_file воркера)."""
    try:
        from workers.tts.state_file import get_state
        return bool(get_state().get("speaking", False))
    except Exception:
        return False


def _release_duck_safe():
    """Снять duck, только если TTS не говорит.

    Если TTS говорит — duck_hold остаётся, TTS сам снимет после конца фразы.
    """
    if _tts_speaking():
        print("   [DUCK] TTS говорит — пауза сохраняется (release_duck пропущен)")
        return
    music.release_duck()


def _wait_for_playing(player, timeout: float = PLAYING_WAIT_SEC) -> bool:
    """Ждать, пока VLC перейдёт в State.Playing. True — дождались.

    FIX (день 28): раньше mark_ready() и release_duck() вызывались сразу
    после player.play(). VLC в State.Opening — duck() не ставил паузу,
    TTS говорил поверх музыки. Теперь ждём Playing.
    """
    waited = 0.0
    while waited < timeout:
        try:
            st = player.get_state()
        except Exception:
            return False
        if st == vlc.State.Playing:
            return True
        if st in (vlc.State.Ended, vlc.State.Stopped, vlc.State.Error):
            return False
        time.sleep(PLAYING_WAIT_STEP)
        waited += PLAYING_WAIT_STEP
    return False


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
                print("  📻 Первый трек — локальный (быстрый старт)")
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
        while True:
            with state.playlist_lock:
                plen = len(state.playlist)
            if idx >= plen:
                print("Очередь закончилась.")
                return False
            with state.playlist_lock:
                track = state.playlist[idx]

            _artist_for_mind = ""
            _title_for_mind = ""

            if isinstance(track, LocalTrack):
                file_path = track.path
                title = f"{track.title} — {track.artists[0].name}"
                _artist_for_mind = track.artists[0].name
                _title_for_mind = track.title
                print(f"✅ [{idx+1}/{plen}] Локально: {title}")
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
                    print(f"✅ [{idx+1}/{plen}] Локально: {title}")
                else:
                    with state.playlist_lock:
                        waiting = idx in state.prefetched
                    file_path = None
                    if waiting:
                        print(f"⏳ Жду prefetch: {title}")
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
                            print(f"  ⚠ prefetch timeout {PREFETCH_WAIT_SEC:.0f}с — пропускаю «{title}»")
                            idx += 1
                            continue
                    if not file_path:
                        print(f"  Качаю сейчас: {title}")
                        try:
                            file_path = download_track(track)
                        except Exception as e:
                            print(f"  ✗ download_track упал: {e}")
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
                print(f"   ⚠ playback: mind_stream.on_play: {e}")

            if not file_path or not os.path.exists(file_path):
                print("  ✗ Не удалось получить файл. Пропускаю.")
                idx += 1
                continue

            player = vlc.MediaPlayer(file_path)
            music.set_player(player)

            if _tts_speaking():
                waited = 0.0
                while _tts_speaking() and waited < 30:
                    time.sleep(0.1)
                    waited += 0.1
                time.sleep(2.0)
            else:
                time.sleep(1.0)

            try:
                player.play()
            except Exception as e:
                print(f"  ✗ player.play() упал: {e}")
                return False

            # FIX (день 28): ждём State.Playing ПЕРЕД mark_ready + release_duck.
            # Иначе duck() видит State.Opening и не ставит паузу — TTS поверх музыки.
            if _wait_for_playing(player, timeout=PLAYING_WAIT_SEC):
                pass
            else:
                print(f"   ⚠ play_index: State.Playing не дождались за {PLAYING_WAIT_SEC:.0f}с — mark_ready всё равно")

            music.mark_ready()
            _release_duck_safe()

            state.current_track_title = title
            state.current_track_id = track.id
            state.playlist_index = idx
            state.last_play_start_time = time.time()
            print(f"▶ [{idx+1}/{plen}] {title}")

            try:
                ipc.put(protocol.BRAIN_INBOX,
                        protocol.event_music_started(artist=_artist_for_mind,
                                                     title=_title_for_mind))
            except Exception as e:
                print(f"   ⚠ playback: event_music_started: {e}")

            start_prefetch(idx + 1, count=cfg.PREFETCH_COUNT)
            threading.Thread(target=refill_to_target, daemon=True).start()
            return True


def play_next(from_finish=False):
    """Перейти к следующему треку.

    from_finish=True — вызвано из monitor_player (трек закончился).
                       Не считается скипом. remember_finish уже вызван.
    from_finish=False — вызвано из _cmd_next (пользователь сказал «дальше»).
                        Считается скипом, если трек играл >3 сек.

    Возвращает True, если удалось переключиться, False — если нет.
    """
    now = time.time()
    if now - state.last_next_time < cfg.NEXT_DEBOUNCE_SEC:
        print("   ⏭ debounce")
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
            print(f"   ⚠ playback: mind_stream.on_skip: {e}")

    next_idx = state.playlist_index + 1
    with state.playlist_lock:
        plen = len(state.playlist)
    if next_idx < plen:
        ok = play_index(next_idx)
        if not ok:
            print(f"   ⚠ play_next: play_index({next_idx}) вернул False")
        return ok
    if refill_one():
        with state.playlist_lock:
            plen = len(state.playlist)
        if state.playlist_index < plen:
            ok = play_index(state.playlist_index)
        else:
            ok = play_index(state.playlist_index + 1)
        if not ok:
            print(f"   ⚠ play_next: play_index(refill) вернул False")
        return ok
    print("Очередь закончилась.")
    return False


def play_prev():
    now = time.time()
    if now - state.last_next_time < cfg.NEXT_DEBOUNCE_SEC:
        print("   ⏮ debounce")
        return
    state.last_next_time = now

    with state.playlist_lock:
        cur_idx = state.playlist_index

    if cur_idx <= 0:
        print("   ⏮ предыдущего нет — рестарт текущего")
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
            print("\n⏭ Трек закончился...")

            cur = None
            with state.playlist_lock:
                if 0 <= state.playlist_index < len(state.playlist):
                    cur = state.playlist[state.playlist_index]

            if cur:
                cur_id = str(cur.id)
                if state._last_skipped_track_id == cur_id:
                    print(f"   (skip уже записан, finish пропущен для {cur_id})")
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
                        print(f"   ⚠ playback: mind_stream.on_finish: {e}")

                    try:
                        ipc.put(protocol.BRAIN_INBOX,
                                protocol.event_track_finished(artist=_fin_artist,
                                                              title=_fin_title))
                    except Exception as e:
                        print(f"   ⚠ playback: event_track_finished: {e}")

            state.switching = True
            try:
                play_next(from_finish=True)
            finally:
                state.switching = False

        last_state = st