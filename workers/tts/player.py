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
#
# v6.2 (2026-11-05): play_tts_file принимает rate и применяет VLC set_rate.
#   Причина: synth больше НЕ передаёт rate в Яндекс (mp3 всегда speed=1.0,
#   кэш всегда попадает). rate из personality_core (prosody) применяется
#   здесь, в VLC: set_rate(1.05) = быстрее на 5%, тон сохраняется
#   (VLC Scaletempo). set_rate вызывается ДО play() — без щелчков.
#   "+5%" → 1.05, "-10%" → 0.90 (хелпер _rate_to_float).
#
# FIX (2026-11-07): PCR is called too late / ES_OUT_SET_(GROUP_)PCR.
#   Причина: vlc.MediaPlayer(file_path) использовал ДЕФОЛТНЫЙ глобальный
#   vlc.Instance(). Если в том же процессе есть music-плеер — общий clock,
#   старый PCR «просрочен» → VLC ругается, Opening затягивается.
#   Фикс:
#    1) явный vlc.Instance() для TTS (изоляция от music);
#    2) media = inst.media_new(path) + p.set_media(media) вместо
#       vlc.MediaPlayer(path) — media освобождается явно (media.release());
#    3) media.release() в finally (утечка C-объектов libvlc_media_t);
#    4) p.stop() УБРАН из finally — блокирует 1-2 сек (VLC flush),
#       release() сам останавливает воспроизведение;
#    5) TTS_START_TIMEOUT_SEC дефолт 2.0 → 3.0 (VLC Opening на Windows
#       с directsound дольше 2 сек на cold start).

import os
import time

import vlc

from shared import config as cfg
from shared.log import log


TTS_VOLUME = 100


# FIX (2026-11-07): явный vlc.Instance для TTS — изоляция от music.
# Дефолтный vlc.MediaPlayer() использует глобальный instance; если в том же
# процессе есть music-плеер, они делят clock → PCR «too late».
# Один instance на процесс TTS (создаётся лениво).
_vlc_instance = None


def _get_instance():
    global _vlc_instance
    if _vlc_instance is None:
        _vlc_instance = vlc.Instance(
            "--no-video",
            "--no-osd",
            "--quiet",
            "--aout=mmdevice",   # FIX (2026-11-08): Windows 10/11 — быстрее directsound
        )
        log("tts: vlc.Instance создан (TTS, изолирован от music)",
            tag="TTS", level="DEBUG")
    return _vlc_instance


def _state_str(state):
    try:
        return str(state)
    except Exception:
        return "<unknown>"


def _rate_to_float(rate) -> float | None:
    """'+5%' → 1.05, '-10%' → 0.90. None/'' → None.

    VLC set_rate принимает float (1.0 = норма). Тон сохраняется
    (Scaletempo). Если rate не парсится — None (не трогаем скорость).
    """
    if not rate:
        return None
    try:
        s = str(rate).replace('%', '').replace('+', '').strip()
        pct = int(s)
        r = 1.0 + pct / 100.0
        # Защита от абсурдных значений
        if r < 0.5 or r > 2.0:
            return None
        return r
    except Exception:
        return None


def play_tts_file(file_path: str, rate: str | None = None) -> bool:
    """Проиграть mp3. Останавливает и освобождает плеер после окончания.

    rate — строка из personality_core (prosody), например "+5%".
    Применяется в VLC (set_rate) ДО play(), тон сохраняется.
    mp3 всегда синтезирован с speed=1.0 (кэш совпадает).

    Возвращает True, если проигрывание завершилось (Ended/Stopped),
    False — при ошибке или таймауте.
    """
    if not os.path.exists(file_path):
        return False

    # FIX (2026-11-07): дефолт 2.0 → 3.0 (VLC Opening на Windows дольше).
    start_timeout = float(getattr(cfg, "TTS_START_TIMEOUT_SEC", 3.0))
    play_timeout = float(getattr(cfg, "TTS_PLAY_TIMEOUT_SEC", 30.0))

    p = None
    media = None
    ok = False
    try:
        # FIX (2026-11-07): явный instance + media_new/set_media
        # вместо vlc.MediaPlayer(file_path). Изоляция от music,
        # media освобождается явно (media.release() в finally).
        inst = _get_instance()
        media = inst.media_new(file_path)
        p = inst.media_player_new()
        p.set_media(media)

        try:
            p.audio_set_volume(TTS_VOLUME)
        except Exception:
            pass

        # v6.2: rate из prosody — применяем в VLC ДО play().
        # mp3 уже синтезирован с speed=1.0, set_rate ускоряет/замедляет
        # воспроизведение, тон сохраняется.
        if rate:
            try:
                r = _rate_to_float(rate)
                if r is not None and abs(r - 1.0) > 1e-6:
                    p.set_rate(r)
            except Exception as e:
                log(f"tts: set_rate({rate}) error: {e}",
                    tag="TTS", level="WARN")

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
        # FIX (2026-11-07): stop() УБРАН — блокирует 1-2 сек (VLC flush),
        # release() сам останавливает воспроизведение + освобождает
        # libvlc_media_player_t. media.release() — освобождает
        # libvlc_media_t (иначе утечка C-объектов на каждый вызов).
        if p is not None:
            try:
                p.release()
            except Exception:
                pass
        if media is not None:
            try:
                media.release()
            except Exception:
                pass