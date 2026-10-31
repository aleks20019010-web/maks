"""
workers/asr/main.py — ASR worker: Vosk + микрофон + hot words + бип на wake.

A2 (день 29): Vosk-wake вернулся (ViolaWake убран).
  - Vosk кормится ВСЕГДА (как до C10). CPU жрётся на фон, но распознаёт
    лучше, чем flush буфера.
  - _find_wake_word ищет «макс» в тексте (по границам слов).
  - Нашёл «макс»:
    * after есть («макс включи киша» → after = «включи киша») —
      команда сразу, БИП НЕ ИГРАЕТ (TTS ответит).
    * after пусто («макс») — бип, wake в brain, _waiting_for_command,
      ждём команду.
  - ViolaWake убран (нет _wake_worker, pre_wake_buffer, wake_q).
  - Hot words с контекстом: «да»/«нет» при музыке — не музыкальная команда,
    а ответ на pending. Проверяем expecting_input.flag.
  - Router-страховка: YES/NO без pending → игнор.

FIX: echo-фильтр не считает echo, если распознанное — hot word.
FIX: порог echo поднят с 0.6 до 0.85.

DUCKING (день 29, вечер): приглушение музыки привязано к микрофону.
  - Микрофон ВЫКЛ (TTS идёт) → ipc.music_control("duck_on").
  - Микрофон ВКЛ (реально слушает, после mute-паузы) → duck_off.
  Один источник правды: пока TTS говорит, микрофон выключен — музыка
  приглушена. TTS worker больше НЕ шлёт duck_on/duck_off.
  - Флаг _duck_sent защищает от дублей.
  - При старте сбрасываем залипший duck.

FIX (день 29, вечер): duck_off отправляется ПОСЛЕ mute-паузы, а не в
  момент speaking=False. Раньше: duck_off уходил мгновенно после Ended,
  музыка возвращалась на полную громкость, а микрофон ещё 1 сек
  не слушал (mute-пауза) — звук «возвращался сразу после TTS».
  Теперь: duck_off в момент, когда микрофон реально начинает слушать
  (после POST_TTS_MUTE_SEC). Музыка возвращается через 1 сек после TTS.
"""

import json
import queue
import re
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
from shared.sources import CommandSource
from shared.beep import play_beep


POST_TTS_MUTE_SEC = 1.0
POST_TTS_MUTE_WAKE_SEC = 0.7
HOT_WORD_COOLDOWN_SEC = 1.0
ECHO_SIMILARITY_THRESHOLD = 0.85
DEDUP_WINDOW_SEC = 1.0
HOT_WORDS_RELOAD_SEC = 30.0
DIALOG_SESSION_SEC = 20.0

_COMMANDS_FILE = "commands.json"


# ── Внутренние состояния ────────────────────────────────────────
_waiting_for_command = False
_last_wake_time = 0.0
_last_hot_word_at = 0.0

# Ducking музыки через микрофон. _duck_sent=True → duck_on отправлен,
# duck_off ещё не отправлен. Сбрасывается в _duck_off().
_duck_sent = False


# ── Флаги ──────────────────────────────────────────────────────
def _flag_exists(name: str) -> bool:
    try:
        return (cfg.DATA_DIR / name).exists()
    except Exception:
        return False


def _is_music_playing() -> bool:
    return _flag_exists("music_playing.flag")


def _is_expecting_input() -> bool:
    return _flag_exists("expecting_input.flag")


# ── Ducking (микрофон ⇄ музыка) ───────────────────────────────
def _duck_on():
    """Микрофон ВЫКЛ — приглушить музыку. Идемпотентно."""
    global _duck_sent
    if _duck_sent:
        return
    _duck_sent = True
    try:
        ipc.put(protocol.MUSIC_INBOX, protocol.music_control("duck_on"))
    except Exception as e:
        _duck_sent = False  # чтобы следующий _duck_on попробовал снова
        log(f"asr: duck_on error: {e}", tag="ASR", level="WARN")


def _duck_off():
    """Микрофон ВКЛ (реально слушает) — восстановить громкость."""
    global _duck_sent
    if not _duck_sent:
        return
    _duck_sent = False
    try:
        ipc.put(protocol.MUSIC_INBOX, protocol.music_control("duck_off"))
    except Exception as e:
        log(f"asr: duck_off error: {e}", tag="ASR", level="WARN")


# ── Hot words ──────────────────────────────────────────────────
def _get_hot_words() -> set:
    try:
        from brain.skills.controls import ControlsSkill
        return set(ControlsSkill.HOT_WORDS)
    except Exception:
        return set()


# ── Commands (для фильтра диалога) ─────────────────────────────
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


