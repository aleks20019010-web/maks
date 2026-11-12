"""
workers/asr/main.py — ASR worker.

Роли:
  Vosk (грамматика)  — hot words, да/нет и детект «макс» по концу фразы.
  ViolaWake          — детектор «Макс» (быстрый, по слову).
  GigaAM (onnx-asr)  — стенографист: распознаёт кусок из кольцевого буфера.

Как работает «Макс включи музыку» одной фразой:
  * Сегментатор (энергетический VAD) непрерывно режет поток из буфера на фразы.
  * ViolaWake / Vosk только говорят «в этом месте было "макс"».
    Берём фразу, которая заканчивается после этой точки, как только она
    закончилась (пауза VAD_END_SILENCE_SEC) -> GigaAM. Без фиксированных секунд.
  * Если VAD сегмент не отдал — запасной вариант: фиксированное окно.
  * Кто первый — того и обрабатываем, второй игнорируется (busy + rearm).
  * В тексте GigaAM «макс» обязателен: нет слова -> ложный wake, молчим.
  * После бипа команда ловится тем же VAD: следующий сегмент речи.
"""

import collections
import difflib
import io
import json
import os
import queue
import re
import sys
import threading
import time

# Фикс кракозябр в консоли Windows
if os.name == 'nt':
    os.system('chcp 65001 > nul')
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import numpy as np
import onnx_asr
from vosk import Model, KaldiRecognizer

# ── Импорты с проверкой ──────────────────────────────────────
try:
    from violawake_sdk import WakeDetector
    VIOLA_AVAILABLE = True
except ImportError:
    VIOLA_AVAILABLE = False
    WakeDetector = None

try:
    from echoff import AecCapture, AecConfig, AecFrame
    ECHOFF_AVAILABLE = True
except ImportError:
    ECHOFF_AVAILABLE = False
    AecCapture = AecConfig = AecFrame = None

from shared import ipc
from shared import protocol
from shared import config as cfg
from shared import runtime_state as rt
from shared.log import log
from shared.sources import CommandSource
from shared.beep import play_beep

# ── Константы ─────────────────────────────────────────────────
POST_TTS_MUTE_SEC = 1.0
POST_TTS_MUTE_WAKE_SEC = 0.7
HOT_WORD_COOLDOWN_SEC = 1.0
ECHO_SIMILARITY_THRESHOLD = 0.85
HOT_WORDS_RELOAD_SEC = 30.0

GIGAAM_ENABLED = True
GIGAAM_MAX_BUFFER_SEC = 10.0   # размер кольцевого буфера
WAKE_PRE_SEC = 1.0             # запасной вариант (VAD не отдал сегмент): сколько брать ДО wake
VOSK_TAIL_SEC = 4.0            # запасной вариант для Vosk: сколько брать назад
WAKE_REARM_SEC = 1.2           # пауза после обработки: игнорируем «хвостовые» триггеры той же фразы

# VAD (настройки в shared/config.py, если нужно подкрутить)
VAD_END_SILENCE_SEC = getattr(cfg, "VAD_END_SILENCE_SEC", 0.5)   # пауза = конец фразы
VAD_RATIO = getattr(cfg, "VAD_RATIO", 3.0)                       # порог = фон * ratio
VAD_MIN_RMS = getattr(cfg, "VAD_MIN_RMS", 0.004)                 # абсолютный минимум порога
VAD_MAX_SEG_SEC = getattr(cfg, "VAD_MAX_SEG_SEC", 8.0)
VAD_DEBUG = getattr(cfg, "VAD_DEBUG", True)                      # лог сегментов для настройки
WAKE_SEG_MARGIN_SEC = 0.6      # Viola: фраза должна начаться до wake и закончиться не раньше wake - margin
WAKE_SEG_TIMEOUT_SEC = 6.0     # Viola: сколько ждать конца фразы
VOSK_SEG_TIMEOUT_SEC = 1.5     # Vosk: сколько ждать сегмент (фраза уже закончилась)
SEG_PAD_BEFORE_SEC = 0.25      # запас вокруг сегмента для GigaAM
SEG_PAD_AFTER_SEC = 0.15
WAKE_FUZZY = 0.8               # порог похожести токена на «макс» в тексте GigaAM

