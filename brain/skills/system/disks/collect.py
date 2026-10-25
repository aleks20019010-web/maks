"""Диски: сбор данных. Только psutil, без речи. С таймаутом на каждый диск."""

import threading
import psutil
from shared.log import log


# Фиксированный список дисков. Добавить — правка здесь.
KNOWN_DISKS = ["C:", "D:", "E:", "F:", "G:"]

DISK_TIMEOUT_SEC = 2


def _disk_usage_with_timeout(letter: str) -> dict:
    """
    psutil.disk_usage в отдельном потоке с таймаутом.
    Возвращает {"ok": True, ...} или {"ok": False, "reason": "..."}.
    """
    result = {"ok": False, "reason": "timeout"}

    def _worker():
        try:
            path = f"{letter}\\"
            usage = psutil.disk_usage(path)
            total = usage.total
            free = usage.free
            if total <= 0:
                result.update({"ok": False, "reason": "no_data"})
                return
            free_gb = round(free / (1024 ** 3), 1)
            free_pct = int(round((free / total) * 100))
            result.update({
                "ok": True,
                "free_gb": free_gb,
                "free_pct": free_pct,
            })
        except FileNotFoundError:
            result.update({"ok": False, "reason": "not_found"})
        except PermissionError:
            result.update({"ok": False, "reason": "permission"})
        except Exception as e:
            result.update({"ok": False, "reason": "error", "detail": str(e)})

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join(timeout=DISK_TIMEOUT_SEC)

    if thread.is_alive():
        # Поток не завершился — диск не отвечает.
        result.update({"ok": False, "reason": "timeout"})

    return result


def collect() -> dict:
    """
    Возвращает:
        {
            "ok": True,
            "disks": {
                "C:": {"ok": True, "free_gb": 45.0, "free_pct": 18},
                "D:": {"ok": True, "free_gb": 8.0, "free_pct": 5},
                "E:": {"ok": False, "reason": "not_found"},
                "F:": {"ok": True, "free_gb": 200.0, "free_pct": 40},
            }
        }
    """
    try:
        disks = {}
        for letter in KNOWN_DISKS:
            disks[letter] = _disk_usage_with_timeout(letter)

        # Логируем проблемы, не падаем.
        for letter, info in disks.items():
            if not info.get("ok"):
                log("system", f"disk {letter}: {info.get('reason')}", level="WARN")

        return {"ok": True, "disks": disks}
    except Exception as e:
        log("system", f"disks collect error: {e}", level="ERROR")
        return {"ok": False, "reason": "error"}