def _is_command_word(text: str) -> bool:
    hot = _get_hot_words()
    if not hot:
        return False
    tl = text.lower().strip()
    if not tl:
        return False
    if tl in hot:
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
        flag = cfg.DATA_DIR / "session_open.flag"
        if not flag.exists():
            return False
        ts = float(flag.read_text().strip())
        return (time.time() - ts) < DIALOG_SESSION_SEC
    except Exception:
        return False


# ── Фильтр диалога ─────────────────────────────────────────────
_QUESTION_WORDS = (
    "что", "кто", "где", "когда", "почему", "как", "зачем",
    "сколько", "какой", "какая", "какие", "куда", "откуда", "чей",
)

_DETAIL_WORDS = (
    "подробнее", "подробней", "поподробнее",
    "расскажи", "рассказывай",
    "объясни", "объясняй",
    "поясни", "разъясни",
    "в деталях", "детально",
    "продолжай",
)

_SHORT_OK = (
    "спасибо", "благодарю", "привет", "здравствуй", "пока",
    "да", "нет", "конечно", "хорошо", "ладно", "ясно",
    "понял", "поняла", "ок", "окей", "ага", "угу",
    "молодец", "умница", "правильно", "верно",
    "стоп", "хватит", "достаточно", "замолчи",
    "расскажи", "объясни", "подробнее", "продолжай",
)


def _is_meaningful_dialog(text_lower: str, command_words: set) -> bool:
    if not text_lower:
        return False
    if text_lower.strip() in _SHORT_OK:
        return True
    if command_words:
        for w in command_words:
            if w in text_lower:
                return True
    if any(w in text_lower for w in _QUESTION_WORDS):
        return True
    if any(w in text_lower for w in _DETAIL_WORDS):
        return True
    if len(text_lower.split()) >= 3:
        return True
    return False


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
        trimmed = audio[:new_len * r]
        reshaped = trimmed.reshape(new_len, r)
        return reshaped.mean(axis=1).astype(audio.dtype)
    new_len = int(len(audio) * to_rate / from_rate)
    if new_len <= 0:
        return np.zeros(0, dtype=audio.dtype)
    old_idx = np.arange(len(audio))
    new_idx = np.linspace(0, len(audio) - 1, new_len)
    return np.interp(new_idx, old_idx, audio).astype(audio.dtype)


# ── Wake word (Vosk, A2) ──────────────────────────────────────
def _find_wake_word(text: str):
    """Найти wake word по ГРАНИЦАМ СЛОВ. «макароны» ≠ «макс».

    Возвращает (wake_word, after). after — команда после «макс»
    (может быть пустой). Если не найдено — (None, None).
    """
    t_lower = text.lower()
    for w in cfg.WAKE_WORDS:
        pattern = r'(?<![а-яёa-z])' + re.escape(w) + r'(?![а-яёa-z])'
        m = re.search(pattern, t_lower)
        if m:
            after = text[m.end():].strip()
            after = after.strip(' ,.!?-')
            return w, after
    return None, None


# ── Hot words с контекстом (A2) ───────────────────────────────
def _hot_word_appropriate(text_lower: str) -> bool:
    yes_no = {
        "да", "нет", "ок", "окей", "подтверждаю",
        "не надо", "неважно", "не важно", "не хочу", "отмена",
    }
    if text_lower in yes_no:
        return _is_expecting_input()

    control_words = {
        "дальше", "next", "следующий", "пропусти", "пропустить",
        "назад", "предыдущий", "предыдущая", "вернись", "верни",
        "пауза", "продолжить", "продолжи", "играть", "играй", "плей",
        "стоп", "останови", "остановись", "стоп музыка",
        "громче", "тише", "погромче", "потише",
    }
    if text_lower in control_words:
        return True

    if text_lower.startswith("громкость "):
        return True

    if text_lower in ("что играет", "что за песня", "что за трек", "что сейчас"):
        return False

    return True


