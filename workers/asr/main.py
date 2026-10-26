"""
workers/asr/main.py — ASR worker: Vosk + микрофон + wake word + hot words + AEC.

Поток (AEC_ENABLED = True):
  echoff (сам захватывает mic + loopback, выравнивает, вычитает эхо)
    → clean_q (16 кГц) → Vosk → текст

Поток (AEC_ENABLED = False):
  микрофон → near_q → Vosk → текст

  Vosk → echo-фильтр → дедуп → wake/hot word/команда → brain_inbox

FIX: echo-фильтр не считает echo, если распознанное — hot word.
FIX: порог echo поднят с 0.6 до 0.85.

ОТКАТ: динамическая грамматика (IDLE/FULL) убрана.
Причина: Vosk SetGrammar на работающем распознавателе нестабилен
('Can't add speaker model to already running recognizer',
 'NoneType' object has no attribute 'encode',
 'Expecting array of strings, got: []').
Один KaldiRecognizer(model, rate) без грамматики — полный диапазон.
ASR фильтрует фоновый разговор через _session_open() и wake/hot.
"""

import json
import queue
import sys
import threading
import time

import numpy as np
import sounddevice as sd
from vosk import Model, KaldiRecognizer

from shared import ipc
from shared import protocol
from shared import config as cfg
from shared.log import log


POST_TTS_MUTE_SEC = 1.0
POST_TTS_MUTE_WAKE_SEC = 0.7
HOT_WORD_COOLDOWN_SEC = 1.0
ECHO_SIMILARITY_THRESHOLD = 0.85
DEDUP_WINDOW_SEC = 1.0
HOT_WORDS_RELOAD_SEC = 30.0
DIALOG_SESSION_SEC = 20.0


# ── Внутренние состояния ────────────────────────────────────────
_waiting_for_command = False
_last_wake_time = 0.0
_last_hot_word_at = 0.0


# ── Hot words ──────────────────────────────────────────────────
def _get_hot_words() -> set:
    try:
        from brain.skills.controls import ControlsSkill
        return set(ControlsSkill.HOT_WORDS)
    except Exception:
        return set()


def _get_wake_words() -> set:
    try:
        return set(w.lower() for w in cfg.WAKE_WORDS)
    except Exception:
        return set()


def _is_command_word(text: str) -> bool:
    hot = _get_hot_words()
    wake = _get_wake_words()
    if not hot and not wake:
        return False
    tl = text.lower().strip()
    if not tl:
        return False
    if tl in hot:
        return True
    for w in wake:
        if tl.startswith(w):
            rest = tl[len(w):].strip(" ,.!?-")
            if rest in hot:
                return True
            if rest and len(rest.split()) >= 1:
                return True
    return False


# ── Echo-фильтр ────────────────────────────────────────────────
def _is_echo(text: str) -> bool:
    if _is_command_word(text):
        return False
    try:
        from workers.tts.state_file import get_state
        state = get_state()
        last = state.get("last_text", "") or ""
    except Exception:
        return False
    if not last or not text:
        return False
    import difflib
    last_norm = last.lower().strip()
    text_norm = text.lower().strip()
    if not last_norm or not text_norm:
        return False
    if len(text_norm.split()) <= 2 and len(last_norm.split()) <= 2:
        return False
    ratio = difflib.SequenceMatcher(None, last_norm, text_norm).ratio()
    if ratio >= ECHO_SIMILARITY_THRESHOLD:
        log(f"asr: echo '{text}' ~ '{last}' ({ratio:.2f})", tag="ASR")
        return True
    return False


# ── Диалоговый режим ───────────────────────────────────────────
def _session_open() -> bool:
    try:
        import pathlib
        flag = pathlib.Path("data/session_open.flag")
        if not flag.exists():
            return False
        ts = float(flag.read_text().strip())
        return (time.time() - ts) < DIALOG_SESSION_SEC
    except Exception:
        return False


# ── Wake word ──────────────────────────────────────────────────
def _find_wake_word(text: str):
    t_lower = text.lower()
    for w in cfg.WAKE_WORDS:
        idx = t_lower.find(w)
        if idx != -1:
            after = text[idx + len(w):].strip()
            after = after.strip(' ,.!?-')
            return w, after
    return None, None


def _check_wake_cooldown() -> bool:
    global _last_wake_time
    now = time.time()
    if now - _last_wake_time < cfg.WAKE_COOLDOWN_SEC:
        return False
    _last_wake_time = now
    return True


