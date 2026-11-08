# workers/music/playback.py
# Воспроизведение: play_index, play_next, play_prev, monitor_player.
# Плюс refill_one / refill_to_target — заполнение очереди.
#
# VLC реальный. Music worker играет.
#
# A2 (день 29): music_playing.flag — пишем, когда музыка реально играет.
#   ASR читает флаг: если музыка играет — hot words («стоп», «дальше»)
#   пропускаются в brain. Не играет — фон, игнор.
#
#   _set_music_playing() — после mark_ready в play_index.
#   _clear_music_playing() — если play_index не смог, play_next вернул False,
#   monitor_player Ended + play_next False. stop/pause — в main.py.
#
# Защита от гонки: state.play_lock на всё переключение.
# Флаг state.switching: monitor_player не дёргает play_next,
# пока идёт переключение из main.
# player.play() в try/except — VLC иногда падает.
#
# События в brain:
#   music_started  — после mark_ready() в play_index (трек реально играет)
#   track_finished — в monitor_player на Ended (до play_next), с артистом/треком
#   discovery      — v6: перед discovery-треком, brain скажет фразу
#
# Duck: если TTS говорит — не снимаем паузу. TTS снимет сам после конца фразы.
#
# Prefetch timeout: 10 сек. Если prefetch не успел — пропускаем трек,
# играем следующий готовый. Не блокируем play_lock на минуты.
# download_track в play_index — крайний случай, с таймаутом 30 сек (в download.py).
#
# FIX: после stop (playlist пуст, playlist_index = 0) play_next после refill_one
# играет индекс 0, а не playlist_index + 1 (иначе «Очередь закончилась»).
#
# FIX (2026-10-27): play_index — счётчик попыток. Если 3 трека подряд не удалось
# получить — сдаёмся, return False.
#
# DUCKING (день 29, вечер): ducking привязан к микрофону ASR.
#   - _release_duck_safe() больше НЕ вызывает music.release_duck().
#     Ducking от TTS снимается ASR при mic_on (duck_off). Здесь только
#     проверка TTS-флага для случая «плеер новый, а TTS ещё говорит» —
#     hold уже стоит в modes.py, ничего дополнительно не делаем.
#     Раньше: _release_duck_safe() дёргал music.release_duck() в play_index
#     после mark_ready → снимал ducking, поставленный TTS, даже если TTS
#     ещё говорил. Именно это давало «приглушение после TTS» в логе 15:15:20
#     (volume 100 → 20, потом volume → 100 через секунду, TTS ещё говорит).
#   - _tts_speaking() оставлен: используется в play_index для паузы перед
#     player.play(), чтобы не начинать трек поверх TTS.
#   - music.set_player() при ducked=True создаст плеер приглушённым
#     (см. player.py), TTS-hold от modes.py продолжит работать.
#   - ⚠ music.check_duck_watchdog() надо вызывать из music/main.py при
#     пустой очереди — иначе если TTS упадёт, ducking залипнет.
#
# v6 (2026-11-04): DISCOVERY через brain (единая личность).
#   Music НЕ читает music_discovery.json и НЕ шлёт в TTS напрямую.
#   Вместо _say_discovery: шлём event_discovery в brain → brain решает
#   «когда» → personality_core («что и как», composer) → TTS.
#   Ждём speaking=True до DISCOVERY_WAIT_SEC (3 сек): фразы прогреты
#   в кэш warmup_tts.py, поэтому TTS стартует мгновенно. Если не
#   дождались — играем без фразы (TTS не может: сеть/API).
#
# v6.1 (2026-11-05): убраны time.sleep(2.0) и else: time.sleep(1.0)
#   перед player.play(). Причина: _tts_speaking()-цикл уже ждёт КОНЦА
#   TTS (если говорит), а ASR даёт 1.0 сек на «хвост VLC» (микрофон ВКЛ
#   через 1.0s). Эхо не услышит. Лишние 2–3 сек задержки убраны.
#   TTS перед треком сохраняется: ждём speaking=False, если TTS говорит.
#
# FIX (2026-11-07): PCR is called too late / ES_OUT_SET_(GROUP_)PCR.
#   Причина: vlc.MediaPlayer(file_path) использовал ДЕФОЛТНЫЙ глобальный
#   vlc.Instance(). Media (libvlc_media_t) создавалась ВНУТРИ конструктора
#   MediaPlayer и НЕ освобождалась (release() плеера не освобождает media
#   в python-vlc). Утечка libvlc_media_t на каждый трек → старый PCR
#   «просрочен» → VLC ругается, Opening затягивается, музыка стартует с
#   задержкой.
#   Фикс:
#     1) _get_music_instance() — явный singleton vlc.Instance() для music
#        (изоляция от TTS/radio-плееров, которые могут делить clock в том
#        же процессе).
#     2) media = inst.media_new(path) + p.set_media(media) вместо
#        vlc.MediaPlayer(path) — media освобождается ЯВНО.
#     3) music.set_player(player, media) — MusicManager хранит _media и
#        освобождает её в _safe_release / clear_player.
#   radio.py импортирует _get_music_instance из этого модуля — один
#   instance на music-процесс (без дублирования и PCR-конфликта между
#   playback и radio).
#
# FIX (2026-11-07, #2): дублирование _get_music_instance в radio.py
#   невозможно (импорт из playback.py) — но если radio.py импортирует
#   playback.py, а playback.py импортирует radio.py — циклический импорт.
#   Проверено: playback.py НЕ импортирует radio.py. radio.py импортирует
#   playback.py (только _get_music_instance) — цикла НЕТ.
#
# FIX (2026-11-08): убран _tts_speaking() цикл в play_index.
#   Раньше: если TTS говорит, play_index ждал КОНЦА TTS (6+ сек) до
#   player.play(). Музыка стартовала ПОСЛЕ TTS — 15 сек задержки между
#   «включи музыку» и реальным звуком.
#   Теперь: музыка стартует сразу (ducking 30%, TTS-hold от modes.py),
#   TTS говорит поверх. ASR уже выключил микрофон (mic_off на TTS) —
#   эхо не проблема. Первый трек — сразу (0–1 сек вместо 6–15).

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


