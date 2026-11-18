"""
brain/pending.py — менеджер ожиданий.

wait_tts=True — таймер ответа стартует ТОЛЬКО после того, как TTS
озвучил и завершил вопрос. При срабатывании таймаута ожидание
автоматически удаляется из менеджера.
"""

import threading
import time
from typing import Callable, Optional

from shared import runtime_state as rt
from shared.log import log


def _get_tts_state() -> dict:
    try:
        from workers.tts.state_file import get_state
        return get_state()
    except Exception:
        return {"speaking": False, "phrase_seq": 0}


def is_expecting() -> bool:
    """Есть ли живое ожидание (флаг не протух)."""
    return rt.exists(rt.FLAG_EXPECTING_INPUT)


class Pending:
    def __init__(self, key: str, timeout: float,
                 on_timeout: Optional[Callable] = None,
                 data: Optional[dict] = None,
                 wait_tts: bool = False,
                 base_phrase_seq: Optional[int] = None,
                 on_expire: Optional[Callable[["Pending"], None]] = None):
        self.key = key
        self.timeout = timeout
        self.on_timeout = on_timeout
        self.data = data or {}
        self.wait_tts = wait_tts
        self.base_phrase_seq = base_phrase_seq
        self._on_expire = on_expire

        self.timer: Optional[threading.Timer] = None
        self._cancelled = False
        self._lock = threading.Lock()
        self._waiter: Optional[threading.Thread] = None

    def start(self) -> None:
        if self.wait_tts:
            self._waiter = threading.Thread(
                target=self._wait_tts_then_start,
                daemon=True,
                name=f"pending-wait-tts-{self.key}",
            )
            self._waiter.start()
        else:
            self._start_timer()

    def _wait_tts_then_start(self) -> None:
        if self.base_phrase_seq is not None:
            # 1. Ждём старта конкретной фразы (phrase_seq > base_phrase_seq), до 5 сек
            start_deadline = time.time() + 5.0
            while time.time() < start_deadline:
                if self._cancelled:
                    return
                if _get_tts_state()["phrase_seq"] > self.base_phrase_seq:
                    break
                time.sleep(0.05)

            # 2. Ждём завершения речи TTS (если уже договорил — выйдет сразу)
            end_deadline = time.time() + 30.0
            while time.time() < end_deadline:
                if self._cancelled:
                    return
                if not _get_tts_state()["speaking"]:
                    break
                time.sleep(0.05)
        else:
            # Fallback без base_phrase_seq
            start_deadline = time.time() + 2.0
            while time.time() < start_deadline:
                if self._cancelled:
                    return
                if _get_tts_state()["speaking"]:
                    break
                time.sleep(0.1)

            end_deadline = time.time() + 30.0
            while time.time() < end_deadline:
                if self._cancelled:
                    return
                if not _get_tts_state()["speaking"]:
                    break
                time.sleep(0.1)

        self._start_timer()

    def _start_timer(self) -> None:
        with self._lock:
            if self._cancelled:
                return
            self.timer = threading.Timer(self.timeout, self._fire)
            self.timer.daemon = True
            self.timer.start()

    def _fire(self) -> None:
        with self._lock:
            if self._cancelled:
                return
            self._cancelled = True

        if self._on_expire:
            self._on_expire(self)

        if self.on_timeout:
            try:
                self.on_timeout(self.data)
            except Exception as e:
                log(f"pending[{self.key}] on_timeout: {e}",
                    tag="BRAIN", level="ERROR")

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
            if self.timer:
                self.timer.cancel()


class PendingManager:
    def __init__(self):
        self._pending: dict[str, Pending] = {}
        self._lock = threading.Lock()

    def _remove_expired(self, p: Pending) -> None:
        with self._lock:
            if self._pending.get(p.key) is p:
                self._pending.pop(p.key, None)
            if not self._pending:
                rt.clear(rt.FLAG_EXPECTING_INPUT)

    def set(self, key: str, timeout: float,
            on_timeout: Optional[Callable] = None,
            data: Optional[dict] = None,
            wait_tts: bool = False,
            base_phrase_seq: Optional[int] = None) -> None:
        with self._lock:
            old = self._pending.get(key)
            if old:
                old.cancel()
            p = Pending(
                key=key,
                timeout=timeout,
                on_timeout=on_timeout,
                data=data,
                wait_tts=wait_tts,
                base_phrase_seq=base_phrase_seq,
                on_expire=self._remove_expired,
            )
            self._pending[key] = p
            p.start()
            rt.set(rt.FLAG_EXPECTING_INPUT, True, ttl=rt.TTL_EXPECTING_INPUT)

    def get(self, key: str) -> Optional[Pending]:
        with self._lock:
            return self._pending.get(key)

    def pop(self, key: str) -> Optional[Pending]:
        with self._lock:
            p = self._pending.pop(key, None)
            if p:
                p.cancel()
            if not self._pending:
                rt.clear(rt.FLAG_EXPECTING_INPUT)
            return p

    def cancel(self, key: str) -> bool:
        return self.pop(key) is not None

    def cancel_all(self) -> None:
        with self._lock:
            for p in self._pending.values():
                p.cancel()
            self._pending.clear()
            rt.clear(rt.FLAG_EXPECTING_INPUT)


_manager = PendingManager()


def set(key: str, timeout: float, on_timeout: Optional[Callable] = None,
        data: Optional[dict] = None, wait_tts: bool = False,
        base_phrase_seq: Optional[int] = None) -> None:
    _manager.set(key, timeout, on_timeout, data, wait_tts, base_phrase_seq=base_phrase_seq)


def get(key: str) -> Optional[Pending]:
    return _manager.get(key)


def pop(key: str) -> Optional[Pending]:
    return _manager.pop(key)


def cancel(key: str) -> bool:
    return _manager.cancel(key)


def cancel_all() -> None:
    _manager.cancel_all()