_COMMANDS_FILE = "commands.json"
_YES_NO_WORDS = {"да", "нет", "ок", "окей", "подтверждаю", "не надо", "неважно",
                 "не важно", "не хочу", "отмена"}
_CONTROL_WORDS = {"дальше", "назад", "пауза", "продолжи", "стоп", "громче", "тише",
                  "хватит", "достаточно", "замолчи"}

# ── Глобальное состояние ──────────────────────────────────────
_waiting_for_command = False
_wait_pos = 0                  # позиция буфера на момент бипа (команда = сегмент после неё)
_wait_gen = 0                  # «поколение» ожидания: защита от гонки таймеров
_last_hot_word_at = 0.0
_duck_sent = False
_tts_stopped_at = 0.0          # когда TTS последний раз замолчал (для ViolaWake)

_gigaam_busy = False
_rearm_until = 0.0
_wake_req = (0, "viola")       # (абсолютная позиция в буфере, источник)
_wake_triggered = threading.Event()
_trig_lock = threading.Lock()

_hot_cache = {"hot": set()}    # обновляется из главного цикла, чтобы не импортировать skill на каждый вызов


# ── Кольцевой буфер float32 с абсолютным счётчиком ───────────
class Ring:
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


# ── Потоковый энергетический VAD ─────────────────────────────
class Segmenter:
    """Режет поток 16 кГц float32 на фразы: отдаёт (start_abs, end_abs) в сэмплах.
    Окна 20 мс. Фон оценивается по первым ~0.5 с и дальше только в тишине."""
    WARMUP_WINDOWS = 25

    def __init__(self, rate, end_silence, ratio, min_rms, max_len, min_speech=0.15):
        self.win = int(0.02 * rate)
        self.end_n = max(1, int(end_silence / 0.02))
        self.min_n = max(1, int(min_speech / 0.02))
        self.max_n = int(max_len / 0.02)
        self.ratio, self.min_rms = ratio, min_rms
        self.noise = None
        self._warm = []
        self.pos = 0
        self.tail = np.zeros(0, dtype=np.float32)
        self.start = None
        self.speech_n = self.sil_n = self.len_n = 0

    def threshold(self) -> float:
        return max((self.noise or 0.0) * self.ratio, self.min_rms)

    def reset(self):
        self.start = None
        self.speech_n = self.sil_n = self.len_n = 0

    def feed(self, x: np.ndarray) -> list:
        out = []
        x = np.concatenate([self.tail, x]) if len(self.tail) else x
        n = len(x) // self.win
        self.tail = x[n * self.win:]
        for k in range(n):
            w = x[k * self.win:(k + 1) * self.win]
            rms = float(np.sqrt(np.mean(w * w)))
            w_start = self.pos
            self.pos += self.win

            if self.noise is None:                      # разогрев: оцениваем фон
                self._warm.append(rms)
                if len(self._warm) >= self.WARMUP_WINDOWS:
                    self.noise = float(np.median(self._warm))
                continue

            loud = rms > self.threshold()
            if self.start is None:
                if loud:
                    self.start, self.speech_n, self.sil_n, self.len_n = w_start, 1, 0, 1
                else:
                    self.noise = 0.98 * self.noise + 0.02 * rms
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


# ── Флаги ────────────────────────────────────────────────────
def _is_music_playing() -> bool:
    return rt.exists(rt.FLAG_MUSIC_PLAYING)

def _is_expecting_input() -> bool:
    return rt.exists(rt.FLAG_EXPECTING_INPUT)

def _session_open() -> bool:
    return rt.exists(rt.FLAG_SESSION_OPEN)


def _duck_on():
    global _duck_sent
    if _duck_sent:
        return
    _duck_sent = True
    log("asr: DUCK_ON → music (приглушаю)", tag="ASR")
    try:
        ipc.put(protocol.MUSIC_INBOX, protocol.music_control("duck_on"))
    except Exception as e:
        _duck_sent = False
        log(f"asr: duck_on error: {e}", tag="ASR", level="WARN")


