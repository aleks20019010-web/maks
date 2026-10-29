# tools/test_wake_live.py
# Простенький live-тест wake word. НЕ входит в Макс — одноразовый.
#
# Модель: ViolaWake (kinder85/openwakeword-train → viola_models/max_viola.onnx).
# Использует violawake_sdk.WakeDetector.process(), НЕ detect().
#
# Использование:
#   python tools/test_wake_live.py --model D:\Projects\openwakeword-train\viola_models\max_viola.onnx --threshold 0.8
#
# Что делает:
#   - Слушает микрофон 16 кГц.
#   - WakeDetector.process(audio_chunk) → score.
#   - Печатает score, если > 0.1, «🔔 WAKE» при score >= threshold.
#
# Тестировать:
#   1. Скажи «Макс» 5 раз → 5 WAKE.
#   2. Скажи «макароны» 5 раз → 0 WAKE.
#   3. Включи музыку → 0 WAKE.
#   4. Скажи «Макс» при музыке → 1 WAKE.
#   5. TTS Макса говорит → 0 WAKE.
#
# Ctrl+C — остановка.

import argparse
import queue

import numpy as np
import sounddevice as sd
from violawake_sdk import WakeDetector


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True,
                        help="путь к max_viola.onnx")
    parser.add_argument("--threshold", type=float, default=0.8,
                        help="порог wake (из config.json)")
    parser.add_argument("--device", type=int, default=None,
                        help="индекс микрофона (по умолчанию — системный)")
    args = parser.parse_args()

    print(f"Загружаю модель: {args.model}")
    wake_detector = WakeDetector(model=args.model, threshold=args.threshold)
    print(f"Порог: {args.threshold}")
    print(f"Микрофон: {args.device if args.device is not None else 'по умолчанию'}")
    print()
    print("Слушаю. Говори «Макс» / «макароны» / «Макс» при музыке.")
    print("Ctrl+C — остановка.")
    print()

    q = queue.Queue()

    def callback(indata, frames, time_info, status):
        q.put(bytes(indata))

    try:
        with sd.RawInputStream(
            samplerate=16000,
            blocksize=1280,  # 80 мс — шаг ViolaWake
            device=args.device,
            channels=1,
            dtype="int16",
            callback=callback,
        ):
            while True:
                data = q.get()
                audio = np.frombuffer(data, dtype=np.int16)
                # ViolaWake: process(), НЕ detect()
                score = wake_detector.process(audio)
                if score >= 0.1:
                    marker = " 🔔 WAKE" if score >= args.threshold else ""
                    print(f"  score: {score:.3f}{marker}")
    except KeyboardInterrupt:
        print("\nОстановлено.")
    except Exception as e:
        print(f"\nОшибка: {e}")


if __name__ == "__main__":
    main()