# workers/tts/player.py
# VLC-плеер для TTS. Ducking музыки привязан к ASR (микрофон ВЫКЛ/ВКЛ →
# duck_on/duck_off), см. workers/asr/main.py. TTS worker ducking НЕ управляет.
#
# FIX (2026-10-27): p.release() в finally. Раньше: p.stop() только.
# stop() останавливает воспроизведение, но НЕ освобождает C-объект
# libvlc_media_player_t. Без release() — C-объект живёт до gc.collect()
# (refcount), а на Windows с python-vlc __del__ может не сработать.
# TTS worker — долгоживущий, вызывает play_tts_file каждую фразу.
# Если __del__ не срабатывает — тысячи libvlc_media_player_t в памяти,
# WorkingSet растёт. release() освобождает явно.
#
# FIX (день 29, вечер): duck_music_on/duck_music_off удалены.
#   Ducking привязан к микрофону: ASR при mic_off (TTS идёт) шлёт duck_on,
#   при mic_on — duck_off. TTS worker про музыку больше не знает.
#   Если в других местах остались вызовы duck_music_on/off —
#   это ошибка, их надо убрать (grep -rn "duck_music_on\|duck_music_off").
#
# FIX (день 29): play_tts_file:
#  - при State.Error на старте — лог + ранний return False (не ждём 30с);
#  - таймауты вынесены в cfg.TTS_START_TIMEOUT_SEC / cfg.TTS_PLAY_TIMEOUT_SEC;
#  - при State.Error во время воспроизведения — лог ERROR + return False;
#  - при таймауте — лог WARN, но return False.

import os
import time

import vlc

from shared import config as cfg
from shared.log import log


TTS_VOLUME = 100


def _state_str(state):
    try:
        return str(state)
    except Exception:
        return "<unknown>"


def play_tts_file(file_path: str) -> bool:
    """Проиграть mp3. Останавливает и освобождает плеер после окончания.

    Возвращает True, если проигрывание завершилось (Ended/Stopped),
    False — при ошибке или таймауте.
    """
    if not os.path.exists(file_path):
        return False

    start_timeout = float(getattr(cfg, "TTS_START_TIMEOUT_SEC", 2.0))
    play_timeout = float(getattr(cfg, "TTS_PLAY_TIMEOUT_SEC", 30.0))

    p = None
    ok = False
    try:
        p = vlc.MediaPlayer(file_path)
        try:
            p.audio_set_volume(TTS_VOLUME)
        except Exception:
            pass
        p.play()

        # Ждём реального старта (State.Playing), не фиксированные 0.4 сек.
        waited = 0.0
        started = False
        while waited < start_timeout:
            try:
                s = p.get_state()
            except Exception:
                break
            if s == vlc.State.Playing:
                started = True
                break
            if s in (vlc.State.Error, vlc.State.Ended, vlc.State.Stopped):
                log(
                    f"tts: start failed state={_state_str(s)} "
                    f"file={os.path.basename(file_path)}",
                    tag="TTS", level="WARN",
                )
                return False
            time.sleep(0.05)
            waited += 0.05
        if not started:
            try:
                cur_state = p.get_state()
            except Exception:
                cur_state = None
            log(
                f"tts: not Playing in {start_timeout:.1f}s, "
                f"continue waiting (state={_state_str(cur_state)})",
                tag="TTS", level="WARN",
            )

        # Ждём реального конца воспроизведения (Ended), не «отдал в VLC».
        waited = 0.0
        while waited < play_timeout:
            try:
                s = p.get_state()
            except Exception:
                break
            if s in (vlc.State.Ended, vlc.State.Stopped):
                ok = True
                break
            if s == vlc.State.Error:
                log(
                    f"tts: play error state=Error "
                    f"file={os.path.basename(file_path)}",
                    tag="TTS", level="ERROR",
                )
                return False
            time.sleep(0.15)
            waited += 0.15
        else:
            log(
                f"tts: play timeout {play_timeout:.1f}s "
                f"file={os.path.basename(file_path)}",
                tag="TTS", level="WARN",
            )
        return ok
    except Exception as e:
        log(f"tts: play error: {e}", tag="TTS", level="ERROR")
        return False
    finally:
        if p is not None:
            try:
                p.stop()
            except Exception:
                pass
            try:
                p.release()
            except Exception:
                pass