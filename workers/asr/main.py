"""
workers/asr/main.py — ASR worker (v8.2).

Роли:
  • Vosk — единственный wake-детектор («макс» в грамматике) + hot-words.
  • ViolaWake — ОТКЛЮЧЁН (VIOLA_ENABLED=False). Не ловит «Макс» в слитной
    фразе, поэтому убран. Можно включить обратно через флаг.
  • VAD — работает ПОСТОЯННО, копит плотные сегменты в deque.
  • GigaAM — единственный распознаватель свободной речи, берёт плотный
    VAD-сегмент.

Как работает «Макс включи музыку» слитно:
  * Vosk с грамматикой ловит «макс» в искажённой фразе («макс громче музыка»),
    триггерит wake с pos = data_end_pos (конец фразы Vosk).
  * VAD уже создал плотный сегмент «макс включи музыку» (потому что Vosk
    закрывает фразу по silence, и VAD тоже по silence — они синхронны).
  * GigaAM берёт этот сегмент, распознаёт «макс включи музыку»,
    _after_wake отрезает «макс», команда уходит в brain.
  * Если VAD-сегмент не найден за VOSK_SEG_TIMEOUT_SEC — fallback
    ring.get(pos - VOSK_TAIL_SEC, pos) = 4с аудио.

Фиксы v8.1 сохранены:
  • VAD_RATIO=3.0, VAD_MIN_RMS=0.004 — VAD видит тихую речь.
  • VAD_DEBUG=False — без спама в лог.
  • _dialog_advance после обработки — DIALOG не берёт то же аудио.
  • Дедуп одинаковых команд (DUP_CMD_WINDOW_SEC=4.0).
  • WAKE_FUZZY=0.7 — «мэкс», «макц» ловятся.
  • vosk_q maxsize=200 — меньше дропов.
  • WAIT эксклюзивен (Vosk не обрабатывается, пока ждём команду).
"""

import difflib
import io
import json
import os
import queue
import re
import signal
import sys
import threading
import time
import collections

if os.name == 'nt':
    os.system('chcp 65001 > nul')
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import numpy as np
import onnx_asr
from vosk import Model, KaldiRecognizer

try:
    from violawake_sdk import WakeDetector
    VIOLA_AVAILABLE = True
except ImportError:
    VIOLA_AVAILABLE = False
    WakeDetector = None

# Отключаем Viola: не ловит «Макс» в слитной фразе. Включить = True.
VIOLA_ENABLED = False

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
GIGAAM_MAX_BUFFER_SEC = 10.0
WAKE_REARM_SEC = 1.2

# ── VAD ──
VAD_END_SILENCE_SEC = getattr(cfg, "VAD_END_SILENCE_SEC", 0.5)
VAD_RATIO = getattr(cfg, "VAD_RATIO", 3.0)
VAD_MIN_RMS = getattr(cfg, "VAD_MIN_RMS", 0.004)
VAD_MAX_SEG_SEC = getattr(cfg, "VAD_MAX_SEG_SEC", 8.0)
VAD_MIN_SPEECH_SEC = 0.15
VAD_DEBUG = getattr(cfg, "VAD_DEBUG", False)

# ── Wake ──
WAKE_FUZZY = 0.7

# Vosk-wake (основной)
VOSK_SEG_TIMEOUT_SEC = 1.5     # сколько ждать VAD-сегмент после data_end_pos
VOSK_TAIL_SEC = 4.0            # fallback: сколько брать назад от data_end_pos

# Viola-wake (если включён)
WAKE_SEG_MARGIN_SEC = 0.6
WAKE_SEG_TIMEOUT_SEC = 6.0
WAKE_PRE_SEC = 1.0
WAKE_TAIL_SEC = 3.0

# ── Паддинги VAD-сегмента ──
SEG_PAD_BEFORE_SEC = 0.25
SEG_PAD_AFTER_SEC = 0.15

# ── WAIT ──
WAIT_EARLY_SILENCE_SEC = 2.0

# ── DIALOG ──
DIALOG_WINDOW_SEC = 10.0
DIALOG_SEG_POLL_SEC = 0.1

DUP_CMD_WINDOW_SEC = 4.0

_GIGAAM_ERROR_LIMIT = 5
_GIGAAM_RELOAD_COOLDOWN_SEC = 60.0