def _duck_off():
    global _duck_sent
    if not _duck_sent:
        return
    _duck_sent = False
    log("asr: DUCK_OFF → music (восстанавливаю)", tag="ASR")
    try:
        ipc.put(protocol.MUSIC_INBOX, protocol.music_control("duck_off"))
    except Exception as e:
        log(f"asr: duck_off error: {e}", tag="ASR", level="WARN")


# ── Слова ────────────────────────────────────────────────────
def _get_hot_words() -> set:
    try:
        from brain.skills.controls import ControlsSkill
        return set(ControlsSkill.HOT_WORDS)
    except Exception:
        return set()


def _get_command_words() -> set:
    path = cfg.RESPONSES_DIR / _COMMANDS_FILE
    if not path.exists():
        return set()
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        words = data.get("words", [])
        if isinstance(words, list):
            return set(w.lower().strip() for w in words if w)
    except Exception as e:
        log(f"asr: commands.json: {e}", tag="ASR", level="WARN")
    return set()


def _grammar_words(hot_words) -> list:
    """Грамматика Vosk = «макс» + управляющие + да/нет + hot words skill'а.
    Только кириллица/пробел/дефис (цифры и т.п. в грамматику не пустить)."""
    words = {"макс"} | _CONTROL_WORDS | _YES_NO_WORDS
    for w in hot_words or ():
        if isinstance(w, str):
            words.add(w)
    ok = set()
    for w in words:
        w = w.strip().lower()
        if w and re.fullmatch(r"[а-яё\- ]+", w):
            ok.add(w)
    return sorted(ok) + ["[unk]"]


def _grammar_json(hot_words) -> str:
    return json.dumps(_grammar_words(hot_words), ensure_ascii=False)


def _is_command_word(text: str) -> bool:
    hot = _hot_cache["hot"]
    if not hot:
        return False
    tl = text.lower().strip()
    return bool(tl) and tl in hot


def _is_echo(text: str) -> bool:
    if _is_command_word(text):
        return False
    try:
        from workers.tts.state_file import get_state
        last = get_state().get("last_text", "") or ""
    except Exception:
        return False
    if not last or not text:
        return False
    ratio = difflib.SequenceMatcher(None, last.lower().strip(), text.lower().strip()).ratio()
    if ratio >= ECHO_SIMILARITY_THRESHOLD:
        log(f"asr: echo '{text}' ~ '{last}' ({ratio:.2f})", tag="ASR")
        return True
    return False


def _find_wake(text: str) -> int:
    """Индекс конца слова «макс» в тексте GigaAM (или -1). Допускает «макса/максу» и мелкие искажения."""
    for m in re.finditer(r"[а-яёa-z]+", text.lower()):
        w = m.group(0)
        if w == "макс":
            return m.end()
        if abs(len(w) - 4) <= 1 and difflib.SequenceMatcher(None, w, "макс").ratio() >= WAKE_FUZZY:
            return m.end()
    return -1


def _after_wake(text: str):
    """Текст команды после «макс» либо None, если слова «макс» нет."""
    idx = _find_wake(text)
    if idx < 0:
        return None
    return text[idx:].lstrip(" ,.!?:;-—").strip()


def _is_meaningful_dialog(text_lower: str, command_words: set) -> bool:
    if not text_lower:
        return False
    _SHORT_OK = ("спасибо", "привет", "да", "нет", "ок", "стоп", "хватит")
    if text_lower.strip() in _SHORT_OK:
        return True
    if command_words and any(w in text_lower for w in command_words):
        return True
    if any(w in text_lower for w in ("что", "кто", "где", "как", "почему")):
        return True
    if len(text_lower.split()) >= 3:
        return True
    return False


# ── Ожидание команды после бипа (с защитой от гонки таймеров) ──
def _start_wait(pos: int):
    global _waiting_for_command, _wait_gen, _wait_pos
    _wait_pos = pos
    _wait_gen += 1
    gen = _wait_gen
    _waiting_for_command = True
    threading.Thread(target=_wait_for_command_timeout, args=(gen,), daemon=True).start()


def _finish_wait():
    global _waiting_for_command, _wait_gen
    _waiting_for_command = False
    _wait_gen += 1


