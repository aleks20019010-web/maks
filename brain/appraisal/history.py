# brain/appraisal/history.py
# Окно эпизода: сколько SKIP одного артиста/трека за окно времени.
#
# Правило окна: если между событиями больше EPISODE_WINDOW_SEC —
# счётчик сбрасывается, начинается новый эпизод.
#
# Только данные. Решение «реагировать или нет» — в policy.py.

import threading
import time

from brain.appraisal.events import Event, KIND_SKIP


DEFAULT_EPISODE_WINDOW_SEC = 180.0  # 3 минуты


class History:
    def __init__(self, window_sec: float = DEFAULT_EPISODE_WINDOW_SEC):
        self._window = window_sec
        self._lock = threading.Lock()
        # key -> {"count": int, "first_ts": float, "last_ts": float}
        self._episodes: dict[str, dict] = {}

    def record(self, event: Event, now: float | None = None) -> int:
        """Записать событие. Вернуть счётчик для ключа после записи."""
        if event.kind != KIND_SKIP:
            return 0

        now = time.time() if now is None else now
        key = event.key()

        with self._lock:
            ep = self._episodes.get(key)
            if ep is None or (now - ep["last_ts"]) > self._window:
                ep = {"count": 0, "first_ts": now, "last_ts": now}
                self._episodes[key] = ep

            ep["count"] += 1
            ep["last_ts"] = now
            return ep["count"]

    def get_count(self, key: str, now: float | None = None) -> int:
        """Счётчик без записи. Учитывает устаревание окна."""
        now = time.time() if now is None else now
        with self._lock:
            ep = self._episodes.get(key)
            if ep is None:
                return 0
            if (now - ep["last_ts"]) > self._window:
                return 0
            return ep["count"]

    def reset(self, key: str) -> None:
        """Сбросить эпизод для ключа."""
        with self._lock:
            self._episodes.pop(key, None)

    def reset_all(self) -> None:
        """Сбросить всё (для тестов)."""
        with self._lock:
            self._episodes.clear()