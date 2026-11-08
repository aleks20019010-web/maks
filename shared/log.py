# shared/log.py
# Общий лог для brain и workers.
# Пишет в data/logs/jarvis.log + stderr с тегом.
#
# FIX (этап 0.3): MIN_LEVEL + ротация.
#   Раньше: писалось всё, включая DEBUG; файл рос без границы.
#   Теперь:
#     - MAKS_LOG_LEVEL=DEBUG включает DEBUG (по умолчанию INFO);
#     - jarvis.log ротируется при > 10 МБ → jarvis.log.1, .1 → .2, ... .3;
#     - файл открыт постоянно (не open на каждый вызов), flush по строке.
#
# Почему «открыт постоянно»: playback логирует каждый трек, controls —
# каждое действие. open/close на каждую строку — лишний I/O. Держим
# файл открытым, flush — чтобы при краше последние строки были на диске.
# Ротация — через os.replace под локом; между процессами лок не нужен,
# потому что jarvis.log пишут все воркеры одновременно (это by design,
# ротация может «промахнуться» на одном воркере — не страшно, следующий
# вызов догонит).

import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from shared import config as cfg


_LOGS_DIR = Path(cfg.DATA_LOGS_DIR)   # FIX: было cfg.LOGS_DIR (BASE_DIR/logs),
                                       # теперь data/logs — данные отдельно от кода
_LOGS_DIR.mkdir(parents=True, exist_ok=True)
_LOG_FILE = _LOGS_DIR / "jarvis.log"

# ── уровни ─────────────────────────────────────────────────────
_LEVELS = {"DEBUG": 0, "INFO": 1, "WARN": 2, "ERROR": 3}
_MIN_LEVEL_NAME = os.environ.get("MAKS_LOG_LEVEL", "INFO").upper()
_MIN_LEVEL = _LEVELS.get(_MIN_LEVEL_NAME, _LEVELS["INFO"])

# ── ротация ────────────────────────────────────────────────────
_MAX_BYTES = 10 * 1024 * 1024
_BACKUP_COUNT = 3
# Проверять размер не на каждый вызов: на горячем пути (playback каждый
# трек) лишний getsize ни к чему. Раз в 1 сек или раз в 100 вызовов.
_ROTATE_CHECK_EVERY = 100
_ROTATE_CHECK_INTERVAL_SEC = 1.0

_file_lock = threading.Lock()
_file = None              # открытый файл (или None, если ещё не открыт)
_calls_since_check = 0
_last_check_ts = 0.0


def _open_file():
    """Открыть лог-файл на дозапись. Вызывается лениво при первом log()."""
    global _file
    with _file_lock:
        if _file is None:
            _file = open(_LOG_FILE, "a", encoding="utf-8")
    return _file


def _rotate_if_needed():
    """Проверить размер и ротировать. Под _file_lock, потому что
    закрывает/открывает _file и делает os.replace."""
    global _file, _calls_since_check, _last_check_ts

    now = time.time()
    _calls_since_check += 1
    if (_calls_since_check < _ROTATE_CHECK_EVERY
            and (now - _last_check_ts) < _ROTATE_CHECK_INTERVAL_SEC):
        return
    _calls_since_check = 0
    _last_check_ts = now

    with _file_lock:
        try:
            if _file is not None:
                _file.flush()
                _file.close()
                _file = None
        except Exception:
            _file = None

        try:
            size = _LOG_FILE.stat().st_size
        except FileNotFoundError:
            return
        except Exception:
            return

        if size < _MAX_BYTES:
            return

        # jarvis.log → .1, .1 → .2, ... .N-1 → .N (N = _BACKUP_COUNT)
        try:
            for i in range(_BACKUP_COUNT, 0, -1):
                src = _LOG_FILE if i == 1 else _LOG_FILE.with_suffix(f".log.{i - 1}")
                # with_suffix даёт "jarvis.log.1" — не "jarvis.1". Проверим:
                # jarvis.log.with_suffix(".log.1") == "jarvis.log.1" — да,
                # потому что suffix = ".log", замена на ".log.1".
                dst = _LOG_FILE.with_suffix(f".log.{i}")
                if src.exists():
                    os.replace(src, dst)
        except Exception:
            # Ротация — вспомогательная операция. Если упала (диск,
            # права) — пишем дальше в тот же файл, он вырастет, ну и ладно.
            pass


def log(msg: str, tag: str = "", level: str = "INFO"):
    """Записать в лог.

    tag   — префикс (BRAIN, ASR, TTS, MUSIC, SUPERVISOR, RUNTIME, ...).
    level — DEBUG, INFO, WARN, ERROR.

    DEBUG не пишется в файл, если MAKS_LOG_LEVEL не DEBUG. В stderr —
    то же самое: консоль смотрят глазами, DEBUG там мешает.
    """
    if _LEVELS.get(level, _LEVELS["INFO"]) < _MIN_LEVEL:
        return

    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    prefix = f"[{tag}]" if tag else ""
    line = f"[{ts}] [{level}] {prefix} {msg}"

    # stderr — чтобы видеть в консоли
    try:
        print(line, flush=True)
    except Exception:
        pass

    # файл
    try:
        _rotate_if_needed()
        f = _open_file()
        with _file_lock:
            f.write(line + "\n")
            f.flush()
    except Exception:
        pass