_STATS_LOG_SEC = 60.0
_STATS = {
    "vad_segments": 0,
    "vad_dropped": 0,
    "gigaam_requests": 0,
    "gigaam_ok": 0,
    "gigaam_empty": 0,
    "gigaam_error": 0,
    "wake_vosk": 0,
    "wake_viola": 0,
    "hot_words": 0,
    "wait_commands": 0,
    "wait_timeouts": 0,
    "dialog_commands": 0,
    "dup_commands": 0,
    "gigaam_reloads": 0,
}
_stats_lock = threading.Lock()


def _stat_inc(key: str, n: int = 1):
    with _stats_lock:
        _STATS[key] = _STATS.get(key, 0) + n


def _stats_snapshot() -> dict:
    with _stats_lock:
        return dict(_STATS)


_COMMANDS_FILE = "commands.json"
_YES_NO_WORDS = {"да", "нет", "ок", "окей", "подтверждаю", "не надо", "неважно",
                 "не важно", "не хочу", "отмена"}
_CONTROL_WORDS = {"дальше", "назад", "пауза", "продолжи", "стоп", "громче", "тише",
                  "хватит", "достаточно", "замолчи"}

# ── Глобальное состояние ──────────────────────────────────────
_shutdown = threading.Event()

_waiting_for_command = False
_wait_pos = 0
_wait_gen = 0
_wait_lock = threading.Lock()

_last_hot_word_at = 0.0
_tts_stopped_at = 0.0

_gigaam_busy = False
_gigaam_lock = threading.Lock()
_wake_req = (0, "vosk", 0.0)
_rearm_until = 0.0
_wake_triggered = threading.Event()
_trig_lock = threading.Lock()

_duck_sent = False
_duck_lock = threading.Lock()
_last_duck_warn_at = 0.0
_wake_active = False
_wake_active_lock = threading.Lock()

_dialog_until = 0.0
_dialog_start_pos = 0
_dialog_lock = threading.Lock()

_hot_cache = {"hot": set()}
_hot_lock = threading.Lock()

_TTS_STUCK_LIMIT = 30.0
_TTS_STUCK_WARN_COOLDOWN_SEC = 5.0
_last_duck_on_at = 0.0

_GIGAAM_ERROR_STREAK = 0
_GIGAAM_LAST_RELOAD_AT = 0.0

_last_cmd_text = ""
_last_cmd_at = 0.0
_cmd_lock = threading.Lock()

_SESSION_ID = int(time.time())


def _is_dup_command(text: str) -> bool:
    global _last_cmd_text, _last_cmd_at
    norm = text.lower().strip()
    with _cmd_lock:
        now = time.time()
        if norm and norm == _last_cmd_text and (now - _last_cmd_at) < DUP_CMD_WINDOW_SEC:
            _stat_inc("dup_commands")
            return True
        _last_cmd_text = norm
        _last_cmd_at = now
        return False


# ── Ring ────────────────────────────────────────────────────
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


# ── VAD ─────────────────────────────────────────────────────
class Segmenter:
    WARMUP_WINDOWS = 25

    def __init__(self, rate, end_silence, ratio, min_rms, max_len,
                 min_speech=VAD_MIN_SPEECH_SEC):
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


# ── Ducking ─────────────────────────────────────────────────
def _duck_on():
    global _duck_sent, _last_duck_on_at
    with _duck_lock:
        if _duck_sent:
            return
        _duck_sent = True
        _last_duck_on_at = time.time()
    log("asr: DUCK_ON → music (приглушаю)", tag="ASR")
    try:
        ipc.put(protocol.MUSIC_INBOX, protocol.music_control("duck_on"))
    except Exception as e:
        with _duck_lock:
            _duck_sent = False
        log(f"asr: duck_on error: {e}", tag="ASR", level="WARN")


def _duck_off():
    global _duck_sent
    with _duck_lock:
        if not _duck_sent:
            return
    log("asr: DUCK_OFF → music (восстанавливаю)", tag="ASR")
    try:
        ipc.put(protocol.MUSIC_INBOX, protocol.music_control("duck_off"))
        with _duck_lock:
            _duck_sent = False
    except Exception as e:
        log(f"asr: duck_off error (retry): {e}", tag="ASR", level="WARN")


def _set_wake_active(val: bool):
    global _wake_active
    with _wake_active_lock:
        _wake_active = val


def _is_wake_active() -> bool:
    with _wake_active_lock:
        return _wake_active


