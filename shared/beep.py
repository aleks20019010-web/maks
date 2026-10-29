# shared/beep.py
# Звуковой сигнал wake (как у Алисы / Гугла). Не TTS — микрофон не выключается.
# winsound (stdlib, Windows-only). WAV генерируется один раз в data/beep.wav.
#
# 3 тона: C5 (523) → E5 (659) → G5 (784), по 70 мс, fade 10 мс, пауза 15 мс.
# Всего ~250 мс. Мягко, восходящее, узнаваемо. Не «пииип» Pentium.
#
# Если хочешь свой WAV — положи в data/beep.wav, код его не тронет
# (ensure_beep_wav проверяет BEEP_FILE.exists()).

import math
import os
import struct
import wave

from shared import config as cfg


BEEP_FILE = cfg.DATA_DIR / "beep.wav"
BEEP_SAMPLE_RATE = 44100

# Ноты: (частота Гц, длительность мс)
BEEP_NOTES = [
    (523, 70),   # C5
    (659, 70),   # E5
    (784, 70),   # G5
]
BEEP_FADE_MS = 10
BEEP_GAP_MS = 15


def ensure_beep_wav():
    """Сгенерировать data/beep.wav, если нет. 3 тона, мягкий fade."""
    if BEEP_FILE.exists():
        return
    try:
        BEEP_FILE.parent.mkdir(parents=True, exist_ok=True)
        fade_sec = BEEP_FADE_MS / 1000.0
        gap_samples = int(BEEP_SAMPLE_RATE * BEEP_GAP_MS / 1000.0)

        with wave.open(str(BEEP_FILE), "w") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)  # 16 bit
            wav.setframerate(BEEP_SAMPLE_RATE)

            for note_freq, note_ms in BEEP_NOTES:
                duration_sec = note_ms / 1000.0
                num_samples = int(BEEP_SAMPLE_RATE * duration_sec)

                for i in range(num_samples):
                    t = i / BEEP_SAMPLE_RATE
                    # Fade in/out для мягкости
                    fade = 1.0
                    if t < fade_sec:
                        fade = t / fade_sec
                    elif t > duration_sec - fade_sec:
                        fade = (duration_sec - t) / fade_sec
                    sample = fade * math.sin(2 * math.pi * note_freq * t)
                    wav.writeframes(struct.pack("<h", int(sample * 32767)))

                # Пауза между нотами (тишина)
                for _ in range(gap_samples):
                    wav.writeframes(struct.pack("<h", 0))
    except Exception as e:
        print(f"⚠ beep: не сгенерировал {BEEP_FILE}: {e}")


def play_beep():
    """Играть сигнал асинхронно. Не блокирует, микрофон не трогает."""
    try:
        import winsound
        ensure_beep_wav()
        if BEEP_FILE.exists():
            winsound.PlaySound(
                str(BEEP_FILE),
                winsound.SND_FILENAME | winsound.SND_ASYNC,
            )
    except Exception as e:
        print(f"⚠ beep: play: {e}")