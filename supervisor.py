"""
supervisor.py — наблюдатель за воркерами Макса.

Что делает:
  1. Lock-файл: один supervisor. Второй — выход.
  2. Инициализирует IPC (файловая шина) + ЧИСТИТ старые сообщения.
  3. Запускает brain + workers (shell=False, cmd списком).
  4. Следит за ними. Упал → перезапуск. Перед restart — убить всё дерево.
  5. Ночной перезапуск всех воркеров (4:30).
  6. RAM-лимиты (страховка).
  7. Мягкая остановка по Ctrl+C.

FIX (день 28, аудит 1.1): для brain проверяется exit-флаг
  (data/brain_exit.flag). Если фреш-флаг (<60 сек) — brain вышел
  штатно по команде «выход», supervisor НЕ перезапускает.
  Раньше sys.exit(0) → supervisor видел «штатно» → перезапускал.

Запуск:
    python supervisor.py
"""

import json
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import psutil

BASE_DIR = Path(__file__).parent.resolve()
CONFIG_FILE = BASE_DIR / "config" / "workers.json"
EXIT_FLAG = BASE_DIR / "data" / "brain_exit.flag"
EXIT_FLAG_TTL_SEC = 60.0

_workers = []
_stopping = False


def log(msg: str, level: str = "INFO"):
    from shared.log import log as _log
    _log(msg, tag="SUPERVISOR", level=level)


# ── Worker ─────────────────────────────────────────────────────
class Worker:
    def __init__(self, cfg: dict):
        self.name = cfg["name"]

        cmd = cfg["cmd"]
        if isinstance(cmd, str):
            cmd = cmd.split()
        self.cmd: list[str] = cmd

        self.cwd = cfg.get("cwd", str(BASE_DIR))
        self.auto_restart = cfg.get("auto_restart", True)
        self.critical = cfg.get("critical", False)
        self.ram_limit_mb = cfg.get("ram_limit_mb", 500)

        self.process: subprocess.Popen | None = None
        self.started_at: float = 0.0
        self.crash_times: list[float] = []

    def start(self):
        log(f"[{self.name}] старт: {' '.join(self.cmd)}")
        self.process = subprocess.Popen(
            self.cmd,
            shell=False,
            cwd=self.cwd,
        )
        self.started_at = time.time()
        log(f"[{self.name}] PID={self.process.pid}")

    def is_alive(self) -> bool:
        if self.process is None:
            return False
        return self.process.poll() is None

    def _kill_tree(self, pid: int):
        try:
            parent = psutil.Process(pid)
            children = parent.children(recursive=True)
            for child in children:
                try:
                    child.terminate()
                except Exception:
                    pass
            try:
                parent.terminate()
            except Exception:
                pass

            gone, alive = psutil.wait_procs(children + [parent], timeout=5)
            for p in alive:
                try:
                    p.kill()
                except Exception:
                    pass
        except psutil.NoSuchProcess:
            pass
        except Exception as e:
            log(f"[{self.name}] kill_tree ошибка: {e}", level="WARN")

    def restart(self, reason: str = ""):
        if self.process is not None and self.is_alive():
            log(f"[{self.name}] перезапуск ({reason})")
            self.stop()
        elif self.process is not None:
            self._kill_tree(self.process.pid)

        deadline = time.time() + 5.0
        while time.time() < deadline:
            if self.process is None or self.process.poll() is not None:
                break
            time.sleep(0.1)

        time.sleep(0.3)
        self.start()

    def stop(self):
        if self.process is None:
            return
        if self.is_alive():
            log(f"[{self.name}] остановка")
            self._kill_tree(self.process.pid)
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                log(f"[{self.name}] kill (timeout)")
                try:
                    self.process.kill()
                except Exception:
                    pass
        else:
            self._kill_tree(self.process.pid)
        self.process = None

    def ram_mb(self) -> float:
        if self.process is None or not self.is_alive():
            return 0.0
        try:
            p = psutil.Process(self.process.pid)
            total = p.memory_info().rss
            for child in p.children(recursive=True):
                try:
                    total += child.memory_info().rss
                except Exception:
                    pass
            return total / (1024 * 1024)
        except Exception:
            return 0.0

    def _check_exit_flag(self) -> bool:
        """Аудит 1.1: brain вышел штатно? Проверить фреш-флаг, удалить."""
        if self.name != "brain":
            return False
        if not EXIT_FLAG.exists():
            return False
        try:
            ts = float(EXIT_FLAG.read_text(encoding="utf-8").strip())
            age = time.time() - ts
            if age > EXIT_FLAG_TTL_SEC:
                log(f"[brain] exit-флаг устарел ({age:.0f}s) — игнор, удаляю",
                    level="WARN")
                try:
                    EXIT_FLAG.unlink()
                except Exception:
                    pass
                return False
            log(f"[brain] exit-флаг свежий ({age:.0f}s) — не перезапускаю",
                level="INFO")
            try:
                EXIT_FLAG.unlink()
            except Exception:
                pass
            return True
        except Exception as e:
            log(f"[brain] exit-флаг битый: {e}", level="WARN")
            try:
                EXIT_FLAG.unlink()
            except Exception:
                pass
            return False

    def check(self, crash_window: float, crash_max: int, restart_delay: float) -> bool:
        global _stopping

        if self.is_alive():
            return True

        if _stopping:
            return False

        # Аудит 1.1: brain вышел штатно по «выход» — не перезапускаем
        if self._check_exit_flag():
            return False

        if not self.auto_restart:
            log(f"[{self.name}] упал, авто-перезапуск выключен", level="WARN")
            return False

        now = time.time()
        self.crash_times = [t for t in self.crash_times if now - t < crash_window]
        self.crash_times.append(now)

        if len(self.crash_times) >= crash_max:
            log(f"[{self.name}] УПАЛ {crash_max} раз за {crash_window:.0f}с — СТОП", level="ERROR")
            if self.critical:
                log(f"[{self.name}] критичный воркер умер — останавливаем всё", level="ERROR")
                _stopping = True
            return False

        log(f"[{self.name}] упал, перезапуск через {restart_delay}с", level="WARN")
        time.sleep(restart_delay)
        self.restart("crash")
        return True