def _duck_is_sent() -> bool:
    with _duck_lock:
        return _duck_sent


def duck_worker():
    global _last_duck_warn_at
    while not _shutdown.is_set():
        try:
            speaking = _tts_speaking()
            wake = _is_wake_active()
            sess = _session_open()
            need_duck = speaking or wake or sess

            if _duck_is_sent() and not need_duck:
                if (time.time() - _last_duck_on_at) > _TTS_STUCK_LIMIT:
                    now = time.time()
                    if now - _last_duck_warn_at >= _TTS_STUCK_WARN_COOLDOWN_SEC:
                        _last_duck_warn_at = now
                        log("asr: ducking held too long without reason (TTL warn)",
                            tag="ASR", level="WARN")

            if need_duck:
                _duck_on()
            else:
                _duck_off()
            time.sleep(0.05)
        except Exception as e:
            log(f"asr: duck_worker error: {e}", tag="ASR", level="WARN")
            time.sleep(0.5)


# ── Флаги ───────────────────────────────────────────────────
def _is_music_playing() -> bool:
    return rt.exists(rt.FLAG_MUSIC_PLAYING)

def _is_expecting_input() -> bool:
    return rt.exists(rt.FLAG_EXPECTING_INPUT)

def _session_open() -> bool:
    return rt.exists(rt.FLAG_SESSION_OPEN)


# ── Окно диалога ────────────────────────────────────────────
def _open_dialog_window(start_pos: int):
    global _dialog_until, _dialog_start_pos
    with _dialog_lock:
        now = time.time()
        was_active = now < _dialog_until
        if not was_active:
            _dialog_start_pos = start_pos
        _dialog_until = now + DIALOG_WINDOW_SEC


def _dialog_advance(pos: int):
    global _dialog_start_pos
    with _dialog_lock:
        if pos > _dialog_start_pos:
            _dialog_start_pos = pos


def _dialog_is_active() -> bool:
    with _dialog_lock:
        return time.time() < _dialog_until


def _dialog_start_pos_get() -> int:
    with _dialog_lock:
        return _dialog_start_pos


# ── Слова ───────────────────────────────────────────────────
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
            return {w.lower().strip() for w in words if isinstance(w, str) and w.strip()}
    except Exception as e:
        log(f"asr: commands.json: {e}", tag="ASR", level="WARN")
    return set()


def _grammar_words(hot_words) -> list:
    # «макс» возвращён — Vosk снова wake-детектор.
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
    with _hot_lock:
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
    if len(last) > 500 or len(text) > 500:
        return False
    ratio = difflib.SequenceMatcher(None, last.lower().strip(), text.lower().strip()).ratio()
    if ratio >= ECHO_SIMILARITY_THRESHOLD:
        log(f"asr: echo '{text}' ~ '{last}' ({ratio:.2f})", tag="ASR")
        return True
    return False


def _after_wake(text: str):
    tl = text.lower()
    for m in re.finditer(r"[а-яёa-z]+", tl):
        w = m.group(0)
        if w == "макс" or (
            abs(len(w) - 4) <= 1
            and difflib.SequenceMatcher(None, w, "макс").ratio() >= WAKE_FUZZY
        ):
            return text[m.end():].lstrip(" ,.!?:;-—").strip()
    return None


# ── WAIT ────────────────────────────────────────────────────
def _start_wait(pos: int):
    global _waiting_for_command, _wait_gen, _wait_pos
    with _wait_lock:
        _wait_pos = pos
        _wait_gen += 1
        gen = _wait_gen
        _waiting_for_command = True
    threading.Thread(target=_wait_for_command_timeout, args=(gen,),
                     daemon=True, name="asr-wait-timeout").start()


def _finish_wait():
    global _waiting_for_command, _wait_gen
    with _wait_lock:
        _waiting_for_command = False
        _wait_gen += 1


def _wait_for_command_timeout(gen: int):
    global _waiting_for_command
    deadline = time.time() + cfg.COMMAND_TIMEOUT_SEC
    while time.time() < deadline:
        if _shutdown.is_set():
            return
        time.sleep(0.1)
    should_fire = False
    with _wait_lock:
        if _waiting_for_command and gen == _wait_gen:
            _waiting_for_command = False
            should_fire = True
    if should_fire:
        _stat_inc("wait_timeouts")
        log("asr: команда не поступила — WAKE_WAIT", tag="ASR")
        try:
            ipc.put(protocol.BRAIN_INBOX, protocol.event("wake_wait", source="asr"))
        except Exception as e:
            log(f"asr: wake_wait ipc error: {e}", tag="ASR", level="WARN")