PREFETCH_WAIT_SEC = 10.0
MAX_PLAY_ATTEMPTS = 3

# v6: сколько ждать, пока TTS начнёт discovery-фразу.
# Фразы прогреты в кэш (warmup_tts.py) → TTS стартует мгновенно.
# 3 сек — с большим запасом. Не дождались → TTS не может (сеть/API).
DISCOVERY_WAIT_SEC = 3.0

_MUSIC_PLAYING_FLAG = cfg.DATA_DIR / "music_playing.flag"


# ── FIX (2026-11-07): явный vlc.Instance для music ────────────────
# Изоляция от TTS-плеера (у TTS свой instance в workers/tts/player.py)
# и от любых других плееров в music-процессе. Один instance на процесс.
# radio.py импортирует _get_music_instance отсюда — без дублирования.
_music_instance = None


def _get_music_instance():
    global _music_instance
    if _music_instance is None:
        _music_instance = vlc.Instance(
            "--no-video",
            "--no-osd",
            "--quiet",
            "--aout=mmdevice",   # FIX (2026-11-08): Windows 10/11 — быстрее directsound
        )
        print("   [MUSIC] vlc.Instance создан (music, изолирован от TTS)")
    return _music_instance


# ── A2: music_playing.flag ────────────────────────────────────
def _set_music_playing():
    try:
        _MUSIC_PLAYING_FLAG.parent.mkdir(parents=True, exist_ok=True)
        _MUSIC_PLAYING_FLAG.write_text(str(time.time()), encoding="utf-8")
    except Exception as e:
        print(f"   ⚠ playback: set music_playing.flag: {e}")


def _clear_music_playing():
    try:
        if _MUSIC_PLAYING_FLAG.exists():
            _MUSIC_PLAYING_FLAG.unlink()
    except Exception as e:
        print(f"   ⚠ playback: clear music_playing.flag: {e}")


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


