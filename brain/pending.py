"""
brain/pending.py — менеджер ожиданий.

Ожидание — это вопрос, на который ждём ответ. Таймер — часть ожидания.
Если ответа нет — таймаут → callback.

wait_tts=True — таймер стартует ТОЛЬКО когда TTS замолчал.
                Это нужно для вопросов: TTS говорит 2-3 сек, микрофон
                выключен ещё 2 сек. Таймер должен идти ПОСЛЕ этого.
"""

import threading
import time
from typing import Callable, Optional


def _tts_speaking() -> bool:
    try:
        from workers.tts.state_file import get_state
        return bool(get_state().get("speaking", False))
    except Exception:
        return False


class Pending:
    def __init__(self, key: str, timeout: float,
                 on_timeout: Optional[Callable] = None,
                 data: Optional[dict] = None,
                 wait_tts: bool = False):
        self.key = key
        self.timeout = timeout
        self.on_timeout = on_timeout
        self.data = data or {}
        self.wait_tts = wait_tts
        self.timer: Optional[threading.Timer] = None
        self._cancelled = False
        self._waiter: Optional[threading.Thread] = None

    def start(self) -> None:
        if self.wait_tts:
            self._waiter = threading.Thread(target=self._wait_tts_then_start, daemon=True)
            self._waiter.start()
        else:
            self._start_timer()

    def _wait_tts_then_start(self) -> None:
        """Ждать, пока TTS замолчит, потом стартовать таймер."""
        # Ждём, пока TTS начнёт говорить (максимум 2 сек)
        deadline = time.time() + 2.0
        while time.time() < deadline:
            if self._cancelled:
                return
            if _tts_speaking():
                break
            time.sleep(0.1)

        # Ждём, пока TTS закончит (максимум 30 сек)
        deadline = time.time() + 30.0
        while time.time() < deadline:
            if self._cancelled:
                return
            if not _tts_speaking():
                break
            time.sleep(0.1)

        # TTS замолчал — стартуем таймер
        if self._cancelled:
            return
        self._start_timer()

    def _start_timer(self) -> None:
        self.timer = threading.Timer(self.timeout, self._fire)
        self.timer.daemon = True
        self.timer.start()

    def _fire(self) -> None:
        if self._cancelled:
            return
        if self.on_timeout:
            try:
                self.on_timeout(self.data)
            except Exception as e:
                from shared.log import log
                log(f"pending[{self.key}] on_timeout: {e}", tag="BRAIN", level="ERROR")

    def cancel(self) -> None:
        self._cancelled = True
        if self.timer:
            self.timer.cancel()


class PendingManager:
    def __init__(self):
        self._pending: dict[str, Pending] = {}
        self._lock = threading.Lock()

    def set(self, key: str, timeout: float,
            on_timeout: Optional[Callable] = None,
            data: Optional[dict] = None,
            wait_tts: bool = False) -> None:
        with self._lock:
            old = self._pending.get(key)
            if old:
                old.cancel()
            p = Pending(key, timeout, on_timeout, data, wait_tts)
            p.start()
            self._pending[key] = p

    def get(self, key: str) -> Optional[Pending]:
        with self._lock:
            return self._pending.get(key)

    def pop(self, key: str) -> Optional[Pending]:
        with self._lock:
            p = self._pending.pop(key, None)
            if p:
                p.cancel()
            return p

    def cancel(self, key: str) -> bool:
        p = self.pop(key)
        return p is not None

    def cancel_all(self) -> None:
        with self._lock:
            for p in self._pending.values():
                p.cancel()
            self._pending.clear()


_manager = PendingManager()


def set(key: str, timeout: float, on_timeout: Optional[Callable] = None,
        data: Optional[dict] = None, wait_tts: bool = False) -> None:
    _manager.set(key, timeout, on_timeout, data, wait_tts)


def get(key: str) -> Optional[Pending]:
    return _manager.get(key)


def pop(key: str) -> Optional[Pending]:
    return _manager.pop(key)


def cancel(key: str) -> bool:
    return _manager.cancel(key)


def cancel_all() -> None:
    _manager.cancel_all()