def _is_waiting() -> bool:
    with _wait_lock:
        return _waiting_for_command


def _wait_pos_get() -> int:
    with _wait_lock:
        return _wait_pos


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


# ── Silence worker ─────────────────────────────────────────
SILENCE_WAV_PATH = getattr(cfg, "SILENCE_WAV_PATH", r"D:\Maks\silence.wav")
_silence_stop = threading.Event()


def _silence_worker():
    try:
        import wave
        import sounddevice as sd

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
            while not (_silence_stop.is_set() or _shutdown.is_set()):
                time.sleep(0.5)
    except Exception as e:
        log(f"asr: silence worker error: {e}", tag="ASR", level="WARN")


# ── GigaAM loading ─────────────────────────────────────────
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


def _extract_text(out) -> str:
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
                s = _extract_text(x)
                if s:
                    parts.append(s)
        return " ".join(parts).strip()
    if isinstance(out, dict):
        for key in ("text", "transcript", "result"):
            if key in out:
                return _extract_text(out[key])
        return ""
    t = getattr(out, "text", None)
    if isinstance(t, str):
        return t.strip()
    return str(out).strip()


def _handle_shutdown(signum, frame):
    log(f"asr: сигнал {signum} получен, завершаю работу...", tag="ASR")
    _shutdown.set()
    _silence_stop.set()
    _wake_triggered.set()


