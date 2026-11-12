import os
import numpy as np
import soundfile as sf
import onnx_asr
from vosk import Model, KaldiRecognizer

# ── Пути ──────────────────────────────────────────────────────
CAPTURE_DIR = r"C:\Windows\System32\captures\echoff-20261112-120637"
VOSK_MODEL_PATH = r"D:\Maks\models\vosk-model-small-ru-0.22"

FILES = {
    "RAW (микрофон до AEC)":   os.path.join(CAPTURE_DIR, "microphone_raw.wav"),
    "AEC (микрофон после AEC)": os.path.join(CAPTURE_DIR, "microphone_aec.wav"),
    "MUSIC (только музыка)":    os.path.join(CAPTURE_DIR, "computer_audio.wav"),
}

TARGET_SR = 16000

# ── Загрузка моделей ──────────────────────────────────────────
print("⏳ Загружаю Vosk...")
vosk_model = Model(VOSK_MODEL_PATH)
print("✅ Vosk загружен")

print("⏳ Загружаю GigaAM v3 RNN-T...")
gigaam_model = onnx_asr.load_model("gigaam-v3-rnnt")
print("✅ GigaAM загружен\n")

# ── Хелперы ───────────────────────────────────────────────────
def load_and_resample(path: str) -> np.ndarray:
    """Читает wav, ресемплит до 16кГц, возвращает int16 numpy."""
    audio, sr = sf.read(path, dtype='int16')
    if len(audio.shape) > 1:
        audio = audio.mean(axis=1).astype(np.int16)
    
    if sr != TARGET_SR:
        new_len = int(len(audio) * TARGET_SR / sr)
        old_idx = np.arange(len(audio))
        new_idx = np.linspace(0, len(audio) - 1, new_len)
        audio = np.interp(new_idx, old_idx, audio).astype(np.int16)
    return audio

def run_vosk(audio_int16: np.ndarray) -> str:
    rec = KaldiRecognizer(vosk_model, TARGET_SR)
    # Скармливаем кусками по 4000 сэмплов (250мс), как в реальном времени
    chunk_size = 4000
    for i in range(0, len(audio_int16), chunk_size):
        rec.AcceptWaveform(audio_int16[i:i+chunk_size].tobytes())
    final = rec.FinalResult()
    import json
    return json.loads(final).get("text", "").strip()

def run_gigaam(audio_int16: np.ndarray) -> str:
    audio_float = audio_int16.astype(np.float32) / 32768.0
    try:
        return gigaam_model.recognize(audio_float, sample_rate=TARGET_SR)
    except Exception as e:
        return f"[ОШИБКА: {e}]"


# ── Основной цикл ─────────────────────────────────────────────
for label, path in FILES.items():
    print("=" * 70)
    print(f"📁 {label}")
    print(f"   {path}")
    print("=" * 70)
    
    if not os.path.exists(path):
        print(f"   ⚠️  Файл не найден, пропускаю\n")
        continue
    
    audio = load_and_resample(path)
    duration = len(audio) / TARGET_SR
    print(f"   Длительность: {duration:.1f} сек\n")
    
    print("   🎙️  Vosk:")
    vosk_text = run_vosk(audio)
    print(f"      «{vosk_text}»\n")
    
    print("   🚀 GigaAM:")
    gigaam_text = run_gigaam(audio)
    print(f"      «{gigaam_text}»\n")

print("=" * 70)
print("✅ Тест завершён")