def _wait_for_command_timeout():
    global _waiting_for_command
    time.sleep(cfg.COMMAND_TIMEOUT_SEC)
    if _waiting_for_command:
        _waiting_for_command = False
        log("asr: команда не поступила — WAKE_WAIT", tag="ASR")
        ipc.put(protocol.BRAIN_INBOX, protocol.event("wake_wait", source="asr"))


# ── TTS state polling ──────────────────────────────────────────
def _tts_speaking() -> bool:
    try:
        from workers.tts.state_file import get_state
        return bool(get_state().get("speaking", False))
    except Exception:
        return False


# ── Ресемплинг ─────────────────────────────────────────────────
def _resample(audio: np.ndarray, from_rate: int, to_rate: int) -> np.ndarray:
    if from_rate == to_rate:
        return audio
    ratio = from_rate / to_rate
    if ratio == int(ratio) and ratio >= 1:
        r = int(ratio)
        new_len = len(audio) // r
        if new_len <= 0:
            return np.zeros(0, dtype=audio.dtype)
        trimmed = audio[:new_len * r]
        reshaped = trimmed.reshape(new_len, r)
        return reshaped.mean(axis=1).astype(audio.dtype)
    new_len = int(len(audio) * to_rate / from_rate)
    if new_len <= 0:
        return np.zeros(0, dtype=audio.dtype)
    old_idx = np.arange(len(audio))
    new_idx = np.linspace(0, len(audio) - 1, new_len)
    return np.interp(new_idx, old_idx, audio).astype(audio.dtype)