# ── Главный ─────────────────────────────────────────────────
def main():
    global _last_hot_word_at, _gigaam_busy, _rearm_until, _wake_req, _tts_stopped_at
    global _GIGAAM_ERROR_STREAK, _GIGAAM_LAST_RELOAD_AT

    signal.signal(signal.SIGINT, _handle_shutdown)
    signal.signal(signal.SIGTERM, _handle_shutdown)

    log(f"asr: старт (session_id={_SESSION_ID}, viola_enabled={VIOLA_ENABLED})", tag="ASR")
    ipc.init()

    try:
        ipc.put(protocol.MUSIC_INBOX, protocol.music_control("duck_off"))
    except Exception as e:
        log(f"asr: initial duck_off error: {e}", tag="ASR", level="WARN")

    log(f"asr: загружаю Vosk: {cfg.VOSK_MODEL_PATH}", tag="ASR")
    try:
        model = Model(str(cfg.VOSK_MODEL_PATH))
    except Exception as e:
        log(f"asr: Vosk не загрузился: {e}", tag="ASR", level="ERROR")
        sys.exit(1)

    hot_words = _get_hot_words()
    command_words = _get_command_words()
    with _hot_lock:
        _hot_cache["hot"] = hot_words
    grammar = _grammar_json(hot_words)
    rec_hot = KaldiRecognizer(model, cfg.VOSK_RATE, grammar)
    log(f"asr: грамматика Vosk: {len(_grammar_words(hot_words))} слов; "
        f"HOT WORDS: {len(hot_words)}, COMMANDS: {len(command_words)}", tag="ASR")
    log(f"asr: VAD конфиг: ratio={VAD_RATIO} min_rms={VAD_MIN_RMS} "
        f"end_silence={VAD_END_SILENCE_SEC} vosk_seg_timeout={VOSK_SEG_TIMEOUT_SEC}", tag="ASR")

    gigaam_model = _load_gigaam()

    wake_detector = None
    if VIOLA_ENABLED and VIOLA_AVAILABLE:
        try:
            wake_detector = WakeDetector(
                model=cfg.WAKE_MODEL_PATH,
                threshold=cfg.WAKE_MODEL_THRESHOLD,
                cooldown_s=cfg.WAKE_COOLDOWN_S,
            )
            log("asr: ViolaWake детектор загружен", tag="ASR")
        except Exception as e:
            log(f"asr: ViolaWake не загрузился: {e}", tag="ASR", level="ERROR")
    else:
        log("asr: ViolaWake ОТКЛЮЧЁН (wake только через Vosk)", tag="ASR")

    vosk_q = queue.Queue(maxsize=200)
    viola_q = queue.Queue(maxsize=100)
    ring = Ring(GIGAAM_MAX_BUFFER_SEC, cfg.VOSK_RATE)

    sr = cfg.VOSK_RATE
    segmenter = Segmenter(sr, VAD_END_SILENCE_SEC, VAD_RATIO, VAD_MIN_RMS,
                          VAD_MAX_SEG_SEC, VAD_MIN_SPEECH_SEC)
    seg_feed_lock = threading.Lock()
    segments = collections.deque(maxlen=12)
    seg_lock = threading.Lock()

    def _seg_push(sg):
        dropped = False
        with seg_lock:
            if len(segments) == segments.maxlen:
                dropped = True
            segments.append(sg)
        if dropped:
            _stat_inc("vad_dropped")

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
        with seg_feed_lock:
            segmenter.reset()

    def _wait_segment(pred, timeout: float):
        t0 = time.time()
        while time.time() - t0 < timeout:
            if _shutdown.is_set():
                return None
            sg = _seg_find(pred)
            if sg:
                return sg
            time.sleep(0.02)
        return None

    def _seg_audio(sg):
        return ring.get(sg[0] - int(SEG_PAD_BEFORE_SEC * sr),
                        sg[1] + int(SEG_PAD_AFTER_SEC * sr))

    capture = None
    _queue_full_warn_at = [0.0]

    def on_aec_frame(frame):
        try:
            samples = np.clip(np.asarray(frame.microphone_clean, dtype=np.float32), -1.0, 1.0)
            int16 = (samples * 32767).astype(np.int16)
            a16 = _resample(int16, 48000, cfg.VOSK_RATE)
            if len(a16) == 0:
                return
            f32 = a16.astype(np.float32) / 32768.0
            ring.push(f32)

            with seg_feed_lock:
                new_segs = segmenter.feed(f32)
            for sg in new_segs:
                _seg_push(sg)
                _stat_inc("vad_segments")
                if VAD_DEBUG:
                    log(f"asr: [VAD] сегмент {(sg[1] - sg[0]) / sr:.2f}с "
                        f"порог {segmenter.threshold():.4f}",
                        tag="ASR", level="DEBUG")

            end_pos = ring.total
            try:
                vosk_q.put_nowait((a16, end_pos))
            except queue.Full:
                now = time.time()
                if now - _queue_full_warn_at[0] >= 5.0:
                    _queue_full_warn_at[0] = now
                    log("asr: vosk_q full — drop frame", tag="ASR", level="WARN")

            if VIOLA_ENABLED:
                try:
                    viola_q.put_nowait((a16, end_pos))
                except queue.Full:
                    now = time.time()
                    if now - _queue_full_warn_at[0] >= 5.0:
                        _queue_full_warn_at[0] = now
                        log("asr: viola_q full — drop frame", tag="ASR", level="WARN")
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

    threads = []

    if capture is not None:
        t = threading.Thread(target=_silence_worker, daemon=True, name="asr-silence")
        t.start()
        threads.append(t)

    t = threading.Thread(target=duck_worker, daemon=True, name="asr-duck")
    t.start()
    threads.append(t)

    def stats_worker():
        while not _shutdown.wait(_STATS_LOG_SEC):
            log(f"asr: stats {_stats_snapshot()}", tag="ASR")

    t = threading.Thread(target=stats_worker, daemon=True, name="asr-stats")
    t.start()
    threads.append(t)

    def _recognize(audio: np.ndarray) -> str:
        nonlocal gigaam_model
        global _GIGAAM_ERROR_STREAK
        if len(audio) < cfg.VOSK_RATE * 0.3:
            return ""
        _stat_inc("gigaam_requests")
        with _gigaam_lock:
            m = gigaam_model
            if m is None:
                _GIGAAM_ERROR_STREAK += 1
                _stat_inc("gigaam_error")
                return ""
            try:
                out = m.recognize(audio, sample_rate=cfg.VOSK_RATE)
                text = _extract_text(out)
                exc = False
            except Exception as e:
                log(f"asr: [GIGAAM] ошибка: {e}", tag="ASR", level="WARN")
                text = ""
                exc = True

            if exc:
                _GIGAAM_ERROR_STREAK += 1
                _stat_inc("gigaam_error")
            elif not text:
                _GIGAAM_ERROR_STREAK = 0
                _stat_inc("gigaam_empty")
            else:
                _GIGAAM_ERROR_STREAK = 0
                _stat_inc("gigaam_ok")
            return text

    def _maybe_reload_gigaam():
        nonlocal gigaam_model
        global _GIGAAM_ERROR_STREAK, _GIGAAM_LAST_RELOAD_AT
        with _gigaam_lock:
            if _GIGAAM_ERROR_STREAK < _GIGAAM_ERROR_LIMIT:
                return
            now = time.time()
            if now - _GIGAAM_LAST_RELOAD_AT < _GIGAAM_RELOAD_COOLDOWN_SEC:
                return
            log("asr: GigaAM серия ошибок, перезагружаю модель...", tag="ASR", level="WARN")
            _GIGAAM_ERROR_STREAK = 0
            _GIGAAM_LAST_RELOAD_AT = now
            gigaam_model = _load_gigaam()
            _stat_inc("gigaam_reloads")

    def _have_model() -> bool:
        with _gigaam_lock:
            return gigaam_model is not None

    def _trigger_wake(source: str, pos=None) -> bool:
        global _gigaam_busy, _wake_req
        with _trig_lock:
            if _gigaam_busy or _is_waiting() or time.time() < _rearm_until:
                return False
            _gigaam_busy = True
            _wake_req = (ring.total if pos is None else pos, source, time.time())
        _set_wake_active(True)
        _wake_triggered.set()
        _stat_inc(f"wake_{source}")
        return True

    def gigaam_worker():
        nonlocal gigaam_model
        global _gigaam_busy, _rearm_until
        while not _shutdown.is_set():
            if _wake_triggered.wait(timeout=DIALOG_SEG_POLL_SEC):
                _wake_triggered.clear()
                if _shutdown.is_set():
                    break
                try:
                    with _trig_lock:
                        pos, source, t_trig = _wake_req

                    vad_ok = False
                    if source == "vosk":
                        # Vosk даёт data_end_pos = конец фразы (после silence).
                        # VAD к этому моменту уже создал сегмент (он тоже
                        # закрывается по silence). Ищем его.
                        margin = int(VOSK_SEG_TIMEOUT_SEC * sr)  # 1.5с
                        sg = _wait_segment(
                            lambda x: x[1] >= pos - margin and x[0] <= pos,
                            VOSK_SEG_TIMEOUT_SEC,
                        )
                        if sg:
                            audio = _seg_audio(sg)
                            _seg_consume(sg[1])
                            _dialog_advance(sg[1])
                            vad_ok = True
                        else:
                            audio = ring.get(max(0, pos - int(VOSK_TAIL_SEC * sr)), pos)
                            _seg_consume(pos)
                            _dialog_advance(pos)
                            log(f"asr: [GIGAAM/vosk] VAD сегмент не найден — fallback "
                                f"({VOSK_TAIL_SEC:.1f}с до data_end_pos)",
                                tag="ASR", level="WARN")
                    else:
                        # Viola (если включён): pos = chunk_end
                        margin = int(WAKE_SEG_MARGIN_SEC * sr)
                        sg = _wait_segment(
                            lambda x: x[0] <= pos and x[1] >= pos - margin,
                            WAKE_SEG_TIMEOUT_SEC,
                        )
                        if sg:
                            audio = _seg_audio(sg)
                            _seg_consume(sg[1])
                            _dialog_advance(sg[1])
                            vad_ok = True
                        else:
                            end_pos = min(ring.total, pos + int(WAKE_TAIL_SEC * sr))
                            audio = ring.get(max(0, pos - int(WAKE_PRE_SEC * sr)), end_pos)
                            _seg_consume(end_pos)
                            _dialog_advance(end_pos)
                            log(f"asr: [GIGAAM/viola] VAD сегмент не найден — fallback",
                                tag="ASR", level="WARN")

                    t_rec = time.time()
                    text = _recognize(audio)
                    log(f"asr: [GIGAAM/{source}] «{text}»", tag="ASR")
                    log(f"asr: wake timing: vad_ok={vad_ok} "
                        f"audio={len(audio) / sr:.2f}s recog={time.time() - t_rec:.2f}s "
                        f"since_trigger={time.time() - t_trig:.2f}s", tag="ASR")
                    _maybe_reload_gigaam()

                    if not text:
                        log("asr: речи нет — ложный wake, игнор", tag="ASR")
                        continue

                    command = _after_wake(text)
                    if command is None:
                        log("asr: «макс» в тексте нет — ложный wake, игнор", tag="ASR")
                        continue

                    if command:
                        if _is_dup_command(command):
                            log(f"asr: дубль команды «{command}» — игнор", tag="ASR")
                            continue
                        log(f"asr: [МАКС] + команда «{command}»", tag="ASR")
                        ipc.put(protocol.BRAIN_INBOX,
                                protocol.command(command, source=CommandSource.VOICE))
                    else:
                        play_beep()
                        log("asr: [МАКС] (бип, ждём команду)", tag="ASR")
                        ipc.put(protocol.BRAIN_INBOX, protocol.event("wake", source="asr"))
                        _start_wait(ring.total)
                except Exception as e:
                    log(f"asr: gigaam_worker wake fatal: {e}", tag="ASR", level="ERROR")
                finally:
                    _set_wake_active(False)
                    with _trig_lock:
                        _rearm_until = time.time() + WAKE_REARM_SEC
                        _gigaam_busy = False
                continue

            if (_have_model() and _dialog_is_active() and not _is_waiting()
                    and not _wake_triggered.is_set() and not _gigaam_busy):
                try:
                    dialog_start = _dialog_start_pos_get()
                    sg = _seg_find(lambda x: x[0] >= dialog_start - int(0.2 * sr))
                    if sg:
                        _seg_consume(sg[1])
                        _dialog_advance(sg[1])
                        audio = _seg_audio(sg)
                        text = _recognize(audio)
                        if text:
                            log(f"asr: [GIGAAM/DIALOG] «{text}»", tag="ASR")
                            if not _is_echo(text):
                                stripped = _after_wake(text)
                                cmd = stripped if stripped is not None else text
                                if cmd and not _is_dup_command(cmd):
                                    _open_dialog_window(ring.total)
                                    ipc.put(protocol.BRAIN_INBOX,
                                            protocol.command(cmd, source=CommandSource.VOICE))
                                    _stat_inc("dialog_commands")
                        else:
                            log("asr: [GIGAAM/DIALOG] пусто", tag="ASR", level="DEBUG")
                        _maybe_reload_gigaam()
                except Exception as e:
                    log(f"asr: gigaam_worker dialog fatal: {e}", tag="ASR", level="ERROR")
                continue

    t = threading.Thread(target=gigaam_worker, daemon=True, name="asr-gigaam")
    t.start()
    threads.append(t)

    def viola_worker():
        if wake_detector is None:
            return
        while not _shutdown.is_set():
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
        t = threading.Thread(target=viola_worker, daemon=True, name="asr-viola")
        t.start()
        threads.append(t)

    _was_speaking = False
    _mic_on_logged = False
    _last_hw_reload = time.time()

    try:
        while not _shutdown.is_set():
            now = time.time()

            if _tts_speaking():
                if not _was_speaking:
                    _was_speaking = True
                    _mic_on_logged = False
                    log("asr: микрофон ВЫКЛ (TTS идёт)", tag="ASR")
                    _open_dialog_window(ring.total)
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
                        grammar = _grammar_json(_get_hot_words())
                        rec_hot = KaldiRecognizer(model, cfg.VOSK_RATE, grammar)
                    except Exception as e:
                        log(f"asr: сброс Vosk: {e}", tag="ASR", level="WARN")
                _open_dialog_window(ring.total)

            mute = POST_TTS_MUTE_WAKE_SEC if _is_waiting() else POST_TTS_MUTE_SEC
            if (now - _tts_stopped_at) < mute:
                _seg_clear()
                time.sleep(0.05)
                continue

            if not _mic_on_logged:
                _mic_on_logged = True
                log(f"asr: микрофон ВКЛ через {mute:.1f}s", tag="ASR")

            if capture is None:
                time.sleep(0.1)
                continue

            # WAIT эксклюзивен
            if _is_waiting():
                if not _have_model():
                    log("asr: [WAIT] GigaAM недоступна — ждём таймаут", tag="ASR", level="WARN")
                    time.sleep(0.1)
                    continue

                wp = _wait_pos_get()
                back = int(0.3 * sr)
                sg = _seg_find(lambda x: x[0] >= wp - back)
                if sg:
                    _seg_consume(sg[1])
                    _dialog_advance(sg[1])
                    audio = _seg_audio(sg)
                    gtext = _recognize(audio)
                    if gtext:
                        log(f"asr: [GIGAAM/WAIT] «{gtext}»", tag="ASR")
                        tl = gtext.lower().strip()
                        if tl in _YES_NO_WORDS or tl in _CONTROL_WORDS:
                            _finish_wait()
                            log(f"asr: [МАКС/WAIT] hot «{gtext}»", tag="ASR")
                            ipc.put(protocol.BRAIN_INBOX,
                                    protocol.command(gtext, source=CommandSource.VOICE_HOT))
                            _stat_inc("wait_commands")
                        else:
                            _finish_wait()
                            if _is_dup_command(gtext):
                                log(f"asr: [WAIT] дубль команды «{gtext}» — игнор", tag="ASR")
                            else:
                                log(f"asr: [МАКС/WAIT] команда «{gtext}»", tag="ASR")
                                ipc.put(protocol.BRAIN_INBOX,
                                        protocol.command(gtext, source=CommandSource.VOICE))
                                _stat_inc("wait_commands")
                    _maybe_reload_gigaam()
                else:
                    if ring.total - wp > int(WAIT_EARLY_SILENCE_SEC * sr):
                        _stat_inc("wait_timeouts")
                        log("asr: [WAIT] 2с тишины — речи не было", tag="ASR")
                        _finish_wait()
                        try:
                            ipc.put(protocol.BRAIN_INBOX,
                                    protocol.event("wake_wait", source="asr"))
                        except Exception as e:
                            log(f"asr: wake_wait ipc error: {e}", tag="ASR", level="WARN")
                continue

            try:
                data_item = vosk_q.get(timeout=0.1)
            except queue.Empty:
                continue
            data_np, data_end_pos = data_item

            if now - _last_hw_reload > HOT_WORDS_RELOAD_SEC:
                hot_words = _get_hot_words()
                command_words = _get_command_words()
                with _hot_lock:
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

            if rec_hot.AcceptWaveform(data_np.tobytes()):
                text = ""
                try:
                    res = json.loads(rec_hot.Result())
                    text = res.get("text", "").strip()
                except Exception as e:
                    log(f"asr: [VOSK] JSON parse error: {e}", tag="ASR", level="WARN")
                    try:
                        rec_hot.Reset()
                    except Exception:
                        grammar = _grammar_json(_get_hot_words())
                        rec_hot = KaldiRecognizer(model, cfg.VOSK_RATE, grammar)
                    continue

                if text:
                    tl = text.lower()

                    if _is_echo(text):
                        continue

                    # Vosk ловит «макс» → wake
                    if "макс" in tl.split():
                        if _trigger_wake("vosk", data_end_pos):
                            log(f"asr: [VOSK] «макс» в «{text}»", tag="ASR")
                        continue

                    # Hot-words
                    if _is_command_word(tl):
                        log(f"asr: [VOSK] «{text}»", tag="ASR")
                        if not _hot_word_appropriate(tl):
                            log(f"asr: игнор hot word '{text}' (не в контексте)", tag="ASR")
                            continue
                        log(f"asr: [HOT] «{text}»", tag="ASR")
                        _stat_inc("hot_words")
                        _dialog_advance(data_end_pos)
                        ipc.put(protocol.BRAIN_INBOX,
                                protocol.command(text, source=CommandSource.VOICE_HOT))
                        continue

                    log(f"asr: [VOSK] вне ролей: «{text}»",
                        tag="ASR", level="DEBUG")
            else:
                try:
                    partial = json.loads(rec_hot.PartialResult()).get("partial", "").strip()
                    if partial and VAD_DEBUG:
                        log(f"asr: [VOSK-PARTIAL] «{partial}»", tag="ASR", level="DEBUG")
                except Exception:
                    pass

    except Exception as e:
        log(f"asr: main loop fatal: {e}", tag="ASR", level="ERROR")
    finally:
        log("asr: завершаю фоновые потоки...", tag="ASR")
        _shutdown.set()
        _silence_stop.set()
        _wake_triggered.set()

        if capture is not None:
            try:
                capture.stop()
            except Exception:
                pass

        deadline = time.time() + 3.0
        for t in threads:
            remaining = max(0.0, deadline - time.time())
            if t.is_alive():
                t.join(timeout=remaining)
            if t.is_alive():
                log(f"asr: поток {t.name} не завершился за отведённое время",
                    tag="ASR", level="WARN")

        log(f"asr: финальная статистика {_stats_snapshot()}", tag="ASR")
        log("asr: стоп", tag="ASR")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("asr: остановка по Ctrl+C", tag="ASR")
        sys.exit(0)