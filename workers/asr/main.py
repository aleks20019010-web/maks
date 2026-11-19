# workers/asr/main.py
# ASR worker: AEC-захват, Vosk-триггер, GigaAM-верификация и режим ожидания команды.
#
# ── AEC OFFLOAD (2026-11-19) ──────────────────────────────────
#   on_aec_frame стал максимально лёгким: только копирует кадр в raw_q
#   (48 кГц float32). Никаких ресемплов, блокировок, дискового I/O в коллбэке.
#   Ресемпл, ring.push, seg_store.feed_audio, vosk_q/viola_q — в главном цикле
#   и в viola_worker.
#
# ── AEC WATCHDOG (2026-11-19) ─────────────────────────────────
#   Если echoff перестал отдавать кадры (>3.5с) — перезапускаем AEC.
#   Защита от suspended/зависания драйвера.
#
# ── ducking.is_speaking (2026-11-19) ──────────────────────────
#   В главном цикле читаем ducking.is_speaking (кэш в памяти), а не
#   ducking.tts_speaking() (диск). Обновляется в run_worker каждые 50 мс.
#
# ── MIC TELEMETRY (2026-11-19) ────────────────────────────────
#   RMS микрофона (16 кГц float32 после AEC) → ui_bridge.update_avatar_telemetry
#   не чаще 20 раз в секунду (раз в 50 мс). UI-аватар читает mic_level
#   и визуально реагирует на громкость голоса пользователя.
#   Нормализация: разговорный RMS 0.004..0.094 → 0..1.

import io
import json
import os
import queue
import signal
import sys
import threading
import time

import numpy as np
from vosk import Model, KaldiRecognizer

try:
    from violawake_sdk import WakeDetector
    VIOLA_AVAILABLE = True
except ImportError:
    VIOLA_AVAILABLE = False
    WakeDetector = None

try:
    from echoff import AecCapture, AecConfig
    ECHOFF_AVAILABLE = True
except ImportError:
    ECHOFF_AVAILABLE = False
    AecCapture = AecConfig = None

from shared import ipc
from shared import protocol
from shared import config as cfg
from shared.log import log
from shared.sources import CommandSource
from shared.ui_bridge import update_avatar_telemetry
from workers.asr.audio import Ring, SegmentStore, VAD_DEBUG, resample, run_silence_worker
from workers.asr.ducking import DuckingController
from workers.asr.recognizer import GigaAMController
from workers.asr.text_utils import (
    CONTROL_WORDS, YES_NO_WORDS, CommandFilter,
    build_grammar_json, build_grammar_words, hot_word_appropriate, load_hot_words,
)


VIOLA_ENABLED = False
POST_TTS_MUTE_SEC = 1.0
POST_TTS_MUTE_WAKE_SEC = 0.7
HOT_WORDS_RELOAD_SEC = 30.0
GIGAAM_MAX_BUFFER_SEC = 10.0
WAIT_EARLY_SILENCE_SEC = 2.0
_STATS_LOG_SEC = 60.0

_shutdown = threading.Event()
_SESSION_ID = int(time.time())


def _flush_queue(q: queue.Queue):
    while True:
        try:
            q.get_nowait()
        except queue.Empty:
            break


def _handle_shutdown(signum, frame):
    log(f"asr: сигнал {signum} получен, завершаю работу...", tag="ASR")
    _shutdown.set()