# ── Главный ────────────────────────────────────────────────────
def main():
    global _waiting_for_command, _last_hot_word_at

    log("asr: старт", tag="ASR")
    ipc.init()

    log(f"asr: загружаю Vosk: {cfg.VOSK_MODEL_PATH}", tag="ASR")
    try:
        model = Model(cfg.VOSK_MODEL_PATH)
    except Exception as e:
        log(f"asr: Vosk не загрузился: {e}", tag="ASR", level="ERROR")
        sys.exit(1)

    # Один распознаватель, полный диапазон, без грамматики
    rec = KaldiRecognizer(model, cfg.VOSK_RATE)
    mic_channels = 1

    hot_words = _get_hot_words()
    log(f"asr: HOT WORDS: {len(hot_words)}", tag="ASR")

    # ── Очереди ──
    near_q = queue.Queue(maxsize=50)
    clean_q = queue.Queue(maxsize=50)

    # ── AEC через echoff ──
    echoff_worker = None
    use_echoff = False
    if cfg.AEC_ENABLED:
        try:
            from workers.asr.echoff_worker import EchoffWorker
            echoff_worker = EchoffWorker(clean_q)
            echoff_worker.start()
            use_echoff = True
            log("asr: AEC включён (echoff + WebRTC AEC3)", tag="ASR")
        except Exception as e:
            log(f"asr: echoff не запустился: {e}", tag="ASR", level="ERROR")
            echoff_worker = None
            use_echoff = False

    input_q = clean_q if use_echoff else near_q

    _was_speaking = False
    _speaking_stopped_at = 0.0

    _last_sent_text = ""
    _last_sent_time = 0.0
    _last_hw_reload = time.time()

    def callback(indata, frames, time_info, status):
        audio = np.frombuffer(bytes(indata), dtype=np.int16)
        if mic_channels > 1:
            audio = audio.reshape(-1, mic_channels).mean(axis=1).astype(np.int16)
        audio_16k = _resample(audio, cfg.MIC_CAPTURE_RATE, cfg.VOSK_RATE)
        if len(audio_16k) > 0:
            try:
                near_q.put_nowait(audio_16k)
            except queue.Full:
                pass

    mic_stream = None
    if not use_echoff:
        log(f"asr: слушаю микрофон @ {cfg.MIC_CAPTURE_RATE} Гц (AEC выкл)", tag="ASR")
        mic_stream = sd.RawInputStream(
            samplerate=cfg.MIC_CAPTURE_RATE,
            blocksize=int(cfg.MIC_CAPTURE_RATE * 0.1),
            device=cfg.MIC_DEVICE_INDEX,
            channels=mic_channels,
            dtype='int16',
            callback=callback,
        )
    else:
        log("asr: микрофон захватывает echoff (sd.RawInputStream не открыт)", tag="ASR")

    try:
        if mic_stream is not None:
            mic_stream.start()

        while True:
            try:
                data_np = input_q.get(timeout=0.5)
            except queue.Empty:
                continue

            data = data_np.tobytes()
            now = time.time()

            if now - _last_hw_reload > HOT_WORDS_RELOAD_SEC:
                new_hw = _get_hot_words()
                if new_hw != hot_words:
                    hot_words = new_hw
                    log(f"asr: HOT WORDS перезагружены: {len(hot_words)}", tag="ASR")
                _last_hw_reload = now

            speaking = _tts_speaking()

            if speaking:
                if not _was_speaking:
                    _was_speaking = True
                    log("asr: микрофон ВЫКЛ (TTS идёт)", tag="ASR")
                continue

            if _was_speaking:
                _was_speaking = False
                _speaking_stopped_at = now
                try:
                    rec = KaldiRecognizer(model, cfg.VOSK_RATE)
                except Exception as e:
                    log(f"asr: ошибка пересоздания rec: {e}", tag="ASR", level="WARN")
                mute = POST_TTS_MUTE_WAKE_SEC if _waiting_for_command else POST_TTS_MUTE_SEC
                log(f"asr: микрофон ВКЛ через {mute:.1f}s", tag="ASR")

            mute = POST_TTS_MUTE_WAKE_SEC if _waiting_for_command else POST_TTS_MUTE_SEC
            if (now - _speaking_stopped_at) < mute:
                continue

            if not rec.AcceptWaveform(data):
                continue

            if _tts_speaking():
                rec.Result()
                continue

            result = json.loads(rec.Result())
            text = result.get('text', '').strip()
            if not text:
                continue

            text_lower = text.lower().strip()

            if _is_echo(text):
                log(f"asr: [v] {text} (echo)", tag="ASR")
                continue

            if text_lower == _last_sent_text and (now - _last_sent_time) < DEDUP_WINDOW_SEC:
                log(f"asr: дубль «{text}» — игнор", tag="ASR")
                continue
            _last_sent_text = text_lower
            _last_sent_time = now

            if _waiting_for_command:
                _waiting_for_command = False
                log(f"asr: команда «{text}»", tag="ASR")
                ipc.put(protocol.BRAIN_INBOX, protocol.command(text, source="voice"))
                continue

            # Диалоговый режим (флаг brain свежий) — пропускаем без wake
            if _session_open():
                if text_lower in hot_words:
                    if (now - _last_hot_word_at) < HOT_WORD_COOLDOWN_SEC:
                        log(f"asr: [HOT] {text} — cooldown", tag="ASR")
                        continue
                    _last_hot_word_at = now
                    log(f"asr: [HOT] {text} (диалог)", tag="ASR")
                    ipc.put(protocol.BRAIN_INBOX, protocol.command(text, source="voice_hot"))
                    continue
                if text_lower in ("хватит", "достаточно", "стоп", "замолчи"):
                    log(f"asr: [ДИАЛОГ] {text} → idle", tag="ASR")
                    ipc.put(protocol.BRAIN_INBOX, protocol.command(text, source="voice_dialog"))
                    continue
                log(f"asr: [ДИАЛОГ] {text}", tag="ASR")
                ipc.put(protocol.BRAIN_INBOX, protocol.command(text, source="voice_dialog"))
                continue

            if text_lower in hot_words:
                if (now - _last_hot_word_at) < HOT_WORD_COOLDOWN_SEC:
                    log(f"asr: [HOT] {text} — cooldown", tag="ASR")
                    continue
                _last_hot_word_at = now
                log(f"asr: [HOT] {text} (без wake)", tag="ASR")
                ipc.put(protocol.BRAIN_INBOX, protocol.command(text, source="voice_hot"))
                continue

            wake, after = _find_wake_word(text)
            if wake:
                if not _check_wake_cooldown():
                    log("asr: wake слишком часто", tag="ASR")
                    ipc.put(protocol.BRAIN_INBOX, protocol.event("wake_repeat", source="asr"))
                    if after:
                        ipc.put(protocol.BRAIN_INBOX, protocol.command(after, source="voice"))
                    continue

                log(f"asr: [{wake.upper()}]", tag="ASR")

                if after:
                    log(f"asr: команда «{after}»", tag="ASR")
                    ipc.put(protocol.BRAIN_INBOX, protocol.command(after, source="voice"))
                else:
                    _waiting_for_command = True
                    ipc.put(protocol.BRAIN_INBOX, protocol.event("wake", source="asr"))
                    threading.Thread(target=_wait_for_command_timeout, daemon=True).start()
                continue

            log(f"asr: [v] {text}", tag="ASR")

    except Exception as e:
        log(f"asr: ошибка микрофона: {e}", tag="ASR", level="ERROR")
    finally:
        if mic_stream is not None:
            try:
                mic_stream.stop()
                mic_stream.close()
            except Exception:
                pass
        if echoff_worker:
            try:
                echoff_worker.stop()
            except Exception:
                pass


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("asr: остановка по Ctrl+C", tag="ASR")
        sys.exit(0)