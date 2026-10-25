# workers/tts/main.py
# TTS worker: слушает tts_inbox, синтезирует, играет через VLC.
# Ducking музыки — через IPC (music_inbox).
# Состояние (speaking) — пишет в data/tts_state.json.

import os
import sys
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


def _synth(text, out_path, rate=None, pitch=None, emotion=None, cache=True) -> bool:
    engine = _get_engine()

    if cache and tts_cache.cache_hit(engine, text, out_path, rate, pitch, emotion):
        return True

    for attempt in range(3):
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
                return True
            else:
                log(f"tts: попытка {attempt+1}: не удалось", tag="TTS", level="WARN")
        except Exception as e:
            log(f"tts: попытка {attempt+1}: {e}", tag="TTS", level="WARN")
        if attempt < 2:
            wait = 0.5 * (2 ** attempt)
            time.sleep(wait)

    if cache:
        tts_pending.add(text, rate, pitch, emotion)
    return False


def _say_one(text, rate=None, pitch=None, emotion=None, cache=True) -> bool:
    """Синтезировать + проиграть. Ducking музыки — через IPC."""
    temp = _unique_temp_file()

    ok = _synth(text, temp, rate, pitch, emotion, cache=cache)
    if not ok:
        _cleanup_temp(temp)
        log("tts: синтез не удался, пропуск", tag="TTS", level="WARN")
        return False

    try:
        # TTS начинает говорить
        tts_state.set_speaking(True, text)
        tts_player.duck_music_on()
        tts_player.play_tts_file(temp)
        return True
    except Exception as e:
        log(f"tts: play error: {e}", tag="TTS", level="ERROR")
        return False
    finally:
        tts_player.duck_music_off()
        # TTS закончил говорить — last_text остаётся для echo-фильтра
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

    # Логируем rate/pitch/emotion — чтобы видеть, доходят ли они из brain.
    _rpe = []
    if rate: _rpe.append(f"rate={rate}")
    if pitch: _rpe.append(f"pitch={pitch}")
    if emotion: _rpe.append(f"emotion={emotion}")
    _rpe_str = f", {', '.join(_rpe)}" if _rpe else ""

    log(f"tts: говорю «{text}» (sync={sync}{_rpe_str})", tag="TTS")
    ok = _say_one(text, rate=rate, pitch=pitch, emotion=emotion, cache=cache)

    if ok:
        time.sleep(cfg.TTS_ECHO_GUARD_SEC)


def main():
    log("tts: старт", tag="TTS")
    ipc.init()

    try:
        _get_engine()
    except Exception as e:
        log(f"tts: не удалось загрузить движок: {e}", tag="TTS", level="ERROR")
        sys.exit(1)

    # На старте — "не говорю"
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
        sys.exit(0)