"""
shared/metrics.py — счётчики для отладки.

Простые счётчики в памяти. Без базы, без Prometheus, без сложности.
inc("commands") — увеличить. dump() — раз в минуту в лог.

Зачем: видеть тренды. "TTS упал 10 раз за час" — сразу видно.
Без метрик — только grep по логу, и то если не пропустил.
"""

import threading

from shared.log import log


_counters: dict[str, int] = {}
_lock = threading.Lock()


def inc(name: str, value: int = 1) -> None:
    """Увеличить счётчик."""
    with _lock:
        _counters[name] = _counters.get(name, 0) + value


def get(name: str) -> int:
    """Получить значение счётчика."""
    with _lock:
        return _counters.get(name, 0)


def dump() -> dict[str, int]:
    """Сбросить снимок счётчиков в лог (DEBUG). Вернуть снимок."""
    with _lock:
        snapshot = dict(_counters)
    for name, value in sorted(snapshot.items()):
        log(f"METRIC {name}={value}", tag="METRICS", level="DEBUG")
    return snapshot


def reset() -> None:
    """Обнулить все счётчики."""
    with _lock:
        _counters.clear()