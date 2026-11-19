# workers/tts/player.py
# VLC-плеер для TTS с переиспользуемым MediaPlayer (без переоткрытия WASAPI на каждую фразу).
#
# ── TTS TELEMETRY (2026-11-19) ────────────────────────────────
#   При play_tts_file() заранее считаем RMS-огибающую файла (по 45 мс окнам
#   через wave/audioread) и в фоновом потоке во время проигрывания
#   обновляем update_avatar_telemetry(tts_level=...). Огибающая — 0..1
#   после нормализации rms/0.18. Когда VLC закончил — tts_level=0.0.
#   Если файл не читается (MP3 без ffmpeg, битый и т.п.) — телеметрия
#   молчит, TTS всё равно играет.

import os
import threading
import time

import vlc

from shared import config as cfg
from shared.log import log
from shared.ui_bridge import update_avatar_telemetry


TTS_VOLUME = 100

# TTS TELEMETRY (2026-11-19): окно огибающей и частота обновления.
_TTS_TELEMETRY_WINDOW_SEC = 0.045
_TTS_TELEMETRY_NORM = 0.18

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


# ── TTS TELEMETRY (2026-11-19): огибающая аудиофайла ─────────
def _envelope_from_file(file_path: str) -> tuple[list[float], float, int]:
    """Вернуть (огибающая, длительность_сек, шаг_мс).

    Огибающая — список RMS по окнам 45 мс (float, 0..1 после /0.18).
    Пустой список — файл не читается (MP3 без ffmpeg, битый и т.п.).
    """
    try:
        import wave
        import numpy as np

        if file_path.lower().endswith(".wav"):
            with wave.open(file_path, "rb") as wf:
                sr = wf.getframerate()
                ch = wf.getnchannels()
                sw = wf.getsampwidth()
                frames = wf.readframes(wf.getnframes())
            if sw == 2:
                arr = np.frombuffer(frames, dtype=np.int16)
            elif sw == 4:
                arr = np.frombuffer(frames, dtype=np.int32)
            else:
                return [], 0.0, 45
            if ch > 1:
                arr = arr.reshape(-1, ch).mean(axis=1)
            audio = arr.astype(np.float32) / 32768.0 if sw == 2 else arr.astype(np.float32) / 2147483648.0
        else:
            # MP3/OGG и пр. — пробуем через audioread (если установлен)
            try:
                import audioread
                import numpy as np
                chunks = []
                with audioread.audio_open(file_path) as f:
                    sr = f.samplerate
                    ch = f.channels
                    for buf in f:
                        chunks.append(buf)
                if not chunks:
                    return [], 0.0, 45
                raw = b"".join(chunks)
                arr = np.frombuffer(raw, dtype=np.int16)
                if ch > 1:
                    arr = arr.reshape(-1, ch).mean(axis=1)
                audio = arr.astype(np.float32) / 32768.0
            except Exception:
                return [], 0.0, 45

        win = max(1, int(_TTS_TELEMETRY_WINDOW_SEC * sr))
        n_win = len(audio) // win
        if n_win <= 0:
            return [], 0.0, int(_TTS_TELEMETRY_WINDOW_SEC * 1000)

        env: list[float] = []
        for i in range(n_win):
            w = audio[i * win:(i + 1) * win]
            rms = float(np.sqrt(np.mean(w * w)))
            env.append(min(1.0, max(0.0, rms / _TTS_TELEMETRY_NORM)))
        return env, len(audio) / sr, int(_TTS_TELEMETRY_WINDOW_SEC * 1000)
    except Exception:
        return [], 0.0, int(_TTS_TELEMETRY_WINDOW_SEC * 1000)


def _telemetry_worker(env: list[float], step_ms: int, stop_evt: threading.Event, player):
    """Фоновый поток: во время проигрывания шлёт tts_level по огибающей."""
    t0 = time.time()
    i = 0
    try:
        while not stop_evt.is_set():
            if player.get_state() not in (vlc.State.Playing, vlc.State.Paused):
                break
            elapsed_ms = (time.time() - t0) * 1000.0
            target = int(elapsed_ms / max(1, step_ms))
            if target < 0:
                target = 0
            while i <= target and i < len(env):
                i += 1
            lvl = env[i - 1] if i > 0 else 0.0
            update_avatar_telemetry(tts_level=round(lvl, 3))
            time.sleep(step_ms / 1000.0)
    finally:
        update_avatar_telemetry(tts_level=0.0)


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

    # TTS TELEMETRY: запускаем фоновый поток огибающей на время проигрывания.
    stop_evt = threading.Event()
    env, _, step_ms = _envelope_from_file(file_path)
    if env:
        threading.Thread(
            target=_telemetry_worker,
            args=(env, step_ms, stop_evt, p),
            daemon=True,
            name="tts-telemetry",
        ).start()

    t_play = time.time()
    try:
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
    finally:
        stop_evt.set()


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
        # Гарантированно сбрасываем tts_level, даже если play() не дошёл до _telemetry_worker
        try:
            update_avatar_telemetry(tts_level=0.0)
        except Exception:
            pass