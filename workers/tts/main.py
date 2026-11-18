# workers/tts/main.py
# TTS worker: слушает tts_inbox, синтезирует, играет через VLC.

import os
import sys
import threading
import time
import uuid

from shared import ipc
from shared import protocol
from shared import config as cfg
from shared.log import log
from workers.tts import engine as tts_engine
from workers.tts import cache as tts_cache
from workers.tts import pending as tts_pending
from workers.tts import player as tts_player
from workers.tts import state_file as tts_state


PLAY_HARD_TIMEOUT_SEC = 45.0
SYNTH_MAX_ATTEMPTS = 2
SYNTH_TOTAL_BUDGET_SEC = 7.0

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        _engine = tts_engine.YandexEngine()
        log(f"tts: движок {_engine.name}", tag="TTS")
    return _engine


def _unique_temp_file() -> str:
    return os.path.join(cfg.CACHE_DIR, f'_tts_{uuid.uuid4().hex[:8]}.mp3')


def _cleanup_temp(path: str):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def _synth(text, out_path, rate=None, pitch=None, emotion=None, cache=True) -> tuple[bool, bool]:
    """Вернуть (ok, from_cache)."""
    engine = _get_engine()

    if cache and tts_cache.cache_hit(engine, text, out_path, rate, pitch, emotion):
        return True, True

    t0 = time.time()
    for attempt in range(SYNTH_MAX_ATTEMPTS):
        if time.time() - t0 >= SYNTH_TOTAL_BUDGET_SEC:
            log(f"tts: бюджет синтеза ({SYNTH_TOTAL_BUDGET_SEC:.1f}с) исчерпан",
                tag="TTS", level="WARN")
            break
        try:
            if os.path.exists(out_path):
                try:
                    os.remove(out_path)
                except OSError:
                    pass
            ok = engine.synth(text, out_path, rate=rate, pitch=pitch, emotion=emotion)
            if ok and os.path.exists(out_path) and os.path.getsize(out_path) > 1024:
                if cache:
                    tts_cache.cache_save(engine, text, out_path, rate, pitch, emotion)
                return True, False
            log(f"tts: попытка {attempt + 1}/{SYNTH_MAX_ATTEMPTS}: не удалось",
                tag="TTS", level="WARN")
        except Exception as e:
            log(f"tts: попытка {attempt + 1}/{SYNTH_MAX_ATTEMPTS}: {e}",
                tag="TTS", level="WARN")

        if attempt + 1 < SYNTH_MAX_ATTEMPTS and (time.time() - t0) < SYNTH_TOTAL_BUDGET_SEC:
            time.sleep(0.3)

    if cache:
        tts_pending.add(text, rate, pitch, emotion)
    return False, False


def _play_with_watchdog(path: str, rate) -> bool:
    result = {"ok": False, "exc": None}

    def run():
        try:
            result["ok"] = tts_player.play_tts_file(path, rate=rate)
        except Exception as e:
            result["exc"] = e

    t = threading.Thread(target=run, daemon=True, name="tts-play")
    t.start()
    t.join(PLAY_HARD_TIMEOUT_SEC)

    if t.is_alive():
        log(f"tts: play_tts_file завис >{PLAY_HARD_TIMEOUT_SEC:.0f}с — "
            f"принудительный выход из _say_one (VLC-поток остаётся)",
            tag="TTS", level="ERROR")
        return False

    if result["exc"] is not None:
        log(f"tts: play_tts_file exception: {result['exc']}",
            tag="TTS", level="ERROR")
        return False

    return bool(result["ok"])


def _say_one(text, rate=None, pitch=None, emotion=None, cache=True) -> bool:
    temp = _unique_temp_file()
    spoke = False
    t_total = time.time()

    try:
        tts_state.set_speaking(True, text)
        spoke = True

        t_synth = time.time()
        ok, from_cache = _synth(text, temp, None, pitch, emotion, cache=cache)
        dt_synth = time.time() - t_synth
        if not ok:
            log(f"tts: синтез не удался ({dt_synth:.2f}s), пропуск", tag="TTS", level="WARN")
            return False

        t_play = time.time()
        played = _play_with_watchdog(temp, rate)
        dt_play = time.time() - t_play
        src = "cache" if from_cache else "yandex"
        log(f"tts: готово ok={played} src={src} synth={dt_synth:.2f}s "
            f"play={dt_play:.2f}s total={time.time() - t_total:.2f}s", tag="TTS")
        return played
    except Exception as e:
        log(f"tts: play error: {e}", tag="TTS", level="ERROR")
        return False
    finally:
        if spoke:
            tts_state.set_speaking(False, text)
        _cleanup_temp(temp)


def _handle_say(msg: protocol.Message):
    text = msg.text
    if not text:
        return

    rate = msg.data.get("rate")
    pitch = msg.data.get("pitch")
    emotion = msg.data.get("emotion")
    cache = msg.data.get("cache", True)
    sync = msg.data.get("sync", True)

    _rpe = []
    if rate:
        _rpe.append(f"rate={rate}")
    if pitch:
        _rpe.append(f"pitch={pitch}")
    if emotion:
        _rpe.append(f"emotion={emotion}")
    _rpe_str = f", {', '.join(_rpe)}" if _rpe else ""

    log(f"tts: говорю «{text}» (sync={sync}{_rpe_str})", tag="TTS")
    ok = _say_one(text, rate=rate, pitch=pitch, emotion=emotion, cache=cache)

    if ok:
        try:
            guard = float(getattr(cfg, "TTS_ECHO_GUARD_SEC", 0.3))
        except (TypeError, ValueError):
            guard = 0.3
        if guard > 0:
            time.sleep(guard)


def main():
    log("tts: старт", tag="TTS")
    ipc.init()

    try:
        from workers.tts.player import _get_instance
        _get_instance()
        log("tts: VLC instance прогрет", tag="TTS")
    except Exception as e:
        log(f"tts: VLC warmup: {e}", tag="TTS", level="WARN")

    try:
        _get_engine()
    except Exception as e:
        log(f"tts: не удалось загрузить движок: {e}", tag="TTS", level="ERROR")
        sys.exit(1)

    tts_state.set_speaking(False, "")

    log("tts: слушаю tts_inbox", tag="TTS")

    while True:
        msg = ipc.get(protocol.TTS_INBOX, timeout=1.0)
        if msg is None:
            continue

        try:
            if msg.type == "say":
                _handle_say(msg)
            else:
                log(f"tts: неизвестный тип {msg.type}", tag="TTS", level="WARN")
        except Exception as e:
            log(f"tts: ошибка: {e}", tag="TTS", level="ERROR")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("tts: остановка по Ctrl+C", tag="TTS")
        try:
            tts_state.set_speaking(False, "")
        except Exception:
            pass
        sys.exit(0)