# brain/nlu/logging.py
# Запись DiagnosticEvent в logs/nlu.log.

import os
import threading
from datetime import datetime

from shared import config as cfg
from brain.nlu.types import DiagnosticEvent


NLU_LOG_FILE = cfg.LOGS_DIR / "nlu.log"
_MAX_LOG_BYTES = 5 * 1024 * 1024  # 5 МБ

_MIN_LEVEL = os.environ.get("MAKS_NLU_LOG_LEVEL", "DEBUG").upper()
_LEVEL_ORDER = {"DEBUG": 0, "INFO": 1, "WARN": 2, "ERROR": 3}
_write_lock = threading.Lock()


def _should_log(level: str) -> bool:
    return _LEVEL_ORDER.get(level, 1) >= _LEVEL_ORDER.get(_MIN_LEVEL, 0)


def _format_data(data: dict) -> str:
    if not data:
        return ""
    parts = []
    for k, v in data.items():
        if v is None or v == "":
            continue
        if isinstance(v, str) and (" " in v or "'" in v or '"' in v):
            parts.append(f"{k}={v!r}")
        else:
            parts.append(f"{k}={v}")
    return " ".join(parts)


def _rotate_if_needed() -> None:
    try:
        if NLU_LOG_FILE.exists() and NLU_LOG_FILE.stat().st_size >= _MAX_LOG_BYTES:
            backup = NLU_LOG_FILE.with_suffix(".log.1")
            if backup.exists():
                backup.unlink()
            NLU_LOG_FILE.replace(backup)
    except Exception:
        pass


def log_diagnostic(event: DiagnosticEvent) -> None:
    if event is None:
        return
    level = (event.level or "INFO").upper()
    if not _should_log(level):
        return

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    stage = event.stage or "-"
    msg = event.message or ""
    data_str = _format_data(event.data)

    line = f"[{ts}] [{level}] [stage={stage}]"
    if data_str:
        line += f" {data_str}"
    if msg:
        line += f" {msg}"

    try:
        print(line, flush=True)
    except Exception:
        pass

    try:
        with _write_lock:
            _rotate_if_needed()
            with open(NLU_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:
        pass


def debug(stage: str, message: str = "", **data) -> None:
    log_diagnostic(DiagnosticEvent(stage=stage, level="DEBUG",
                                   message=message, data=data))


def info(stage: str, message: str = "", **data) -> None:
    log_diagnostic(DiagnosticEvent(stage=stage, level="INFO",
                                   message=message, data=data))


def warn(stage: str, message: str = "", **data) -> None:
    log_diagnostic(DiagnosticEvent(stage=stage, level="WARN",
                                   message=message, data=data))


def error(stage: str, message: str = "", **data) -> None:
    log_diagnostic(DiagnosticEvent(stage=stage, level="ERROR",
                                   message=message, data=data))