# ── Загрузка конфига ───────────────────────────────────────────
def load_config() -> dict:
    if not CONFIG_FILE.exists():
        log(f"❌ Не найден {CONFIG_FILE}", level="ERROR")
        sys.exit(1)
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


# ── Главный цикл ───────────────────────────────────────────────
def main():
    global _workers, _stopping

    from shared import lock
    if not lock.acquire():
        holder = lock.get_holder()
        log(f"❌ Макс уже запущен (PID {holder.get('pid') if holder else '?'}). Выход.", level="ERROR")
        sys.exit(1)
    log(f"🔒 lock взят (PID {__import__('os').getpid()})")

    cfg = load_config()
    sup_cfg = cfg.get("supervisor", {})

    restart_delay = sup_cfg.get("restart_delay_sec", 3.0)
    crash_window = sup_cfg.get("crash_window_sec", 60.0)
    crash_max = sup_cfg.get("crash_max", 5)
    night_hour = sup_cfg.get("night_restart_hour", 4)
    night_minute = sup_cfg.get("night_restart_minute", 30)
    check_ram_interval = sup_cfg.get("check_ram_interval_sec", 1200)

    from shared import ipc
    removed = ipc.init(clean=True)
    log(f"IPC инициализирован (очищено файлов: {removed})")

    _workers = [Worker(w) for w in cfg.get("workers", [])]
    log(f"👁 supervisor запущен, воркеров: {len(_workers)}")

    for w in _workers:
        w.start()

    def shutdown(signum=None, frame=None):
        global _stopping
        if _stopping:
            return
        _stopping = True
        log("👁 supervisor: остановка")
        for w in _workers:
            w.stop()
        lock.release()
        log("🔓 lock снят")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    last_night_date = None
    last_ram_check = 0.0

    try:
        while not _stopping:
            now = time.time()
            dt = datetime.now()

            for w in _workers:
                if not w.check(crash_window, crash_max, restart_delay):
                    if w.critical:
                        log("Критичный воркер мёртв — остановка", level="ERROR")
                        _stopping = True
                        break

            if (dt.hour == night_hour and dt.minute == night_minute
                    and last_night_date != dt.date()):
                log(f"🌙 ночной перезапуск всех воркеров ({night_hour:02d}:{night_minute:02d})")
                for w in _workers:
                    w.restart("night restart")
                last_night_date = dt.date()

            if now - last_ram_check >= check_ram_interval:
                last_ram_check = now
                for w in _workers:
                    ram = w.ram_mb()
                    if ram > 0:
                        if ram > w.ram_limit_mb:
                            log(f"[{w.name}] RAM {ram:.0f} МБ > {w.ram_limit_mb} МБ — перезапуск",
                                level="WARN")
                            w.restart("ram limit")
                        else:
                            log(f"[{w.name}] RAM {ram:.0f} МБ", level="DEBUG")

            time.sleep(1.0)
    except KeyboardInterrupt:
        shutdown()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass