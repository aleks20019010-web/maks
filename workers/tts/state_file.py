# workers/tts/state_file.py
# TTS пишет своё состояние в файл — ASR, brain и UI читают.
#
# state: {
#   "speaking": bool,
#   "last_text": str,
#   "ts": float,
#   "phrase_seq": int,  # монотонный счётчик реплик (инкрементится на set_speaking(True))
# }

import json
import os
import threading
import time

from shared import config as cfg
from shared.log import log


STATE_FILE = os.path.join(cfg.DATA_DIR, "tts_state.json")


def _read_raw() -> dict:
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def set_speaking(speaking: bool, last_text: str = ""):
    """TTS сейчас говорит? Надёжно записать состояние в файл (с учётом блокировок Windows)."""
    now = time.time()
    prev = _read_raw()
    is_speaking = bool(speaking)
    prev_seq = int(prev.get("phrase_seq", 0) or 0)
    phrase_seq = (prev_seq + 1) if is_speaking else prev_seq

    data = {
        "speaking": is_speaking,
        "last_text": last_text or "",
        "ts": now,
        "phrase_seq": phrase_seq,
    }
    payload = json.dumps(data, ensure_ascii=False)
    tmp = f"{STATE_FILE}.{os.getpid()}.{threading.get_ident()}.tmp"

    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(payload)

        # В Windows os.replace может получить PermissionError, если ASR/BRAIN/UI
        # читают файл в эту миллисекунду — делаем до 15 быстрых попыток.
        for _ in range(15):
            try:
                os.replace(tmp, STATE_FILE)
                return
            except PermissionError:
                time.sleep(0.005)

        # Fallback: прямая перезапись, если файл всё ещё занят читателем
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            f.write(payload)
    except Exception as e:
        log(f"tts: state_file write error: {e}", tag="TTS", level="WARN")
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def get_state() -> dict:
    """Прочитать состояние TTS."""
    data = _read_raw()
    if not data:
        return {
            "speaking": False,
            "last_text": "",
            "ts": 0.0,
            "phrase_seq": 0,
        }
    try:
        return {
            "speaking": bool(data.get("speaking", False)),
            "last_text": str(data.get("last_text", "") or ""),
            "ts": float(data.get("ts", 0.0) or 0.0),
            "phrase_seq": int(data.get("phrase_seq", 0) or 0),
        }
    except Exception:
        return {
            "speaking": False,
            "last_text": "",
            "ts": 0.0,
            "phrase_seq": 0,
        }