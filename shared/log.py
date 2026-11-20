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
#
# ── FIX (2026-11-20): ротация на Windows без PermissionError ─────
#   Симптом: ротация падала с PermissionError (WinError 32/5) —
#   jarvis.log открыт другими воркерами (brain, asr, tts, music, ui),
#   os.replace не может переименовать файл, открытый кем-то ещё.
#   В итоге jarvis.log рос без границы, ротация «проглатывалась»
#   в except Exception: pass, и лог через неделю — 500 МБ.
#
#   Фикс: replace старого jarvis.log → .1 заменён на
#     shutil.copyfile(jarvis.log, jarvis.log.1) + truncate(jarvis.log).
#   copyfile читает файл (даже открытый на запись другими процессами —
#   NTFS разрешает concurrent read), а truncate в "w" — обнуляет
#   содержимое, оставляя тот же inode. Другие воркеры продолжают
#   писать в тот же файл (их _file handle валиден), новые строки
#   идут уже в чистый jarvis.log.
#
#   Остальные .1 → .2 → .3 — по-прежнему os.replace: эти файлы
#   никто не держит открытыми, replace проходит. Если вдруг упадёт
#   (антивирус, права) — глотаем, не валим логирование.

import os
import shutil
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
    закрывает/открывает _file и делает os.replace/copyfile.

    FIX (2026-11-20): для Windows — не os.replace старого jarvis.log
    (его держат открытыми другие воркеры → PermissionError), а
    copyfile + truncate. Остальные бэкапы (.1 → .2 → .3) — replace,
    их никто не держит.
    """
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
            # Сдвигаем старые бэкапы: .1 → .2, .2 → .3 (если есть).
            # Эти файлы никто не держит открытыми — os.replace безопасен.
            for i in range(_BACKUP_COUNT, 1, -1):
                src = _LOG_FILE.with_suffix(f".log.{i - 1}")
                dst = _LOG_FILE.with_suffix(f".log.{i}")
                if src.exists():
                    os.replace(src, dst)

            # FIX (2026-11-20): jarvis.log → .1 через copyfile + truncate.
            # os.replace(jarvis.log, jarvis.log.1) на Windows падает с
            # PermissionError, потому что jarvis.log открыт на запись
            # другими воркерами. copyfile читает (concurrent read OK),
            # truncate в "w" обнуляет содержимое, оставляя тот же inode —
            # другие воркеры продолжают писать в тот же (уже пустой) файл.
            shutil.copyfile(_LOG_FILE, _LOG_FILE.with_suffix(".log.1"))
            with open(_LOG_FILE, "w", encoding="utf-8"):
                pass
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