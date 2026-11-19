# workers/asr/recognizer.py
# Загрузка и перезагрузка GigaAM-v3, распознавание аудио и фоновый gigaam_worker.
#
# ── HOT_TAIL_SEC + раздельная ветка hot/wake (2026-11-19) ───────
#   HOT_TAIL_SEC: 1.3 → 1.15 (короткое окно для одного hot-слова
#   «дальше»/«тише»/«стоп»).
#   В run_worker ветка "hot" больше не ждёт VAD-сегмент (Vosk уже
#   дождался конца слова): берём готовый сегмент, а если его нет —
#   fallback на ring.get(pos - HOT_TAIL_SEC, ring.total) без
#   блокирующего wait_for. Ветка wake осталась с margin_before/
#   margin_after + wait_for(VOSK_SEG_TIMEOUT_SEC).

import threading
import time
import numpy as np
import onnx_asr

from shared import ipc
from shared import protocol
from shared import config as cfg
from shared.beep import play_beep
from shared.log import log
from shared.sources import CommandSource
from workers.asr.audio import Ring, SegmentStore
from workers.asr.ducking import DuckingController
from workers.asr.text_utils import CommandFilter, after_wake, hot_word_appropriate


GIGAAM_ENABLED = True
WAKE_REARM_SEC = 1.2
HOT_REARM_SEC = 0.3
VOSK_SEG_TIMEOUT_SEC = 0.85
VOSK_TAIL_SEC = 1.4
HOT_TAIL_SEC = 1.15   # Короткое окно для одного hot-слова («дальше», «тише», «стоп»)

_GIGAAM_ERROR_LIMIT = 5
_GIGAAM_RELOAD_COOLDOWN_SEC = 60.0


def extract_text(out) -> str:
    if out is None:
        return ""
    if isinstance(out, str):
        return out.strip()
    if isinstance(out, (list, tuple)):
        parts = []
        for x in out:
            if isinstance(x, str):
                s = x.strip()
                if s:
                    parts.append(s)
            elif isinstance(x, (list, tuple)):
                for item in x:
                    if isinstance(item, str) and item.strip():
                        parts.append(item.strip())
                        break
            elif isinstance(x, dict):
                s = extract_text(x)
                if s:
                    parts.append(s)
        return " ".join(parts).strip()
    if isinstance(out, dict):
        for key in ("text", "transcript", "result"):
            if key in out:
                return extract_text(out[key])
        return ""
    t = getattr(out, "text", None)
    return t.strip() if isinstance(t, str) else str(out).strip()


def load_gigaam():
    if not GIGAAM_ENABLED:
        return None
    name = getattr(cfg, "GIGAAM_MODEL", "gigaam-v3-rnnt")
    attempts = []
    try:
        import onnxruntime as ort
        so = ort.SessionOptions()
        so.intra_op_num_threads = int(getattr(cfg, "GIGAAM_THREADS", 2))
        so.inter_op_num_threads = 1
        attempts.append({"quantization": "int8", "sess_options": so})
        attempts.append({"sess_options": so})
    except Exception as e:
        log(f"asr: onnxruntime SessionOptions недоступны: {e}", tag="ASR", level="WARN")
    attempts.append({})

    for kw in attempts:
        label = ", ".join(kw) or "по умолчанию"
        try:
            log(f"asr: загружаю GigaAM «{name}» ({label})...", tag="ASR")
            m = onnx_asr.load_model(name, **kw)
            try:
                m.recognize(np.zeros(8000, dtype=np.float32), sample_rate=cfg.VOSK_RATE)
            except Exception:
                pass
            log(f"asr: GigaAM готов ({label})", tag="ASR")
            return m
        except Exception as e:
            log(f"asr: GigaAM ({label}) не загрузился: {e}", tag="ASR", level="WARN")
    log("asr: GigaAM не загрузился ни в одном режиме", tag="ASR", level="ERROR")
    return None