def _wait_for_command_timeout(gen: int):
    global _waiting_for_command
    time.sleep(cfg.COMMAND_TIMEOUT_SEC)
    if _waiting_for_command and gen == _wait_gen:
        _waiting_for_command = False
        log("asr: команда не поступила — WAKE_WAIT", tag="ASR")
        ipc.put(protocol.BRAIN_INBOX, protocol.event("wake_wait", source="asr"))


def _tts_speaking() -> bool:
    try:
        from workers.tts.state_file import get_state
        return bool(get_state().get("speaking", False))
    except Exception:
        return False


def _resample(audio: np.ndarray, from_rate: int, to_rate: int) -> np.ndarray:
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
    return np.interp(np.linspace(0, len(audio) - 1, new_len),
                     np.arange(len(audio)), audio).astype(audio.dtype)


def _hot_word_appropriate(text_lower: str) -> bool:
    if text_lower in _YES_NO_WORDS:
        return _is_expecting_input()
    return True


def _flush_queue(q: queue.Queue):
    while True:
        try:
            q.get_nowait()
        except queue.Empty:
            break


# ── Фоновый поток тишины (держит выходной поток открытым для AEC) ──
SILENCE_WAV_PATH = getattr(cfg, "SILENCE_WAV_PATH", r"D:\Maks\silence.wav")
_silence_stop = threading.Event()


def _silence_worker():
    import wave
    import sounddevice as sd
    try:
        with wave.open(SILENCE_WAV_PATH, 'rb') as wf:
            sr, ch = wf.getframerate(), wf.getnchannels()
            frames = wf.readframes(wf.getnframes())
        audio = np.frombuffer(frames, dtype=np.int16)
        if ch > 1:
            audio = audio.reshape(-1, ch).mean(axis=1).astype(np.int16)

        def callback(outdata, frames, time_info, status):
            nonlocal audio
            if len(audio) < frames:
                outdata[:len(audio), 0] = audio
                outdata[len(audio):, 0] = 0
                audio = np.zeros(0, dtype=np.int16)
            else:
                outdata[:, 0] = audio[:frames]
                audio = audio[frames:]

        with sd.OutputStream(samplerate=sr, channels=1, dtype='int16',
                             callback=callback, blocksize=480):
            while not _silence_stop.is_set():
                time.sleep(0.5)
    except Exception as e:
        log(f"asr: silence worker error: {e}", tag="ASR", level="WARN")


# ── Загрузка GigaAM: int8 + ограничение потоков, с откатом ───
def _load_gigaam():
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
            try:  # прогрев, чтобы первая настоящая команда не тормозила
                m.recognize(np.zeros(8000, dtype=np.float32), sample_rate=cfg.VOSK_RATE)
            except Exception:
                pass
            log(f"asr: GigaAM готов ({label})", tag="ASR")
            return m
        except Exception as e:
            log(f"asr: GigaAM ({label}) не загрузился: {e}", tag="ASR", level="WARN")
    log("asr: GigaAM не загрузился ни в одном режиме", tag="ASR", level="ERROR")
    return None