# ── Главный ────────────────────────────────────────────────────
def main():
    global _waiting_for_command, _last_hot_word_at

    log("asr: старт", tag="ASR")
    ipc.init()

    # На старте: если TTS упал в прошлом запуске, duck_off мог не уйти —
    # музыка осталась приглушённой. Сбрасываем.
    try:
        ipc.put(protocol.MUSIC_INBOX, protocol.music_control("duck_off"))
        log("asr: старт — сбросил возможный залипший duck", tag="ASR")
    except Exception as e:
        log(f"asr: старт duck_off error: {e}", tag="ASR", level="WARN")

    log(f"asr: загружаю Vosk: {cfg.VOSK_MODEL_PATH}", tag="ASR")
    try:
        model = Model(str(cfg.VOSK_MODEL_PATH))
    except Exception as e:
        log(f"asr: Vosk не загрузился: {e}", tag="ASR", level="ERROR")
        sys.exit(1)

    rec = KaldiRecognizer(model, cfg.VOSK_RATE)
    mic_channels = 1

    hot_words = _get_hot_words()
    log(f"asr: HOT WORDS: {len(hot_words)}", tag="ASR")

    command_words = _get_command_words()
    log(f"asr: COMMAND WORDS: {len(command_words)}", tag="ASR")

    near_q = queue.Queue(maxsize=50)
    clean_q = queue.Queue(maxsize=50)

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
    _mic_on_logged = False   # чтобы не спамить логом "ВКЛ" после каждого тика

    _last_sent_text = ""
    _last_sent_time = 0.0
    _last_hw_reload = time.time()
    _last_cmd_reload = time.time()

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

            if now - _last_cmd_reload > HOT_WORDS_RELOAD_SEC:
                new_cmd = _get_command_words()
                if new_cmd != command_words:
                    command_words = new_cmd
                    log(f"asr: COMMAND WORDS перезагружены: {len(command_words)}", tag="ASR")
                _last_cmd_reload = now

            speaking = _tts_speaking()
            if speaking:
                if not _was_speaking:
                    _was_speaking = True
                    _mic_on_logged = False
                    log("asr: микрофон ВЫКЛ (TTS идёт)", tag="ASR")
                    _duck_on()
                continue

            if _was_speaking:
                _was_speaking = False
                _speaking_stopped_at = now
                # duck_off НЕ здесь — ниже, после mute-паузы.
                try:
                    rec = KaldiRecognizer(model, cfg.VOSK_RATE)
                except Exception as e:
                    log(f"asr: ошибка пересоздания rec: {e}", tag="ASR", level="WARN")

            mute = POST_TTS_MUTE_WAKE_SEC if _waiting_for_command else POST_TTS_MUTE_SEC
            if (now - _speaking_stopped_at) < mute:
                continue

            # Mute-пауза прошла — микрофон реально слушает.
            # Снимаем ducking именно здесь, а не в момент speaking=False.
            # _duck_sent защищает от повторов после первого вызова.
            if not _mic_on_logged:
                _mic_on_logged = True
                log(f"asr: микрофон ВКЛ через {mute:.1f}s", tag="ASR")
            _duck_off()

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
                continue

            if text_lower == _last_sent_text and (now - _last_sent_time) < DEDUP_WINDOW_SEC:
                continue
            _last_sent_text = text_lower
            _last_sent_time = now

            # Vosk-wake: ищем «макс» в тексте
            wake, after = _find_wake_word(text)
            if wake:
                if not _check_wake_cooldown():
                    log("asr: wake cooldown — пропуск", tag="ASR")
                    if after:
                        ipc.put(protocol.BRAIN_INBOX,
                                protocol.command(after, source=CommandSource.VOICE))
                    continue
                if after:
                    # Слитно «макс включи киша» — команда уже есть, бип НЕ играет.
                    log(f"asr: [{wake.upper()}] + команда «{after}» (без бипа)", tag="ASR")
                    ipc.put(protocol.BRAIN_INBOX,
                            protocol.command(after, source=CommandSource.VOICE))
                else:
                    # Только «макс» — бип, wake, ждём команду.
                    play_beep()
                    log(f"asr: [{wake.upper()}] (бип, ждём команду)", tag="ASR")
                    _waiting_for_command = True
                    ipc.put(protocol.BRAIN_INBOX,
                            protocol.event("wake", source="asr"))
                    threading.Thread(target=_wait_for_command_timeout, daemon=True).start()
                continue

            if _waiting_for_command:
                _waiting_for_command = False
                log(f"asr: команда «{text}»", tag="ASR")
                ipc.put(protocol.BRAIN_INBOX,
                        protocol.command(text, source=CommandSource.VOICE))
                continue

            if _session_open():
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
                if not _is_meaningful_dialog(text_lower, command_words):
                    continue
                ipc.put(protocol.BRAIN_INBOX,
                        protocol.command(text, source=CommandSource.VOICE_DIALOG))
                continue

            if _is_music_playing():
                if text_lower in hot_words and _hot_word_appropriate(text_lower):
                    if (now - _last_hot_word_at) < HOT_WORD_COOLDOWN_SEC:
                        continue
                    _last_hot_word_at = now
                    ipc.put(protocol.BRAIN_INBOX,
                            protocol.command(text, source=CommandSource.VOICE_HOT))
                    continue
                continue

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