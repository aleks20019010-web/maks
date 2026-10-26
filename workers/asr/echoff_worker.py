"""
workers/asr/echoff_worker.py — AEC через echoff (WebRTC AEC3 + выравнивание).

echoff сам захватывает микрофон и loopback (WASAPI), выравнивает их по времени
и отдаёт on_frame с очищенным микрофоном (48 кГц mono float).

Мы ресемплим в 16 кГц (Vosk) и кладём в clean_q.

AecCapture — context manager. Запускается при __enter__, останавливается
при __exit__. Устройства (default loopback + default mic) выбирает сам echoff.

output_dir — уникальный на каждый запуск (timestamp), иначе echoff
отказывается писать в папку с прошлыми артефактами.

microphone_clean может приходить как tuple — приводим к np.ndarray.

FIX (день 28): output_dir = cfg.DATA_DIR / "aec_capture" / ts,
   а не Path(f"D:/Maks/data/aec_capture/{ts}"). Работает на любом компе.
"""

import queue
import threading
import time
from datetime import datetime
from pathlib import Path

import numpy as np

from shared import config as cfg
from shared.log import log


def _resample_float_to_int16(audio: np.ndarray, from_rate: int, to_rate: int) -> np.ndarray:
    """Ресемплинг float32 [-1, 1] → int16. Та же логика, что в main._resample."""
    if from_rate == to_rate:
        return (audio * 32767).astype(np.int16)

    ratio = from_rate / to_rate
    if ratio == int(ratio) and ratio >= 1:
        r = int(ratio)
        new_len = len(audio) // r
        if new_len <= 0:
            return np.zeros(0, dtype=np.int16)
        trimmed = audio[:new_len * r]
        reshaped = trimmed.reshape(new_len, r)
        return (reshaped.mean(axis=1) * 32767).astype(np.int16)

    new_len = int(len(audio) * to_rate / from_rate)
    if new_len <= 0:
        return np.zeros(0, dtype=np.int16)
    old_idx = np.arange(len(audio))
    new_idx = np.linspace(0, len(audio) - 1, new_len)
    return (np.interp(new_idx, old_idx, audio) * 32767).astype(np.int16)


class EchoffWorker:
    """Захват через echoff + ресемплинг в 16 кГц → clean_q."""

    def __init__(self, clean_q: queue.Queue):
        self.clean_q = clean_q
        self._stop = threading.Event()
        self._thread = None
        self._capture = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._capture is not None:
            try:
                self._capture.__exit__(None, None, None)
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=2.0)

    def _run(self):
        try:
            from echoff import AecCapture, AecConfig
        except Exception as e:
            log(f"echoff_worker: echoff не импортировался: {e}", tag="ASR", level="ERROR")
            return

        # AecConfig принимает только stream_delay_ms (по документации PyPI).
        cfg_obj = AecConfig(stream_delay_ms=cfg.ECHOFF_STREAM_DELAY_MS)

        def on_frame(frame):
            if self._stop.is_set():
                return
            try:
                clean = frame.microphone_clean
                if clean is None or len(clean) == 0:
                    return
                # echoff может отдавать tuple — приводим к ndarray
                clean = np.asarray(clean, dtype=np.float32)
                audio_16k = _resample_float_to_int16(
                    clean, cfg.ECHOFF_SAMPLE_RATE_HZ, cfg.VOSK_RATE
                )
                if len(audio_16k) > 0:
                    try:
                        self.clean_q.put_nowait(audio_16k)
                    except queue.Full:
                        pass
            except Exception as e:
                log(f"echoff_worker: on_frame error: {e}", tag="ASR", level="WARN")

        # Уникальная папка на каждый запуск — иначе echoff откажется перезаписывать
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        # FIX: от cfg.DATA_DIR, не хардкод D:/Maks
        out_dir = cfg.DATA_DIR / "aec_capture" / ts
        out_dir.mkdir(parents=True, exist_ok=True)

        try:
            self._capture = AecCapture(
                cfg_obj,
                on_frame=on_frame,
                output_dir=out_dir,
            )
            self._capture.__enter__()
            log(f"echoff_worker: AEC запущен (echoff + WebRTC AEC3), artifacts: {out_dir}", tag="ASR")
        except Exception as e:
            log(f"echoff_worker: не запустился: {e}", tag="ASR", level="ERROR")
            return

        while not self._stop.is_set():
            time.sleep(0.5)

        log("echoff_worker: остановлен", tag="ASR")