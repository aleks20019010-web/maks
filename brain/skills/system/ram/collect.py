"""RAM: сбор данных. Только psutil, без речи."""

import psutil
from shared.log import log


def collect() -> dict:
    """
    Возвращает:
        {"ok": True, "percent": 69, "free_pct": 31,
         "used_gb": 4.1, "total_gb": 5.9, "free_gb": 1.8}
        {"ok": False, "reason": "..."}
    """
    try:
        vm = psutil.virtual_memory()
        if vm is None:
            return {"ok": False, "reason": "no_data"}

        total_gb = round(vm.total / (1024 ** 3), 1)
        used_gb = round(vm.used / (1024 ** 3), 1)
        free_gb = round(vm.available / (1024 ** 3), 1)

        return {
            "ok": True,
            "percent": int(vm.percent),          # занято, %
            "free_pct": int(100 - vm.percent),   # свободно, %
            "used_gb": used_gb,
            "total_gb": total_gb,
            "free_gb": free_gb,
        }
    except PermissionError as e:
        log(f"ram collect permission: {e}", tag="SYSTEM", level="WARN")
        return {"ok": False, "reason": "permission"}
    except Exception as e:
        log(f"ram collect error: {e}", tag="SYSTEM", level="ERROR")
        return {"ok": False, "reason": "error"}