# ── Главный ────────────────────────────────────────────────────
def main():
    global _last_hot_word_at, _gigaam_busy, _rearm_until, _wake_req, _tts_stopped_at

    log("asr: старт", tag="ASR")
    ipc.init()
    try:
        ipc.put(protocol.MUSIC_INBOX, protocol.music_control("duck_off"))
    except Exception as e:
        log(f"asr: duck_off error: {e}", tag="ASR", level="WARN")

    # ── Vosk (грамматика: макс + hot words + да/нет) ──
    log(f"asr: загружаю Vosk: {cfg.VOSK_MODEL_PATH}", tag="ASR")
    try:
        model = Model(str(cfg.VOSK_MODEL_PATH))
    except Exception as e:
        log(f"asr: Vosk не загрузился: {e}", tag="ASR", level="ERROR")
        sys.exit(1)

    hot_words = _get_hot_words()
    command_words = _get_command_words()
    _hot_cache["hot"] = hot_words
    grammar = _grammar_json(hot_words)
    rec_hot = KaldiRecognizer(model, cfg.VOSK_RATE, grammar)
    log(f"asr: грамматика Vosk: {len(_grammar_words(hot_words))} слов; "
        f"HOT WORDS: {len(hot_words)}, COMMANDS: {len(command_words)}", tag="ASR")

    # ── GigaAM ──
    gigaam_model = _load_gigaam()

    # ── ViolaWake ──
    wake_detector = None
    if VIOLA_AVAILABLE:
        try:
            wake_detector = WakeDetector(
                model=cfg.WAKE_MODEL_PATH,
                threshold=cfg.WAKE_MODEL_THRESHOLD,
                cooldown_s=cfg.WAKE_COOLDOWN_S,
            )
            log("asr: ViolaWake детектор загружен", tag="ASR")
        except Exception as e:
            log(f"asr: ViolaWake не загрузился: {e}", tag="ASR", level="ERROR")

    # ── Очереди: у каждого потребителя СВОЯ ──
    vosk_q = queue.Queue(maxsize=100)
    viola_q = queue.Queue(maxsize=100)
    ring = Ring(GIGAAM_MAX_BUFFER_SEC, cfg.VOSK_RATE)

    sr = cfg.VOSK_RATE
    segmenter = Segmenter(sr, VAD_END_SILENCE_SEC, VAD_RATIO, VAD_MIN_RMS, VAD_MAX_SEG_SEC)
    segments = collections.deque(maxlen=6)
    seg_lock = threading.Lock()

    def _seg_find(pred):
        with seg_lock:
            for sg in list(segments):
                if pred(sg):
                    return sg
        return None

    def _seg_consume(upto_end: int):
        with seg_lock:
            keep = [sg for sg in segments if sg[1] > upto_end]
            segments.clear()
            segments.extend(keep)

    def _seg_clear():
        with seg_lock:
            segments.clear()
        segmenter.reset()

    def _wait_segment(pred, timeout: float):
        t0 = time.time()
        while time.time() - t0 < timeout:
            sg = _seg_find(pred)
            if sg:
                return sg
            time.sleep(0.02)
        return None

    def _seg_audio(sg):
        return ring.get(sg[0] - int(SEG_PAD_BEFORE_SEC * sr), sg[1] + int(SEG_PAD_AFTER_SEC * sr))

    capture = None

    def on_aec_frame(frame):
        try:
            samples = np.clip(np.asarray(frame.microphone_clean, dtype=np.float32), -1.0, 1.0)
            int16 = (samples * 32767).astype(np.int16)
            a16 = _resample(int16, 48000, cfg.VOSK_RATE)
            if len(a16) == 0:
                return
            f32 = a16.astype(np.float32) / 32768.0
            ring.push(f32)
            for sg in segmenter.feed(f32):
                with seg_lock:
                    segments.append(sg)
                if VAD_DEBUG:
                    log(f"asr: [VAD] фраза {(sg[1] - sg[0]) / sr:.2f}с, "
                        f"порог {segmenter.threshold():.4f}", tag="ASR")
            end_pos = ring.total
            try:
                vosk_q.put_nowait(a16)
            except queue.Full:
                pass
            try:
                viola_q.put_nowait((a16, end_pos))
            except queue.Full:
                pass
        except Exception as e:
            log(f"asr: on_aec_frame error: {e}", tag="ASR", level="WARN")

    if ECHOFF_AVAILABLE:
        try:
            aec_config = AecConfig(stream_delay_ms=50)
            capture = AecCapture(aec_config, on_frame=on_aec_frame)
            capture.start()
            log("asr: echoff AEC запущен", tag="ASR")
        except Exception as e:
            log(f"asr: echoff не запустился: {e}", tag="ASR", level="ERROR")

    if capture is not None:
        threading.Thread(target=_silence_worker, daemon=True).start()

    def _recognize(audio: np.ndarray) -> str:
        if gigaam_model is None or len(audio) < cfg.VOSK_RATE * 0.3:
            return ""
        try:
            out = gigaam_model.recognize(audio, sample_rate=cfg.VOSK_RATE)
            return str(out).strip() if out else ""
        except Exception as e:
            log(f"asr: [GIGAAM] ошибка: {e}", tag="ASR", level="WARN")
            return ""

    def _trigger_wake(source: str, pos=None) -> bool:
        global _gigaam_busy, _wake_req
        with _trig_lock:
            if _gigaam_busy or _waiting_for_command or time.time() < _rearm_until:
                return False
            _gigaam_busy = True
            _wake_req = (ring.total if pos is None else pos, source)
        _wake_triggered.set()
        return True

    # ── Поток GigaAM (стенографист) ──────────────────────────
    def gigaam_worker():
        global _gigaam_busy, _rearm_until
        while True:
            if not _wake_triggered.wait(timeout=0.5):
                continue
            _wake_triggered.clear()
            try:
                pos, source = _wake_req
                if source == "viola":
                    margin = int(WAKE_SEG_MARGIN_SEC * sr)
                    sg = _wait_segment(lambda x: x[0] <= pos and x[1] >= pos - margin,
                                       WAKE_SEG_TIMEOUT_SEC)
                else:
                    margin = int(1.5 * sr)
                    sg = _wait_segment(lambda x: x[1] >= pos - margin, VOSK_SEG_TIMEOUT_SEC)

                if sg:
                    audio = _seg_audio(sg)
                    _seg_consume(sg[1])
                else:  # VAD не отдал фразу — запасное фиксированное окно
                    log("asr: VAD не вернул фразу — запасное окно", tag="ASR", level="WARN")
                    if source == "viola":
                        audio = ring.get(pos - int(WAKE_PRE_SEC * sr))
                    else:
                        audio = ring.get(pos - int(VOSK_TAIL_SEC * sr), pos)

                text = _recognize(audio)
                log(f"asr: [GIGAAM/{source}] «{text}»", tag="ASR")

                if not text:
                    log("asr: речи нет — ложный wake, игнор", tag="ASR")
                    continue

                command = _after_wake(text)
                if command is None:
                    log("asr: «макс» в тексте нет — ложный wake, игнор", tag="ASR")
                    continue

                if command:
                    log(f"asr: [МАКС] + команда «{command}»", tag="ASR")
                    ipc.put(protocol.BRAIN_INBOX,
                            protocol.command(command, source=CommandSource.VOICE))
                else:
                    play_beep()
                    log("asr: [МАКС] (бип, ждём команду)", tag="ASR")
                    ipc.put(protocol.BRAIN_INBOX, protocol.event("wake", source="asr"))
                    _start_wait(ring.total)
            except Exception as e:
                log(f"asr: gigaam_worker: {e}", tag="ASR", level="ERROR")
            finally:
                _rearm_until = time.time() + WAKE_REARM_SEC
                _gigaam_busy = False

    threading.Thread(target=gigaam_worker, daemon=True).start()
    log("asr: поток GigaAM запущен", tag="ASR")

    # ── Поток ViolaWake ──────────────────────────────────────
    def viola_worker():
        if wake_detector is None:
            return
        while True:
            try:
                chunk, chunk_end = viola_q.get(timeout=0.1)
            except queue.Empty:
                continue
            if _tts_speaking() or (time.time() - _tts_stopped_at) < POST_TTS_MUTE_WAKE_SEC:
                continue
            try:
                hit = wake_detector.detect(chunk)
            except Exception as e:
                log(f"asr: viola detect: {e}", tag="ASR", level="WARN")
                continue
            if hit:
                log("asr: [VIOLAWAKE] услышал «Макс»", tag="ASR")
                _trigger_wake("viola", chunk_end)

    if wake_detector is not None:
        threading.Thread(target=viola_worker, daemon=True).start()
        log("asr: поток ViolaWake запущен", tag="ASR")

    # ── Основной цикл (Vosk) ──
    _was_speaking = False
    _mic_on_logged = False
    _last_hw_reload = time.time()

    try:
        while True:
            now = time.time()

            # ВАЖНО: проверка TTS стоит ПЕРВОЙ, до чтения очереди.
            # Как только TTS поднял speaking=True (в TTS-воркере это теперь
            # происходит ДО синтеза), мы сразу глушим музыку и чистим буфер.
            if _tts_speaking():
                if not _was_speaking:
                    _was_speaking = True
                    _mic_on_logged = False
                    log("asr: микрофон ВЫКЛ (TTS идёт)", tag="ASR")
                    _duck_on()
                _flush_queue(vosk_q)
                _seg_clear()
                time.sleep(0.05)
                continue

            if _was_speaking:
                _was_speaking = False
                _tts_stopped_at = now
                _flush_queue(vosk_q)
                try:
                    rec_hot.Reset()
                except Exception:
                    try:
                        rec_hot = KaldiRecognizer(model, cfg.VOSK_RATE, grammar)
                    except Exception as e:
                        log(f"asr: сброс Vosk: {e}", tag="ASR", level="WARN")

            mute = POST_TTS_MUTE_WAKE_SEC if _waiting_for_command else POST_TTS_MUTE_SEC
            if (now - _tts_stopped_at) < mute:
                _seg_clear()
                time.sleep(0.05)
                continue

            if not _mic_on_logged:
                _mic_on_logged = True
                log(f"asr: микрофон ВКЛ через {mute:.1f}s", tag="ASR")
            _duck_off()

            if capture is None:
                time.sleep(0.1)
                continue

            try:
                data_np = vosk_q.get(timeout=0.1)
            except queue.Empty:
                continue

            if now - _last_hw_reload > HOT_WORDS_RELOAD_SEC:
                hot_words = _get_hot_words()
                command_words = _get_command_words()
                _hot_cache["hot"] = hot_words
                _last_hw_reload = now
                new_grammar = _grammar_json(hot_words)
                if new_grammar != grammar:
                    grammar = new_grammar
                    try:
                        rec_hot = KaldiRecognizer(model, cfg.VOSK_RATE, grammar)
                        log(f"asr: грамматика обновлена ({len(_grammar_words(hot_words))} слов)",
                            tag="ASR")
                    except Exception as e:
                        log(f"asr: обновление грамматики: {e}", tag="ASR", level="WARN")

            # ── Ждём команду после бипа: следующая фраза по VAD ──
            if _waiting_for_command and gigaam_model is not None:
                back = int(0.3 * sr)
                sg = _seg_find(lambda x: x[0] >= _wait_pos - back)
                if sg:
                    _seg_consume(sg[1])
                    gtext = _recognize(_seg_audio(sg))
                    if gtext:
                        after = _after_wake(gtext)
                        cmd = gtext if after is None else after
                        if cmd:
                            _finish_wait()
                            log(f"asr: команда «{cmd}» (GigaAM, VAD)", tag="ASR")
                            ipc.put(protocol.BRAIN_INBOX,
                                    protocol.command(cmd, source=CommandSource.VOICE))

            if not rec_hot.AcceptWaveform(data_np.tobytes()):
                continue

            if _tts_speaking():
                rec_hot.Result()
                continue

            result = json.loads(rec_hot.Result())
            text = result.get('text', '').strip()
            if not text:
                continue

            text_lower = text.lower().strip()
            if _is_echo(text):
                continue

            # ── Vosk услышал «макс» ──
            if "макс" in text_lower.split():
                if _trigger_wake("vosk"):
                    log(f"asr: [VOSK] «макс» в «{text}»", tag="ASR")
                continue

            # ── Pending (да/нет) ──
            if _is_expecting_input() and text_lower in _YES_NO_WORDS:
                if (now - _last_hot_word_at) < HOT_WORD_COOLDOWN_SEC:
                    continue
                _last_hot_word_at = now
                ipc.put(protocol.BRAIN_INBOX,
                        protocol.command(text, source=CommandSource.VOICE_HOT))
                continue

            # ── Hot words ──
            if _is_music_playing() or _session_open():
                if text_lower in hot_words and _hot_word_appropriate(text_lower):
                    if (now - _last_hot_word_at) < HOT_WORD_COOLDOWN_SEC:
                        continue
                    _last_hot_word_at = now
                    ipc.put(protocol.BRAIN_INBOX,
                            protocol.command(text, source=CommandSource.VOICE_HOT))
                    continue
                if text_lower in ("хватит", "достаточно", "стоп", "замолчи"):
                    ipc.put(protocol.BRAIN_INBOX,
                            protocol.command(text, source=CommandSource.VOICE_DIALOG))
                    continue

    except Exception as e:
        log(f"asr: ошибка в главном цикле: {e}", tag="ASR", level="ERROR")
    finally:
        _silence_stop.set()
        if capture:
            try:
                capture.stop()
            except Exception:
                pass
        log("asr: остановлен", tag="ASR")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("asr: остановка по Ctrl+C", tag="ASR")
        sys.exit(0)