# workers/asr/audio.py
# Аудио-буферизация, ресемплинг, VAD-сегментатор и прогрев аудиоустройства.
#
# ── VAD TUNING (2026-11-19) ─────────────────────────────────────
#   Порог срабатывания ниже, закрытие фразы быстрее:
#     VAD_END_SILENCE_SEC: 0.5  → 0.38  (быстрее финализируем сегмент)
#     VAD_RATIO:           3.0  → 2.0   (после AEC 3.0 — слишком высоко
#                                        для спокойной речи)
#     VAD_MIN_RMS:         0.004 → 0.0025
#     VAD_MIN_SPEECH_SEC:  0.15 → 0.12  (короткие «да/нет» не теряем)
#   Плюс асимметричное обновление noise: вверх — медленно (alpha=0.005),
#   вниз — быстрее (alpha=0.03). Раньше 0.98/0.02 симметрично, и всплеск
#   голоса в начале фразы задирал noise → порог рос → VAD «глох» на
#   остаток фразы.
#   SegmentStore: maxlen 12 → 24 (глубина очереди сегментов).

import collections
import threading
import time
import wave
import numpy as np

from shared import config as cfg
from shared.log import log


VAD_END_SILENCE_SEC = getattr(cfg, "VAD_END_SILENCE_SEC", 0.38)  # было 0.5 — быстрее закрываем сегмент после команды
VAD_RATIO = getattr(cfg, "VAD_RATIO", 2.0)                       # было 3.0 — после AEC 3.0 слишком высоко для спокойной речи
VAD_MIN_RMS = getattr(cfg, "VAD_MIN_RMS", 0.0025)                # было 0.004
VAD_MAX_SEG_SEC = getattr(cfg, "VAD_MAX_SEG_SEC", 8.0)
VAD_MIN_SPEECH_SEC = 0.12
VAD_DEBUG = getattr(cfg, "VAD_DEBUG", False)

SEG_PAD_BEFORE_SEC = 0.25
SEG_PAD_AFTER_SEC = 0.15
SILENCE_WAV_PATH = getattr(cfg, "SILENCE_WAV_PATH", r"D:\Maks\silence.wav")


def resample(audio: np.ndarray, from_rate: int, to_rate: int) -> np.ndarray:
    if from_rate == to_rate:
        return audio
    ratio = from_rate / to_rate
    if ratio == int(ratio) and ratio >= 1:
        r = int(ratio)
        new_len = len(audio) // r
        if new_len <= 0:
            return np.zeros(0, dtype=audio.dtype)
        return audio[:new_len * r].reshape(new_len, r).mean(axis=1).astype(audio.dtype)
    new_len = int(len(audio) * to_rate / from_rate)
    if new_len <= 0:
        return np.zeros(0, dtype=audio.dtype)
    return np.interp(
        np.linspace(0, len(audio) - 1, new_len),
        np.arange(len(audio)),
        audio,
    ).astype(audio.dtype)


class Ring:
    """Потокобезопасный кольцевой аудио-буфер."""

    def __init__(self, seconds: float, rate: int):
        self.rate = rate
        self.buf = np.zeros(int(seconds * rate), dtype=np.float32)
        self._total = 0
        self._lock = threading.Lock()

    @property
    def total(self) -> int:
        with self._lock:
            return self._total

    def push(self, x: np.ndarray):
        cap = len(self.buf)
        with self._lock:
            added = len(x)
            if added >= cap:
                self._total += added - cap
                x = x[-cap:]
            n = len(x)
            i = self._total % cap
            k = min(n, cap - i)
            self.buf[i:i + k] = x[:k]
            if n > k:
                self.buf[:n - k] = x[k:]
            self._total += n

    def get(self, start: int, end=None) -> np.ndarray:
        with self._lock:
            end = self._total if end is None else min(end, self._total)
            start = max(start, self._total - len(self.buf), 0)
            if end <= start:
                return np.zeros(0, dtype=np.float32)
            cap = len(self.buf)
            i, j = start % cap, end % cap
            if i < j:
                return self.buf[i:j].copy()
            return np.concatenate([self.buf[i:], self.buf[:j]])