def main():
    signal.signal(signal.SIGINT, _handle_shutdown)
    signal.signal(signal.SIGTERM, _handle_shutdown)

    log(f"asr: старт (session_id={_SESSION_ID}, viola_enabled={VIOLA_ENABLED})", tag="ASR")
    ipc.init()

    try:
        ipc.put(protocol.MUSIC_INBOX, protocol.music_control("duck_off"))
    except Exception as e:
        log(f"asr: initial duck_off error: {e}", tag="ASR", level="WARN")

    # Статистика
    stats = {
        "vad_segments": 0, "vad_dropped": 0,
        "gigaam_requests": 0, "gigaam_ok": 0, "gigaam_empty": 0, "gigaam_error": 0,
        "wake_vosk": 0, "wake_viola": 0,
        "hot_words": 0, "hot_rejected": 0,
        "wait_commands": 0, "wait_timeouts": 0,
        "dup_commands": 0, "gigaam_reloads": 0,
    }
    stats_lock = threading.Lock()

    def stat_inc(key: str, n: int = 1):
        with stats_lock:
            stats[key] = stats.get(key, 0) + n

    # Состояние WAIT (ожидание команды после бипа)
    wait_state = {"active": False, "pos": 0, "gen": 0}
    wait_lock = threading.Lock()

    def is_waiting() -> bool:
        with wait_lock:
            return wait_state["active"]

    def wait_pos_get() -> int:
        with wait_lock:
            return wait_state["pos"]

    def finish_wait():
        with wait_lock:
            wait_state["active"] = False
            wait_state["gen"] += 1

    def _wait_timeout_worker(gen: int):
        deadline = time.time() + cfg.COMMAND_TIMEOUT_SEC
        while time.time() < deadline:
            if _shutdown.is_set():
                return
            time.sleep(0.1)
        fired = False
        with wait_lock:
            if wait_state["active"] and wait_state["gen"] == gen:
                wait_state["active"] = False
                fired = True
        if fired:
            stat_inc("wait_timeouts")
            log("asr: команда не поступила — WAKE_WAIT", tag="ASR")
            try:
                ipc.put(protocol.BRAIN_INBOX, protocol.event("wake_wait", source="asr"))
            except Exception as e:
                log(f"asr: wake_wait ipc error: {e}", tag="ASR", level="WARN")

    def start_wait(pos: int):
        with wait_lock:
            wait_state["pos"] = pos
            wait_state["gen"] += 1
            gen = wait_state["gen"]
            wait_state["active"] = True
        threading.Thread(
            target=_wait_timeout_worker, args=(gen,), daemon=True, name="asr-wait-timeout"
        ).start()

    # Vosk
    log(f"asr: загружаю Vosk: {cfg.VOSK_MODEL_PATH}", tag="ASR")
    try:
        model = Model(str(cfg.VOSK_MODEL_PATH))
    except Exception as e:
        log(f"asr: Vosk не загрузился: {e}", tag="ASR", level="ERROR")
        sys.exit(1)

    cmd_filter = CommandFilter()
    hot_words = load_hot_words()
    cmd_filter.update_hot_words(hot_words)
    grammar = build_grammar_json(hot_words)
    rec_hot = KaldiRecognizer(model, cfg.VOSK_RATE, grammar)
    log(f"asr: грамматика Vosk: {len(build_grammar_words(hot_words))} слов; HOT WORDS: {len(hot_words)}", tag="ASR")

    # Аудио-буфер, VAD, дакинг и GigaAM
    sr = cfg.VOSK_RATE
    ring = Ring(GIGAAM_MAX_BUFFER_SEC, sr)
    seg_store = SegmentStore(ring, sr)
    ducking = DuckingController()
    gigaam = GigaAMController(
        ring=ring,
        seg_store=seg_store,
        ducking=ducking,
        cmd_filter=cmd_filter,
        stat_inc=stat_inc,
        is_waiting_fn=is_waiting,
        start_wait_fn=start_wait,
    )

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

    # ── AEC OFFLOAD (2026-11-19): коллбэк только копирует кадр в raw_q ──
    raw_q = queue.Queue(maxsize=150)  # сырые кадры 48 кГц из echoff
    queue_full_warn = [0.0]
    last_frame_at = [time.time()]

    def on_aec_frame(frame):
        # Максимально лёгкий коллбэк: никаких блокировок и дискового I/O!
        try:
            last_frame_at[0] = time.time()
            samples = np.asarray(frame.microphone_clean, dtype=np.float32)
            raw_q.put_nowait(samples.copy())
        except queue.Full:
            now = time.time()
            if now - queue_full_warn[0] >= 5.0:
                queue_full_warn[0] = now
                log("asr: raw_q full — drop frame (echoff защищён от backlog)", tag="ASR", level="WARN")
        except Exception:
            pass

    capture_lock = threading.Lock()
    capture_box = {"cap": None}

    def _start_aec() -> bool:
        if not ECHOFF_AVAILABLE:
            return False
        with capture_lock:
            old = capture_box["cap"]
            if old is not None:
                try:
                    old.stop()
                except Exception:
                    pass
                capture_box["cap"] = None
            try:
                aec_config = AecConfig(stream_delay_ms=50)
                cap = AecCapture(aec_config, on_frame=on_aec_frame)
                cap.start()
                capture_box["cap"] = cap
                last_frame_at[0] = time.time()
                log("asr: echoff AEC запущен", tag="ASR")
                return True
            except Exception as e:
                log(f"asr: echoff не запустился: {e}", tag="ASR", level="ERROR")
                return False

    _start_aec()

    # Фоновые потоки
    threads = []
    if ECHOFF_AVAILABLE:
        threads.append(threading.Thread(target=run_silence_worker, args=(_shutdown,), daemon=True, name="asr-silence"))
    threads.append(threading.Thread(target=ducking.run_worker, args=(_shutdown,), daemon=True, name="asr-duck"))
    threads.append(threading.Thread(target=gigaam.run_worker, args=(_shutdown,), daemon=True, name="asr-gigaam"))

    def stats_worker():
        prev = None
        while not _shutdown.wait(_STATS_LOG_SEC):
            with stats_lock:
                snap = dict(stats)
            if snap != prev:
                log(f"asr: stats {snap}", tag="ASR")
                prev = snap

    threads.append(threading.Thread(target=stats_worker, daemon=True, name="asr-stats"))

    # viola_q нужен viola_worker. AEC-коллбэк его больше не наполняет,
    # поэтому заполняем из главного цикла (см. ниже drained_a16).
    viola_q = queue.Queue(maxsize=100)

    if wake_detector is not None:
        def viola_worker():
            while not _shutdown.is_set():
                try:
                    chunk, chunk_end = viola_q.get(timeout=0.1)
                except queue.Empty:
                    continue
                if ducking.is_speaking:
                    continue
                try:
                    if wake_detector.detect(chunk):
                        log("asr: [VIOLAWAKE] услышал «Макс»", tag="ASR")
                        gigaam.trigger_wake("viola", chunk_end)
                except Exception as e:
                    log(f"asr: viola detect: {e}", tag="ASR", level="WARN")

        threads.append(threading.Thread(target=viola_worker, daemon=True, name="asr-viola"))

    # Сторож: если echoff завис/ушёл в suspended (нет кадров > 3.5с) — перезапускаем
    def aec_watchdog():
        while not _shutdown.wait(1.5):
            if not ECHOFF_AVAILABLE:
                continue
            if (time.time() - last_frame_at[0]) > 3.5:
                log("asr: echoff перестал отдавать кадры (>3.5с) — перезапускаю AEC...",
                    tag="ASR", level="WARN")
                _flush_queue(raw_q)
                _start_aec()

    if ECHOFF_AVAILABLE:
        threads.append(threading.Thread(target=aec_watchdog, daemon=True, name="asr-aec-wd"))

    for t in threads:
        t.start()

    # Главный цикл распознавания
    was_speaking = False
    tts_stopped_at = 0.0
    mic_on_logged = False
    last_hw_reload = time.time()

    # ── MIC TELEMETRY (2026-11-19): агрегатор RMS для UI-аватара ──
    last_mic_ui_at = 0.0
    mic_rms_acc = 0.0

    try:
        while not _shutdown.is_set():
            now = time.time()
            speaking_now = ducking.is_speaking

            # Забираем накопившиеся сырые кадры из echoff и раскладываем по буферам
            drained_a16 = []
            while True:
                try:
                    raw_samples = raw_q.get_nowait()
                except queue.Empty:
                    break
                clamped = np.clip(raw_samples, -1.0, 1.0)
                int16 = (clamped * 32767).astype(np.int16)
                a16 = resample(int16, 48000, sr)
                if len(a16) == 0:
                    continue
                f32 = a16.astype(np.float32) / 32768.0
                ring.push(f32)

                # ── MIC TELEMETRY: RMS → mic_level (0..1), не чаще 20 Гц ──
                rms = float(np.sqrt(np.mean(f32 * f32)))
                # Нормализуем разговорный уровень (0.005..0.12 → 0..1)
                norm_mic = min(1.0, max(0.0, (rms - 0.004) / 0.09))
                if norm_mic > mic_rms_acc:
                    mic_rms_acc = norm_mic
                if (now - last_mic_ui_at) >= 0.05:
                    last_mic_ui_at = now
                    update_avatar_telemetry(mic_level=round(mic_rms_acc, 3))
                    mic_rms_acc = 0.0

                if not speaking_now:
                    seg_store.feed_audio(
                        f32,
                        on_segment=lambda sg, dropped: (
                            stat_inc("vad_segments"),
                            stat_inc("vad_dropped") if dropped else None,
                        ),
                    )
                end_pos = ring.total
                drained_a16.append((a16, end_pos))
                if VIOLA_ENABLED:
                    try:
                        viola_q.put_nowait((a16, end_pos))
                    except queue.Full:
                        pass

            if speaking_now:
                if not was_speaking:
                    was_speaking = True
                    mic_on_logged = False
                    log("asr: микрофон ВЫКЛ (TTS идёт)", tag="ASR")
                seg_store.clear()
                time.sleep(0.03)
                continue

            if was_speaking:
                was_speaking = False
                tts_stopped_at = now
                try:
                    rec_hot.Reset()
                except Exception:
                    grammar = build_grammar_json(load_hot_words())
                    rec_hot = KaldiRecognizer(model, sr, grammar)

            mute = POST_TTS_MUTE_WAKE_SEC if is_waiting() else POST_TTS_MUTE_SEC
            if (now - tts_stopped_at) < mute:
                seg_store.clear()
                time.sleep(0.05)
                continue

            if not mic_on_logged:
                mic_on_logged = True
                log(f"asr: микрофон ВКЛ через {mute:.1f}s", tag="ASR")

            if not ECHOFF_AVAILABLE or capture_box["cap"] is None:
                time.sleep(0.1)
                continue

            # Режим ожидания команды после «Макс» + бип
            if is_waiting():
                if not gigaam.have_model():
                    time.sleep(0.1)
                    continue

                wp = wait_pos_get()
                sg = seg_store.find(lambda x: x[0] >= wp - int(0.3 * sr))
                if sg:
                    seg_store.consume(sg[1])
                    audio = seg_store.extract_audio(sg)
                    gtext = gigaam.recognize(audio)
                    if gtext:
                        log(f"asr: [GIGAAM/WAIT] «{gtext}»", tag="ASR")
                        tl = gtext.lower().strip()
                        finish_wait()
                        if tl in YES_NO_WORDS or tl in CONTROL_WORDS:
                            log(f"asr: [МАКС/WAIT] hot «{gtext}»", tag="ASR")
                            ipc.put(protocol.BRAIN_INBOX, protocol.command(gtext, source=CommandSource.VOICE_HOT))
                            stat_inc("wait_commands")
                        elif cmd_filter.is_dup_command(gtext):
                            stat_inc("dup_commands")
                            log(f"asr: [WAIT] дубль команды «{gtext}» — игнор", tag="ASR")
                        else:
                            log(f"asr: [МАКС/WAIT] команда «{gtext}»", tag="ASR")
                            ipc.put(protocol.BRAIN_INBOX, protocol.command(gtext, source=CommandSource.VOICE))
                            stat_inc("wait_commands")
                    gigaam.maybe_reload()
                elif ring.total - wp > int(WAIT_EARLY_SILENCE_SEC * sr):
                    stat_inc("wait_timeouts")
                    log("asr: [WAIT] 2с тишины — речи не было", tag="ASR")
                    finish_wait()
                    try:
                        ipc.put(protocol.BRAIN_INBOX, protocol.event("wake_wait", source="asr"))
                    except Exception as e:
                        log(f"asr: wake_wait ipc error: {e}", tag="ASR", level="WARN")
                # Кадры уже разложены по ring/seg_store в дренажном блоке;
                # WAIT-ветка обрабатывает их через find/extract_audio.
                continue

            # Обычный поток Vosk
            if not drained_a16:
                time.sleep(0.015)
                continue

            if now - last_hw_reload > HOT_WORDS_RELOAD_SEC:
                hw = load_hot_words()
                cmd_filter.update_hot_words(hw)
                last_hw_reload = now
                new_grammar = build_grammar_json(hw)
                if new_grammar != grammar:
                    grammar = new_grammar
                    try:
                        rec_hot = KaldiRecognizer(model, sr, grammar)
                        log(f"asr: грамматика обновлена ({len(build_grammar_words(hw))} слов)", tag="ASR")
                    except Exception as e:
                        log(f"asr: обновление грамматики: {e}", tag="ASR", level="WARN")

            for data_np, data_end_pos in drained_a16:
                if rec_hot.AcceptWaveform(data_np.tobytes()):
                    try:
                        text = json.loads(rec_hot.Result()).get("text", "").strip()
                    except Exception as e:
                        log(f"asr: [VOSK] JSON parse error: {e}", tag="ASR", level="WARN")
                        rec_hot = KaldiRecognizer(model, sr, grammar)
                        continue

                    if not text or cmd_filter.is_echo(text):
                        continue

                    tl = text.lower()
                    if "макс" in tl.split():
                        if gigaam.trigger_wake("vosk", data_end_pos):
                            log(f"asr: [VOSK] «макс» в «{text}»", tag="ASR")
                        continue

                    if cmd_filter.is_command_word(tl):
                        log(f"asr: [VOSK-CANDIDATE] «{text}»", tag="ASR")
                        if not hot_word_appropriate(tl):
                            log(f"asr: игнор hot word '{text}' (не в контексте)", tag="ASR")
                            continue
                        if not gigaam.trigger_hot(tl, data_end_pos):
                            log(f"asr: [HOT-BUSY] пропущен кандидат «{text}»", tag="ASR", level="DEBUG")
                        continue

                    log(f"asr: [VOSK] вне ролей: «{text}»", tag="ASR", level="DEBUG")
                elif VAD_DEBUG:
                    try:
                        partial = json.loads(rec_hot.PartialResult()).get("partial", "").strip()
                        if partial:
                            log(f"asr: [VOSK-PARTIAL] «{partial}»", tag="ASR", level="DEBUG")
                    except Exception:
                        pass

    except Exception as e:
        log(f"asr: main loop fatal: {e}", tag="ASR", level="ERROR")
    finally:
        log("asr: завершаю фоновые потоки...", tag="ASR")
        _shutdown.set()
        gigaam.wake_event().set()

        with capture_lock:
            cap = capture_box["cap"]
            capture_box["cap"] = None
        if cap is not None:
            try:
                cap.stop()
            except Exception:
                pass

        deadline = time.time() + 3.0
        for t in threads:
            rem = max(0.0, deadline - time.time())
            if t.is_alive():
                t.join(timeout=rem)

        with stats_lock:
            final_stats = dict(stats)
        log(f"asr: финальная статистика {final_stats}", tag="ASR")
        log("asr: стоп", tag="ASR")


if __name__ == "__main__":
    if os.name == "nt":
        os.system("chcp 65001 > nul")
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

    try:
        main()
    except KeyboardInterrupt:
        log("asr: остановка по Ctrl+C", tag="ASR")
        sys.exit(0)