# workers/tts/player.py
# VLC-плеер для TTS.

import os
import threading
import time

import vlc

from shared import config as cfg
from shared.log import log


TTS_VOLUME = 100

_vlc_instance = None
_vlc_lock = threading.Lock()


def _get_instance():
    global _vlc_instance
    with _vlc_lock:
        if _vlc_instance is None:
            _vlc_instance = vlc.Instance(
                "--no-video",
                "--no-osd",
                "--quiet",
                "--aout=mmdevice",
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


def _safe_play(player, timeout: float = 3.0) -> bool:
    _, timed_out = _call_with_timeout(player.play, timeout, "play")
    return not timed_out


def _safe_get_state(player, timeout: float = 1.0):
    try:
        val, timed_out = _call_with_timeout(player.get_state, timeout, "get_state")
        if timed_out:
            return None
        return val
    except Exception:
        return None


def _safe_set_rate(player, r: float, timeout: float = 1.0) -> bool:
    try:
        _, timed_out = _call_with_timeout(lambda: player.set_rate(r), timeout, "set_rate")
        return not timed_out
    except Exception:
        return False


def _safe_audio_set_volume(player, vol: int, timeout: float = 1.0):
    try:
        _call_with_timeout(lambda: player.audio_set_volume(vol), timeout, "set_volume")
    except Exception:
        pass


def _safe_release(player, timeout: float = 2.0):
    if player is None:
        return
    try:
        _call_with_timeout(player.release, timeout, "release")
    except Exception:
        pass


def _safe_media_release(media, timeout: float = 1.0):
    if media is None:
        return
    try:
        _call_with_timeout(media.release, timeout, "media_release")
    except Exception:
        pass


def play_tts_file(file_path: str, rate: str | None = None) -> bool:
    if not os.path.exists(file_path):
        return False

    start_timeout = float(getattr(cfg, "TTS_START_TIMEOUT_SEC", 3.0))
    play_timeout = float(getattr(cfg, "TTS_PLAY_TIMEOUT_SEC", 30.0))

    p = None
    media = None
    ok = False

    try:
        inst = _get_instance()

        try:
            media = inst.media_new(file_path)
        except Exception as e:
            log(f"tts: media_new error: {e}", tag="TTS", level="ERROR")
            return False

        try:
            p = inst.media_player_new()
            p.set_media(media)
        except Exception as e:
            log(f"tts: media_player_new error: {e}", tag="TTS", level="ERROR")
            return False

        _safe_audio_set_volume(p, TTS_VOLUME)

        if rate:
            r = _rate_to_float(rate)
            if r is not None and abs(r - 1.0) > 1e-6:
                _safe_set_rate(p, r)

        played = _safe_play(p, timeout=3.0)
        if not played:
            log(f"tts: play() не стартовал за 3с — прерываю "
                f"file={os.path.basename(file_path)}",
                tag="TTS", level="WARN")
            return False

        t_start = time.time()
        started = False
        while (time.time() - t_start) < start_timeout:
            s = _safe_get_state(p, timeout=1.0)
            if s is None:
                return False
            if s == vlc.State.Playing:
                started = True
                break
            if s in (vlc.State.Error, vlc.State.Ended, vlc.State.Stopped):
                log(f"tts: start failed state={_state_str(s)} "
                    f"file={os.path.basename(file_path)}",
                    tag="TTS", level="WARN")
                return False
            time.sleep(0.05)

        if not started:
            cur_state = _safe_get_state(p, timeout=0.5)
            log(f"tts: not Playing in {start_timeout:.1f}s — прерываю "
                f"file={os.path.basename(file_path)} (state={_state_str(cur_state)})",
                tag="TTS", level="WARN")
            return False

        t_play = time.time()
        while (time.time() - t_play) < play_timeout:
            s = _safe_get_state(p, timeout=1.0)
            if s is None:
                return False
            if s in (vlc.State.Ended, vlc.State.Stopped):
                ok = True
                break
            if s == vlc.State.Error:
                log(f"tts: play error state=Error "
                    f"file={os.path.basename(file_path)}",
                    tag="TTS", level="ERROR")
                return False
            time.sleep(0.15)
        else:
            log(f"tts: play timeout {play_timeout:.1f}s "
                f"file={os.path.basename(file_path)}",
                tag="TTS", level="WARN")

        return ok

    except Exception as e:
        log(f"tts: play error: {e}", tag="TTS", level="ERROR")
        return False

    finally:
        _safe_release(p, timeout=2.0)
        _safe_media_release(media, timeout=1.0)