class Segmenter:
    """Энергетический VAD с адаптивным порогом фонового шума."""

    WARMUP_WINDOWS = 25

    def __init__(
        self,
        rate: int,
        end_silence: float = VAD_END_SILENCE_SEC,
        ratio: float = VAD_RATIO,
        min_rms: float = VAD_MIN_RMS,
        max_len: float = VAD_MAX_SEG_SEC,
        min_speech: float = VAD_MIN_SPEECH_SEC,
    ):
        self.win = int(0.02 * rate)
        self.end_n = max(1, int(end_silence / 0.02))
        self.min_n = max(1, int(min_speech / 0.02))
        self.max_n = int(max_len / 0.02)
        self.ratio = ratio
        self.min_rms = min_rms
        self.noise = None
        self._warm = []
        self.pos = 0
        self.tail = np.zeros(0, dtype=np.float32)
        self.start = None
        self.speech_n = 0
        self.sil_n = 0
        self.len_n = 0

    def threshold(self) -> float:
        return max((self.noise or 0.0) * self.ratio, self.min_rms)

    def reset(self):
        self.start = None
        self.speech_n = self.sil_n = self.len_n = 0
        self.tail = np.zeros(0, dtype=np.float32)

    def feed(self, x: np.ndarray) -> list[tuple[int, int]]:
        out = []
        x = np.concatenate([self.tail, x]) if len(self.tail) else x
        n = len(x) // self.win
        self.tail = x[n * self.win:]
        for k in range(n):
            w = x[k * self.win:(k + 1) * self.win]
            rms = float(np.sqrt(np.mean(w * w)))
            w_start = self.pos
            self.pos += self.win

            if self.noise is None:
                self._warm.append(rms)
                if len(self._warm) >= self.WARMUP_WINDOWS:
                    self.noise = float(np.median(self._warm))
                continue

            loud = rms > self.threshold()
            if self.start is None:
                if loud:
                    self.start = w_start
                    self.speech_n = 1
                    self.sil_n = 0
                    self.len_n = 1
                else:
                    # Быстро опускаем шум в тишине, но очень медленно поднимаем
                    # на пред-речевых звуках: иначе всплеск голоса в начале
                    # фразы задирает noise → растёт порог → VAD глохнет.
                    alpha = 0.005 if rms > self.noise else 0.03
                    self.noise = (1.0 - alpha) * self.noise + alpha * rms
                continue

            self.len_n += 1
            if loud:
                self.speech_n += 1
                self.sil_n = 0
            else:
                self.sil_n += 1
            if self.sil_n >= self.end_n or self.len_n >= self.max_n:
                if self.speech_n >= self.min_n:
                    out.append((self.start, self.pos - self.sil_n * self.win))
                self.start = None
        return out


class SegmentStore:
    """Потокобезопасное хранилище VAD-сегментов поверх Ring."""

    def __init__(self, ring: Ring, rate: int):
        self.ring = ring
        self.sr = rate
        self.segmenter = Segmenter(rate)
        self._feed_lock = threading.Lock()
        self._segments = collections.deque(maxlen=24)
        self._seg_lock = threading.Lock()

    def feed_audio(self, f32: np.ndarray, on_segment=None):
        with self._feed_lock:
            new_segs = self.segmenter.feed(f32)
        for sg in new_segs:
            dropped = False
            with self._seg_lock:
                if len(self._segments) == self._segments.maxlen:
                    dropped = True
                self._segments.append(sg)
            if on_segment:
                on_segment(sg, dropped)
            if VAD_DEBUG:
                log(f"asr: [VAD] сегмент {(sg[1] - sg[0]) / self.sr:.2f}с", tag="ASR", level="DEBUG")

    def find(self, pred):
        with self._seg_lock:
            for sg in list(self._segments):
                if pred(sg):
                    return sg
        return None

    def consume(self, upto_end: int):
        with self._seg_lock:
            keep = [sg for sg in self._segments if sg[1] > upto_end]
            self._segments.clear()
            self._segments.extend(keep)

    def clear(self):
        with self._seg_lock:
            self._segments.clear()
        with self._feed_lock:
            self.segmenter.reset()

    def wait_for(self, pred, timeout: float, shutdown_event: threading.Event):
        t0 = time.time()
        while time.time() - t0 < timeout:
            if shutdown_event.is_set():
                return None
            sg = self.find(pred)
            if sg:
                return sg
            time.sleep(0.02)
        return None

    def extract_audio(self, sg: tuple[int, int]) -> np.ndarray:
        return self.ring.get(
            sg[0] - int(SEG_PAD_BEFORE_SEC * self.sr),
            sg[1] + int(SEG_PAD_AFTER_SEC * self.sr),
        )


def run_silence_worker(shutdown_event: threading.Event):
    """Удерживает аудио-поток прогретым для стабильной работы AEC."""
    try:
        import sounddevice as sd

        with wave.open(SILENCE_WAV_PATH, "rb") as wf:
            sr, ch = wf.getframerate(), wf.getnchannels()
            frames = wf.readframes(wf.getnframes())
        audio = np.frombuffer(frames, dtype=np.int16)
        if ch > 1:
            audio = audio.reshape(-1, ch).mean(axis=1).astype(np.int16)

        def callback(outdata, frame_count, time_info, status):
            nonlocal audio
            if len(audio) < frame_count:
                outdata[:len(audio), 0] = audio
                outdata[len(audio):, 0] = 0
                audio = np.zeros(0, dtype=np.int16)
            else:
                outdata[:, 0] = audio[:frame_count]
                audio = audio[frame_count:]

        with sd.OutputStream(samplerate=sr, channels=1, dtype="int16", callback=callback, blocksize=480):
            while not shutdown_event.is_set():
                time.sleep(0.5)
    except Exception as e:
        log(f"asr: silence worker error: {e}", tag="ASR", level="WARN")