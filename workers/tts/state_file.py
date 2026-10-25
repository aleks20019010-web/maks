# workers/tts/state_file.py
# TTS пишет своё состояние в файл — ASR читает.
#
# state: {
#   "speaking": bool,
#   "last_text": str,
#   "ts": float,
# }
#
# Атомарная запись (tmp → rename).

import json
import os
import time

from shared import config as cfg


STATE_FILE = os.path.join(cfg.DATA_DIR, "tts_state.json")


def set_speaking(speaking: bool, last_text: str = ""):
    """TTS сейчас говорит? Записать в файл."""
    data = {
        "speaking": bool(speaking),
        "last_text": last_text or "",
        "ts": time.time(),
    }
    try:
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, STATE_FILE)
    except Exception:
        pass


def get_state() -> dict:
    """Прочитать состояние TTS."""
    if not os.path.exists(STATE_FILE):
        return {"speaking": False, "last_text": "", "ts": 0.0}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("not dict")
        return {
            "speaking": bool(data.get("speaking", False)),
            "last_text": str(data.get("last_text", "") or ""),
            "ts": float(data.get("ts", 0.0) or 0.0),
        }
    except Exception:
        return {"speaking": False, "last_text": "", "ts": 0.0}