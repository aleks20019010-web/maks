# workers/asr/ducking.py
# Управление приглушением музыки (DUCK_ON / DUCK_OFF) и мониторинг активности TTS.
#
# ── TTS-SPEAKING CACHE (2026-11-19) ────────────────────────────
#   is_speaking — кэш последнего значения tts_speaking().
#   run_worker вызывает tts_speaking() каждые 50 мс, так что кэш
#   всегда свежий. Диска касается только один поток (run_worker);
#   остальные читают self.is_speaking из памяти (без файла).

import threading
import time

from shared import ipc
from shared import protocol
from shared import runtime_state as rt
from shared.log import log


WAKE_MAX_SEC = 5.0
DUCK_OFF_WAIT_SEC = 0.5
_TTS_SPEAKING_TTL_SEC = 60.0
_TTS_STUCK_LIMIT = 30.0
_TTS_STUCK_WARN_COOLDOWN_SEC = 5.0


class DuckingController:
    def __init__(self):
        self._duck_sent = False
        self._duck_lock = threading.Lock()
        self._last_duck_on_at = 0.0
        self._last_duck_warn_at = 0.0

        self._wake_active = False
        self._wake_active_since = 0.0
        self._wake_lock = threading.Lock()

        self._pending_duck_off_since = 0.0
        self._pending_lock = threading.Lock()

        self._tts_speaking_since = 0.0
        self._tts_last_state = False

        # Кэш последнего значения tts_speaking() — читается без диска.
        self.is_speaking = False

    def tts_speaking(self) -> bool:
        try:
            from workers.tts.state_file import get_state
            speaking = bool(get_state().get("speaking", False))
        except Exception:
            speaking = False

        now = time.time()
        if speaking and not self._tts_last_state:
            self._tts_speaking_since = now
        self._tts_last_state = speaking

        if speaking and (now - self._tts_speaking_since) > _TTS_SPEAKING_TTL_SEC:
            speaking = False

        self.is_speaking = speaking
        return speaking

    def set_wake_active(self, val: bool):
        with self._wake_lock:
            if val and not self._wake_active:
                self._wake_active_since = time.time()
            self._wake_active = val

    def is_wake_active(self) -> bool:
        with self._wake_lock:
            return self._wake_active

    def _wake_elapsed(self) -> float:
        with self._wake_lock:
            return (time.time() - self._wake_active_since) if self._wake_active else 0.0

    def duck_on(self):
        with self._duck_lock:
            if self._duck_sent:
                return
            self._duck_sent = True
            self._last_duck_on_at = time.time()
        log("asr: DUCK_ON → music (приглушаю)", tag="ASR")
        try:
            ipc.put(protocol.MUSIC_INBOX, protocol.music_control("duck_on"))
        except Exception as e:
            with self._duck_lock:
                self._duck_sent = False
            log(f"asr: duck_on error: {e}", tag="ASR", level="WARN")

    def duck_off(self):
        with self._duck_lock:
            if not self._duck_sent:
                return
        log("asr: DUCK_OFF → music (восстанавливаю)", tag="ASR")
        try:
            ipc.put(protocol.MUSIC_INBOX, protocol.music_control("duck_off"))
        except Exception as e:
            log(f"asr: duck_off error: {e}", tag="ASR", level="WARN")
        finally:
            with self._duck_lock:
                self._duck_sent = False

    def run_worker(self, shutdown_event: threading.Event):
        while not shutdown_event.is_set():
            try:
                speaking = self.tts_speaking()
                wake = self.is_wake_active()
                sess = rt.exists(rt.FLAG_SESSION_OPEN)

                if wake and speaking:
                    log("asr: wake → speaking (mic_off подхватил)", tag="ASR")
                    self.set_wake_active(False)
                    wake = False
                elif wake and self._wake_elapsed() > WAKE_MAX_SEC:
                    log(f"asr: wake TTL {WAKE_MAX_SEC:.0f}с — снимаю (TTS не запустился)",
                        tag="ASR", level="WARN")
                    self.set_wake_active(False)
                    wake = False

                need_duck = speaking or wake or sess

                with self._duck_lock:
                    sent = self._duck_sent
                    stuck_dt = time.time() - self._last_duck_on_at

                if sent and not need_duck and stuck_dt > _TTS_STUCK_LIMIT:
                    now = time.time()
                    if now - self._last_duck_warn_at >= _TTS_STUCK_WARN_COOLDOWN_SEC:
                        self._last_duck_warn_at = now
                        log("asr: ducking held too long without reason (TTL warn)",
                            tag="ASR", level="WARN")

                if need_duck:
                    with self._pending_lock:
                        self._pending_duck_off_since = 0.0
                    self.duck_on()
                else:
                    now = time.time()
                    with self._pending_lock:
                        if self._pending_duck_off_since == 0.0:
                            self._pending_duck_off_since = now
                            elapsed = 0.0
                        else:
                            elapsed = now - self._pending_duck_off_since
                    if elapsed >= DUCK_OFF_WAIT_SEC:
                        with self._pending_lock:
                            self._pending_duck_off_since = 0.0
                        self.duck_off()

                time.sleep(0.05)
            except Exception as e:
                log(f"asr: duck_worker error: {e}", tag="ASR", level="WARN")
                time.sleep(0.5)