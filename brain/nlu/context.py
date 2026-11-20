# brain/nlu/context.py
# NLU-контекст: продолжение диалога, уточнения, TTL 60 секунд.

import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from brain.nlu.types import Entity, Intent


DEFAULT_TTL_SEC = 60.0

CANCEL_WORDS = frozenset({
    "отмена", "отмени", "забудь", "не надо", "неважно", "не важно",
    "не хочу", "стоп", "хватит", "нет",
})


@dataclass
class PendingCommand:
    intent: Intent
    missing: list[str] = field(default_factory=list)
    partial_entity: Optional[Entity] = None
    original_text: str = ""
    created_at: float = field(default_factory=time.time)
    ttl_sec: float = DEFAULT_TTL_SEC

    def is_expired(self, now: Optional[float] = None) -> bool:
        now = time.time() if now is None else now
        return (now - self.created_at) > self.ttl_sec

    def age_sec(self, now: Optional[float] = None) -> float:
        now = time.time() if now is None else now
        return max(0.0, now - self.created_at)


@dataclass
class NLUContext:
    pending: Optional[PendingCommand] = None
    history: list[tuple[Intent, str]] = field(default_factory=list)
    ttl_until: float = 0.0
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False, compare=False)

    def set_pending(self, pending: PendingCommand) -> None:
        with self._lock:
            self.pending = pending
            self.ttl_until = pending.created_at + pending.ttl_sec

    def get_pending(self) -> Optional[PendingCommand]:
        with self._lock:
            if self.pending is None:
                return None
            if self.pending.is_expired():
                return None
            return self.pending

    def clear_pending(self) -> None:
        with self._lock:
            self.pending = None
            self.ttl_until = 0.0

    def clear_expired(self) -> bool:
        with self._lock:
            if self.pending is not None and self.pending.is_expired():
                self.clear_pending()
                return True
            return False

    def add_history(self, intent: Intent, text: str, max_len: int = 20) -> None:
        with self._lock:
            self.history.append((intent, text))
            if len(self.history) > max_len:
                self.history = self.history[-max_len:]

    def is_cancel(self, text: str) -> bool:
        if not text:
            return False
        t = text.lower().strip()
        return t in CANCEL_WORDS


_context: Optional[NLUContext] = None
_singleton_lock = threading.Lock()


def get() -> NLUContext:
    global _context
    with _singleton_lock:
        if _context is None:
            _context = NLUContext()
        return _context


def reset() -> None:
    global _context
    with _singleton_lock:
        _context = NLUContext()