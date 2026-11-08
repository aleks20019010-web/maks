"""
brain/pending.py — менеджер ожиданий.

Ожидание — это вопрос, на который ждём ответ. Таймер — часть ожидания.
Если ответа нет — таймаут → callback.

wait_tts=True — таймер стартует ТОЛЬКО когда TTS замолчал.

── FIX (этап 0.1): expecting_input через runtime_state ────────────
  Раньше: EXPECTING_FLAG = cfg.DATA_DIR / "expecting_input.flag",
  _write_expecting_flag() → write_text(str(time.time())), БЕЗ TTL.
  Если brain падал между set() и pop() — флаг залипал, ASR навсегда
  считал, что ждём ответ, и «да»/«нет» шли в brain как VOICE_HOT без
  pending → router молча игнорил. Пользователь говорит «да» — тишина.

  Теперь: rt.set(FLAG_EXPECTING_INPUT, True, ttl=60). TTL — 60 сек
  (TTL_EXPECTING_INPUT). Если brain упал — флаг протухает через 60 сек,
  ASR перестаёт считать, что ждём ответ. brain при рестарте тоже
  сбрасывает (rt.clear в main, если надо — но сейчас не надо: TTL).

  ASR читает через rt.exists(FLAG_EXPECTING_INPUT) — не через pending.is_expecting()
  (кросс-процессный импорт brain в ASR не хочу).

  is_expecting() оставлен для brain-внутреннего использования (если
  router захочет проверить «есть ли pending» — но router и так знает
  через pending.get(key)).

── FIX (этап 0.1): формат ────────────────────────────────────────
  data/runtime/expecting_input.flag.json:
    {"value": true, "ts": <float>, "ttl": 60.0}
  Вместо data/expecting_input.flag (write_text(str(time.time()))).
"""

import threading
import time
from typing import Callable, Optional

from shared import runtime_state as rt
from shared.log import log


def _tts_speaking() -> bool:
    try:
        from workers.tts.state_file import get_state
        return bool(get_state().get("speaking", False))
    except Exception:
        return False


def is_expecting() -> bool:
    """Есть ли живое ожидание (флаг не протух).

    Для brain-внутреннего использования. ASR читает напрямую через
    rt.exists(FLAG_EXPECTING_INPUT) — без импорта brain в ASR.
    """
    return rt.exists(rt.FLAG_EXPECTING_INPUT)


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
        deadline = time.time() + 2.0
        while time.time() < deadline:
            if self._cancelled:
                return
            if _tts_speaking():
                break
            time.sleep(0.1)

        deadline = time.time() + 30.0
        while time.time() < deadline:
            if self._cancelled:
                return
            if not _tts_speaking():
                break
            time.sleep(0.1)

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
                log(f"pending[{self.key}] on_timeout: {e}",
                    tag="BRAIN", level="ERROR")

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
            # FIX (этап 0.1): TTL 60 сек. brain упал — флаг протухнет,
            # ASR перестанет ждать ответ.
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
        p = self.pop(key)
        return p is not None

    def cancel_all(self) -> None:
        with self._lock:
            for p in self._pending.values():
                p.cancel()
            self._pending.clear()
            rt.clear(rt.FLAG_EXPECTING_INPUT)


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