def _wait_discovery_tts(timeout: float = DISCOVERY_WAIT_SEC) -> bool:
    """v6: дождаться, пока TTS начнёт discovery-фразу.

    Фразы прогреты в кэш (warmup_tts.py) → TTS ставит speaking=True
    почти мгновенно. Если за timeout не дождались — TTS не может
    (сеть/API/brain) — играем без фразы.

    Возвращает True, если TTS начал говорить (speaking=True появился).
    Существующий _tts_speaking()-блок в play_index дождётся КОНЦА TTS.
    """
    waited = 0.0
    while waited < timeout:
        if _tts_speaking():
            return True
        time.sleep(0.1)
        waited += 0.1
    return False


# _release_duck_safe() удалён. Ducking от TTS снимает ASR при mic_on.
# Если TTS ещё говорит, music.set_player() создал плеер приглушённым
# (ducked=True), а mark_ready() применит _duck_level. ASR пришлёт duck_off
# в момент mic_on — это и восстановит громкость. Никаких действий отсюда
# не требуется: любой вызов music.release_duck() здесь снимает ducking
# раньше времени (баг из лога 15:15:20).


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

    attempts <= MAX_PLAY_ATTEMPTS: если 3 трека подряд не удалось получить
    (prefetch timeout, download_track None, file_path битый) — сдаёмся.
    """
    with state.play_lock:
        attempts = 0
        while attempts < MAX_PLAY_ATTEMPTS:
            with state.playlist_lock:
                plen = len(state.playlist)
            if idx >= plen:
                print("Очередь закончилась.")
                _clear_music_playing()
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
                            attempts += 1
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
                print(f"  ✗ Не удалось получить файл. Пропускаю (попытка {attempts+1}/{MAX_PLAY_ATTEMPTS}).")
                idx += 1
                attempts += 1
                continue

            # FIX (2026-11-07): явный media через singleton instance.
            # Раньше: vlc.MediaPlayer(file_path) — дефолтный instance,
            # media внутри конструктора, не освобождалась → утечка → PCR.
            # Теперь: media создаётся явно, music.set_player(player, media)
            # сохраняет _media и освобождает её в _safe_release.
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
                    print(f"   ⚠ playback: event_discovery: {e}")

                if not _wait_discovery_tts():
                    print(f"   ⚠ discovery: TTS не начал за "
                          f"{DISCOVERY_WAIT_SEC:.0f}с (сеть/API?) — играю без фразы")

            # FIX (2026-11-08): НЕ ждём TTS. Музыка стартует сразу (ducking 30%),
            # TTS говорит поверх. ASR уже выключил микрофон (mic_off на TTS) —
            # эхо не проблема. _tts_speaking() цикл давал 6+ сек задержки.
            # TTS-hold (modes.py) уже приглушает музыку до mark_ready.
            #
            # Было:
            #   if _tts_speaking():
            #       waited = 0.0
            #       while _tts_speaking() and waited < 30:
            #           time.sleep(0.1)
            #           waited += 0.1

            try:
                player.play()
            except Exception as e:
                print(f"  ✗ player.play() упал: {e}")
                _clear_music_playing()
                return False

            music.mark_ready()
            _set_music_playing()

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

        print(f"  ✗ play_index: сдался после {MAX_PLAY_ATTEMPTS} попыток")
        _clear_music_playing()
        return False


def play_next(from_finish=False):
    """Перейти к следующему треку.

    from_finish=True — вызвано из monitor_player (трек закончился).
    from_finish=False — вызвано из _cmd_next (пользователь сказал «дальше»).

    Возвращает True, если удалось переключиться, False — если нет,
    None — если debounce.
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
            _clear_music_playing()
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
            _clear_music_playing()
        return ok
    print("Очередь закончилась.")
    _clear_music_playing()
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
                ok = play_next(from_finish=True)
                if not ok:
                    # Очередь кончилась — музыка больше не играет
                    _clear_music_playing()
            finally:
                state.switching = False

        last_state = st