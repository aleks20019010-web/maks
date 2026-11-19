# workers/tts/player.py
# VLC-плеер для TTS с переиспользуемым MediaPlayer (без переоткрытия WASAPI на каждую фразу).

import os
import threading
import time

import vlc

from shared import config as cfg
from shared.log import log


TTS_VOLUME = 100

_vlc_instance = None
_vlc_player = None
_vlc_lock = threading.Lock()


def _get_instance():
    global _vlc_instance
    if _vlc_instance is None:
        _vlc_instance = vlc.Instance(
            "--no-video",
            "--no-osd",
            "--quiet",
            "--aout=mmdevice",
        )
        log("tts: vlc.Instance создан (TTS)", tag="TTS", level="DEBUG")
    return _vlc_instance


def _get_player(force_recreate: bool = False):
    """Вернуть единый прогретый MediaPlayer (исключает зависания mmdevice на release/new)."""
    global _vlc_player
    with _vlc_lock:
        if force_recreate and _vlc_player is not None:
            try:
                _vlc_player.stop()
            except Exception:
                pass
            _vlc_player = None

        if _vlc_player is None:
            inst = _get_instance()
            _vlc_player = inst.media_player_new()
            try:
                _vlc_player.audio_set_volume(TTS_VOLUME)
            except Exception:
                pass
            log("tts: единый MediaPlayer создан", tag="TTS", level="DEBUG")
        return _vlc_player


def _state_str(state):
    try:
        return str(state)
    except Exception:
        return "<unknown>"


def _rate_to_float(rate) -> float | None:
    if not rate:
        return None
    try:
        s = str(rate).replace('%', '').replace('+', '').strip()
        pct = int(s)
        r = 1.0 + pct / 100.0
        if r < 0.5 or r > 2.0:
            return None
        return r
    except Exception:
        return None


def _call_with_timeout(fn, timeout: float, label: str):
    result_box = {"value": None, "exc": None}

    def run():
        try:
            result_box["value"] = fn()
        except Exception as e:
            result_box["exc"] = e

    t = threading.Thread(target=run, daemon=True, name=f"vlc-{label}")
    t.start()
    t.join(timeout)

    if t.is_alive():
        log(f"tts: vlc.{label}() завис >{timeout:.1f}с — прерываю ожидание",
            tag="TTS", level="WARN")
        return None, True

    if result_box["exc"] is not None:
        raise result_box["exc"]
    return result_box["value"], False


def _play_with_player(p, media, file_path: str, rate: str | None, start_timeout: float, play_timeout: float) -> tuple[bool, bool]:
    """Вернуть (ok, stuck_in_opening)."""
    try:
        p.audio_set_volume(TTS_VOLUME)
    except Exception:
        pass

    r = _rate_to_float(rate) if rate else 1.0
    if r is None:
        r = 1.0
    try:
        p.set_rate(r)
    except Exception:
        pass

    p.set_media(media)

    _, timed_out = _call_with_timeout(p.play, 2.0, "play")
    if timed_out:
        return False, True

    t_start = time.time()
    started = False
    while (time.time() - t_start) < start_timeout:
        s = p.get_state()
        if s == vlc.State.Playing:
            started = True
            break
        if s in (vlc.State.Error, vlc.State.Ended, vlc.State.Stopped):
            log(f"tts: start failed state={_state_str(s)} file={os.path.basename(file_path)}",
                tag="TTS", level="WARN")
            return False, False
        time.sleep(0.02)

    if not started:
        cur_state = p.get_state()
        log(f"tts: not Playing in {start_timeout:.1f}s — прерываю "
            f"file={os.path.basename(file_path)} (state={_state_str(cur_state)})",
            tag="TTS", level="WARN")
        return False, True

    t_play = time.time()
    while (time.time() - t_play) < play_timeout:
        s = p.get_state()
        if s in (vlc.State.Ended, vlc.State.Stopped):
            return True, False
        if s == vlc.State.Error:
            log(f"tts: play error state=Error file={os.path.basename(file_path)}",
                tag="TTS", level="ERROR")
            return False, False
        time.sleep(0.05)

    log(f"tts: play timeout {play_timeout:.1f}s file={os.path.basename(file_path)}",
        tag="TTS", level="WARN")
    return False, False


def play_tts_file(file_path: str, rate: str | None = None) -> bool:
    if not os.path.exists(file_path):
        return False

    start_timeout = float(getattr(cfg, "TTS_START_TIMEOUT_SEC", 1.5))
    play_timeout = float(getattr(cfg, "TTS_PLAY_TIMEOUT_SEC", 30.0))

    inst = _get_instance()
    media = None

    try:
        media = inst.media_new(file_path)
    except Exception as e:
        log(f"tts: media_new error: {e}", tag="TTS", level="ERROR")
        return False

    try:
        p = _get_player()
        ok, stuck = _play_with_player(p, media, file_path, rate, start_timeout, play_timeout)
        if ok:
            return True

        # Если WASAPI/mmdevice подвис в State.Opening — пересоздаём плеер 1 раз и повторяем
        if stuck:
            log("tts: пересоздаю MediaPlayer после зависания Opening", tag="TTS", level="WARN")
            p = _get_player(force_recreate=True)
            ok, _ = _play_with_player(p, media, file_path, rate, start_timeout, play_timeout)
            return ok

        return False
    except Exception as e:
        log(f"tts: play error: {e}", tag="TTS", level="ERROR")
        return False
    finally:
        try:
            if _vlc_player is not None:
                _vlc_player.stop()
        except Exception:
            pass
        if media is not None:
            try:
                media.release()
            except Exception:
                pass