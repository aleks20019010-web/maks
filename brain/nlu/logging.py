# brain/nlu/logging.py
# Запись DiagnosticEvent в logs/nlu.log.
#
# ПОЧЕМУ ОТДЕЛЬНЫЙ ФАЙЛ, А НЕ shared/log.py:
#   shared/log.py — общий лог (data/logs/jarvis.log). NLU — отдельный,
#   структурированный поток: stage / intent / entity_state / decision /
#   confidence. Его хочется грепать отдельно (`grep "stage=entity" nlu.log`),
#   не мешая с TTS/ASR/MUSIC. Плюс — формат отличается: не «текст», а
#   key=value пары, чтобы парсить.
#
# ЧЕГО НЕ ДЕЛАЕТ:
#   - не форматирует текст для пользователя (это responses/*.json)
#   - не пишет в jarvis.log (это shared/log.py)
#   - не хранит историю (это NLUContext.history)
#   Только: DiagnosticEvent → строка → logs/nlu.log + stderr.
#
# УРОВНИ:
#   DEBUG — каждый этап (normalizer, intent, entity, decision) для отладки.
#   INFO  — итог pipeline (intent + decision + confidence).
#   WARN  — UNRESOLVED, AMBIGUOUS, FALLBACK, TTL истёк.
#   ERROR — исключение в pipeline.
#
# ФОРМАТ:
#   [2026-10-30 14:23:11] [INFO] [stage=pipeline] [intent=MUSIC_PLAY]
#     [decision=EXECUTE] [conf=0.87] reason=FOUND artist='Ария' → play_artist
#
#   data-поля (intent, decision, conf, entity_state) — key=value, чтобы
#   `grep "intent=MUSIC_PLAY" nlu.log` работал.

import os
import threading
from datetime import datetime

from shared import config as cfg
from brain.nlu.types import DiagnosticEvent


# ── Файл ────────────────────────────────────────────────────
# Отдельно от data/logs/jarvis.log — чтобы NLU грепался независимо.
NLU_LOG_FILE = cfg.LOGS_DIR / "nlu.log"

# Порог: ниже — не пишем. Можно поднять до "WARN" в проде, чтобы не
# раздувать лог. По умолчанию "DEBUG" — как в architecture.md.
_MIN_LEVEL = os.environ.get("MAKS_NLU_LOG_LEVEL", "DEBUG").upper()

_LEVEL_ORDER = {"DEBUG": 0, "INFO": 1, "WARN": 2, "ERROR": 3}

_write_lock = threading.Lock()


def _should_log(level: str) -> bool:
    """Проверить, писать ли событие этого уровня."""
    return _LEVEL_ORDER.get(level, 1) >= _LEVEL_ORDER.get(_MIN_LEVEL, 0)


def _format_data(data: dict) -> str:
    """dict → key=value пары. None и пустые — пропускаем.

    Строки с пробелами — в кавычках: artist='Король и Шут'.
    """
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


def log_diagnostic(event: DiagnosticEvent) -> None:
    """Записать DiagnosticEvent в logs/nlu.log и stderr.

    Не бросает. Если файл недоступен — пишет в stderr, молча.
    """
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

    # stderr — всегда (для живой отладки)
    try:
        print(line, flush=True)
    except Exception:
        pass

    # Файл — с локом, атомарно не нужно (append).
    try:
        with _write_lock:
            with open(NLU_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:
        pass


# ── Удобные обёртки ─────────────────────────────────────────
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