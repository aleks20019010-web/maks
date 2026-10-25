"""
shared/lock.py — lock-файл Макса.

Один supervisor. Один lock. Второй — выходит.

data/jarvis.lock — JSON: {pid, started_at}
"""

import json
import os
from datetime import datetime
from pathlib import Path

import psutil

LOCK_FILE = Path("data/jarvis.lock")


def acquire() -> bool:
    """Взять lock. True — взяли. False — уже занят живым процессом."""
    if LOCK_FILE.exists():
        try:
            data = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
            pid = data.get("pid")
            if pid and psutil.pid_exists(pid):
                return False
        except Exception:
            pass
        try:
            LOCK_FILE.unlink()
        except Exception:
            pass

    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOCK_FILE.write_text(
        json.dumps({
            "pid": os.getpid(),
            "started_at": datetime.now().isoformat(),
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    return True


def release():
    """Удалить lock (только если наш)."""
    try:
        if LOCK_FILE.exists():
            data = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
            if data.get("pid") == os.getpid():
                LOCK_FILE.unlink()
    except Exception:
        pass


def get_holder() -> dict | None:
    """Вернуть данные lock (кто держит). None — если нет/битый."""
    try:
        if LOCK_FILE.exists():
            return json.loads(LOCK_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None