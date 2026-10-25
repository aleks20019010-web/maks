# shared/log.py
# Общий лог для brain и workers.
# Пишет в data/logs/jarvis.log + stderr с тегом.

import os
import sys
from datetime import datetime
from pathlib import Path

from shared import config as cfg

_LOGS_DIR = Path(cfg.LOGS_DIR)
_LOGS_DIR.mkdir(parents=True, exist_ok=True)

_LOG_FILE = _LOGS_DIR / "jarvis.log"


def log(msg: str, tag: str = "", level: str = "INFO"):
    """Записать в лог.

    tag — префикс (BRAIN, ASR, TTS, MUSIC, SUPERVISOR).
    level — INFO, WARN, ERROR.
    """
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    prefix = f"[{tag}]" if tag else ""
    line = f"[{ts}] [{level}] {prefix} {msg}"

    # В stderr
    try:
        print(line, flush=True)
    except Exception:
        pass

    # В файл
    try:
        with open(_LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(line + "\n")
    except Exception:
        pass