class GigaAMController:
    def __init__(
        self,
        ring: Ring,
        seg_store: SegmentStore,
        ducking: DuckingController,
        cmd_filter: CommandFilter,
        stat_inc,
        is_waiting_fn,
        start_wait_fn,
    ):
        self.ring = ring
        self.seg_store = seg_store
        self.ducking = ducking
        self.cmd_filter = cmd_filter
        self.stat_inc = stat_inc
        self.is_waiting = is_waiting_fn
        self.start_wait = start_wait_fn
        self.sr = cfg.VOSK_RATE

        self._model = load_gigaam()
        self._model_lock = threading.Lock()
        self._error_streak = 0
        self._last_reload_at = 0.0

        self._busy = False
        self._rearm_until = 0.0
        self._wake_req = (0, "vosk", 0.0, "wake", "")
        self._triggered = threading.Event()
        self._trig_lock = threading.Lock()

    def have_model(self) -> bool:
        with self._model_lock:
            return self._model is not None

    def recognize(self, audio: np.ndarray) -> str:
        if len(audio) < self.sr * 0.3:
            return ""
        self.stat_inc("gigaam_requests")
        with self._model_lock:
            m = self._model
            if m is None:
                self._error_streak += 1
                self.stat_inc("gigaam_error")
                return ""
            try:
                out = m.recognize(audio, sample_rate=self.sr)
                text = extract_text(out)
                exc = False
            except Exception as e:
                log(f"asr: [GIGAAM] ошибка: {e}", tag="ASR", level="WARN")
                text = ""
                exc = True

            if exc:
                self._error_streak += 1
                self.stat_inc("gigaam_error")
            elif not text:
                self._error_streak = 0
                self.stat_inc("gigaam_empty")
            else:
                self._error_streak = 0
                self.stat_inc("gigaam_ok")
            return text

    def maybe_reload(self):
        with self._model_lock:
            if self._error_streak < _GIGAAM_ERROR_LIMIT:
                return
            now = time.time()
            if now - self._last_reload_at < _GIGAAM_RELOAD_COOLDOWN_SEC:
                return
            log("asr: GigaAM серия ошибок, перезагружаю модель...", tag="ASR", level="WARN")
            self._error_streak = 0
            self._last_reload_at = now
            self._model = load_gigaam()
            self.stat_inc("gigaam_reloads")

    def trigger_wake(self, source: str, pos=None) -> bool:
        with self._trig_lock:
            if self._busy or self.is_waiting() or time.time() < self._rearm_until:
                return False
            self._busy = True
            self._wake_req = (self.ring.total if pos is None else pos, source, time.time(), "wake", "")
        self.ducking.set_wake_active(True)
        self._triggered.set()
        self.stat_inc(f"wake_{source}")
        return True

    def trigger_hot(self, vosk_text: str, pos: int) -> bool:
        with self._trig_lock:
            if self._busy or self.is_waiting():
                return False
            self._busy = True
            self._wake_req = (pos, "vosk_hot", time.time(), "hot", vosk_text)
        self._triggered.set()
        return True

    def wake_event(self) -> threading.Event:
        return self._triggered

    def run_worker(self, shutdown_event: threading.Event):
        while not shutdown_event.is_set():
            if not self._triggered.wait(timeout=0.1):
                continue
            self._triggered.clear()
            if shutdown_event.is_set():
                break

            mode = "wake"
            try:
                with self._trig_lock:
                    pos, source, t_trig, mode, hint_text = self._wake_req

                if mode == "hot":
                    # Для hot-слова («дальше», «тише») Vosk уже дождался конца слова.
                    # Проверяем только уже готовый сегмент без блокирующего ожидания.
                    sg = self.seg_store.find(
                        lambda x: x[1] >= pos - int(1.2 * self.sr) and x[0] <= pos
                    )
                    if sg:
                        audio = self.seg_store.extract_audio(sg)
                        self.seg_store.consume(sg[1])
                        vad_ok = True
                    else:
                        end_pos = self.ring.total
                        audio = self.ring.get(max(0, pos - int(HOT_TAIL_SEC * self.sr)), end_pos)
                        self.seg_store.consume(end_pos)
                        vad_ok = False
                else:
                    margin_before = int(1.4 * self.sr)
                    margin_after = int(0.6 * self.sr)
                    sg = self.seg_store.wait_for(
                        lambda x: x[1] >= pos - margin_before and x[0] <= pos + margin_after,
                        VOSK_SEG_TIMEOUT_SEC,
                        shutdown_event,
                    )

                    if sg:
                        seg_start = min(sg[0], max(0, pos - int(1.2 * self.sr)))
                        audio = self.seg_store.extract_audio((seg_start, sg[1]))
                        self.seg_store.consume(sg[1])
                        vad_ok = True
                    else:
                        end_pos = self.ring.total
                        audio = self.ring.get(max(0, pos - int(VOSK_TAIL_SEC * self.sr)), end_pos)
                        self.seg_store.consume(end_pos)
                        vad_ok = False
                        log(f"asr: [GIGAAM/{source}] VAD сегмент не найден — fallback "
                            f"(до ring.total, {VOSK_TAIL_SEC:.1f}с до data_end_pos)",
                            tag="ASR", level="DEBUG")

                t_rec = time.time()
                text = self.recognize(audio)
                self.maybe_reload()

                # Ветка 1: верификация hot word
                if mode == "hot":
                    dt_rec = time.time() - t_rec
                    log(f"asr: [GIGAAM/HOT] vosk='{hint_text}' → gigaam='{text}' "
                        f"(vad_ok={vad_ok}, {dt_rec:.2f}s)", tag="ASR")
                    if not text or self.cmd_filter.is_echo(text):
                        self.stat_inc("hot_rejected")
                        continue

                    matched = self.cmd_filter.match_hot_word_context(text, hint_text)
                    if matched and hot_word_appropriate(matched):
                        if self.cmd_filter.is_dup_command(matched):
                            self.stat_inc("dup_commands")
                            log(f"asr: [HOT] дубль команды «{matched}» — игнор", tag="ASR")
                            continue
                        log(f"asr: [HOT-OK] «{matched}»", tag="ASR")
                        self.stat_inc("hot_words")
                        ipc.put(protocol.BRAIN_INBOX,
                                protocol.command(matched, source=CommandSource.VOICE_HOT))
                    else:
                        self.stat_inc("hot_rejected")
                        log(f"asr: [HOT-REJECT] отсеяно: vosk='{hint_text}' != gigaam='{text}'",
                            tag="ASR")
                    continue

                # Ветка 2: обычное обращение («Макс...»)
                log(f"asr: [GIGAAM/{source}] «{text}»", tag="ASR")
                log(f"asr: wake timing: vad_ok={vad_ok} "
                    f"audio={len(audio) / self.sr:.2f}s recog={time.time() - t_rec:.2f}s "
                    f"since_trigger={time.time() - t_trig:.2f}s", tag="ASR")

                if not text:
                    log("asr: речи нет — ложный wake, игнор", tag="ASR")
                    continue

                command = after_wake(text)
                if command is None:
                    log("asr: «макс» в тексте нет — ложный wake, игнор", tag="ASR")
                    continue

                if command:
                    if self.cmd_filter.is_dup_command(command):
                        self.stat_inc("dup_commands")
                        log(f"asr: дубль команды «{command}» — игнор", tag="ASR")
                        continue
                    log(f"asr: [МАКС] + команда «{command}»", tag="ASR")
                    ipc.put(protocol.BRAIN_INBOX,
                            protocol.command(command, source=CommandSource.VOICE))
                else:
                    play_beep()
                    log("asr: [МАКС] (бип, ждём команду)", tag="ASR")
                    ipc.put(protocol.BRAIN_INBOX, protocol.event("wake", source="asr"))
                    # Отступаем назад на 0.4с, чтобы не отрезать начало фразы,
                    # если пользователь начал говорить сразу после «Макс»
                    self.start_wait(max(0, self.ring.total - int(0.4 * self.sr)))
            except Exception as e:
                log(f"asr: gigaam_worker ({mode}) fatal: {e}", tag="ASR", level="ERROR")
            finally:
                with self._trig_lock:
                    self._rearm_until = time.time() + (HOT_REARM_SEC if mode == "hot" else WAKE_REARM_SEC)
                    self._busy = False