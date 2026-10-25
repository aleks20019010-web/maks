"""CPU: сбор данных. Только psutil, без речи."""

import psutil
from shared.log import log


def collect() -> dict:
    """
    Возвращает:
        {"ok": True, "percent": 18, "cores": 8}
        {"ok": False, "reason": "..."}
    """
    try:
        # interval=3 — 3 секунды. Реальный процент, не "0%" на простое.
        # Блокирует 3 сек, но SystemSkill уже сказал "Собираю данные" до вызова.
        percent = psutil.cpu_percent(interval=3)
        cores = psutil.cpu_count(logical=True) or 0

        return {
            "ok": True,
            "percent": int(round(percent)),
            "cores": cores,
        }
    except Exception as e:
        log(f"cpu collect error: {e}", tag="SYSTEM", level="ERROR")
        return {"ok": False, "reason": "error"}