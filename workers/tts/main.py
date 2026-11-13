# workers/tts/main.py
# TTS worker: слушает tts_inbox, синтезирует, играет через VLC.
# Ducking музыки привязан к ASR (микрофон ВЫКЛ/ВКЛ → duck_on/duck_off).
# TTS worker про музыку не знает: только выставляет speaking в state_file,
# ASR читает его и управляет ducking.
#
# v8.8 (2026-11-13): speaking=True ДО synth (а не после).
#   Симптом: «Макс, включи король и шут» → TTS начинает synth (1–5с),
#   speaking=True появляется только ПОСЛЕ synth → ASR не делает duck_on
#   → музыка 100% → TTS начинает говорить ПОВЕРХ 100% музыки.
#   Плюс: brain _wait_tts_started(5с) не дожидается speaking=True →
#   music без ожидания → дёрганье.
#
#   Фикс: speaking=True ДО synth. ASR duck_on сразу (музыка 30%),
#   brain _wait_tts_started дожидается сразу, TTS говорит поверх 30%.
#   Если synth упал — finally: speaking=False. MusicManager.volume
#   не меняется, ducking не ломается.
#
# FIX (v8.3, 2026-11-12): защита от зависания TTS на play_tts_file.
#   play_tts_file вызывается в отдельном daemon-потоке с join(45с).
#   Если VLC завис — TTS-цикл не залипает: speaking=False гарантированно
#   сбрасывается в state_file, ASR оживает.
#
# FIX (день 29, вечер): duck_music_on/duck_music_off удалены.
#   - _say_one: убран duck_music_on() перед синтезом и duck_music_off()
#     в finally. Ducking делает ASR.
#   - _say_one: tts_state.set_speaking(False) только если был
#     set_speaking(True) (флаг spoke).
#   - _handle_say: TTS_ECHO_GUARD_SEC с защитой от TypeError/ValueError.
#   - main: при KeyboardInterrupt — сброс speaking=False.
#
# v6.2 (2026-11-05): rate НЕ передаётся в synth (mp3 всегда speed=1.0,
#   кэш всегда попадает). rate из personality_core (prosody) передаётся
#   в player.play_tts_file → VLC set_rate (тон сохраняется).

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


# v8.3: максимум на одну фразу (синтез + play + VLC-таймауты).
# play_tts_file внутри имеет свои таймауты (start 3с + play 30с),
# но если VLC зависнет в p.play() или p.release() — общий watchdog
# оборвёт через PLAY_HARD_TIMEOUT_SEC.
PLAY_HARD_TIMEOUT_SEC = 45.0


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


def _play_with_watchdog(path: str, rate) -> bool:
    """v8.3: play_tts_file в daemon-потоке с join(45с).

    Если VLC завис — TTS-цикл не залипает. VLC-поток остаётся (утечка),
    но лучше утечка, чем залипший ASR.
    """
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
    """Синтезировать + проиграть.

    v8.8: speaking=True ДО synth (а не после). ASR duck_on сразу
    (музыка 30%), brain _wait_tts_started дожидается сразу.
    TTS говорит поверх 30% музыки. Если synth упал — finally: speaking=False.

    v8.3: play_tts_file в отдельном потоке с watchdog. speaking=False
    гарантированно сбрасывается в finally, даже если VLC завис.
    """
    temp = _unique_temp_file()
    spoke = False

    try:
        # v8.8: speaking=True ДО synth. ASR duck_on сразу (музыка 30%),
        # brain _wait_tts_started дожидается. Без «TTS поверх 100%».
        tts_state.set_speaking(True, text)
        spoke = True

        ok = _synth(text, temp, None, pitch, emotion, cache=cache)
        if not ok:
            log("tts: синтез не удался, пропуск", tag="TTS", level="WARN")
            return False

        played = _play_with_watchdog(temp, rate)
        return played
    except Exception as e:
        log(f"tts: play error: {e}", tag="TTS", level="ERROR")
        return False
    finally:
        if spoke:
            # speaking=False гарантированно, даже если VLC завис.
            # last_text остаётся для echo-фильтра в ASR.
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
    if rate: _rpe.append(f"rate={rate}")
    if pitch: _rpe.append(f"pitch={pitch}")
    if emotion: _rpe.append(f"emotion={emotion}")
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