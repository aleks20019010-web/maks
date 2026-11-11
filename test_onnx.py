# test_onnx.py
# Проверка ONNX-модели SetFit: загрузка, инференс, контрольные фразы.
#
# FIX: ONNX может вернуть:
#   - labels (строки): ['MUSIC_PLAY']  → confidence = 1.0
#   - probs (числа):    [0.98, 0.01, ...] → argmax + softmax
# Обрабатываем оба случая.

import json
import time
import numpy as np
import onnxruntime as ort
from transformers import AutoTokenizer

MODEL_DIR = "D:/Maks/models/setfit_intents"
ONNX_PATH = "D:/Maks/models/setfit_intents_onnx/model.onnx"
LABELS_PATH = "D:/Maks/models/setfit_intents_onnx/labels.json"

# 1. Сохраняем labels один раз (порядок классов совпадает с PyTorch)
from setfit import SetFitModel
print("Загружаю PyTorch-модель для labels...")
model = SetFitModel.from_pretrained(MODEL_DIR)
with open(LABELS_PATH, "w", encoding="utf-8") as f:
    json.dump(list(model.labels), f, ensure_ascii=False)
labels = list(model.labels)
print(f"Классов: {len(labels)}")

# 2. Загружаем ONNX
print("Загружаю ONNX...")
t = time.time()
session = ort.InferenceSession(ONNX_PATH)
tokenizer = AutoTokenizer.from_pretrained(MODEL_DIR)
print(f"Загрузка ONNX: {time.time() - t:.2f}с")

# 3. Прогноз
def predict(text):
    enc = tokenizer([text], padding=True, truncation=True, return_tensors="np")
    feed = {k: v.astype(np.int64) for k, v in enc.items()
            if k in ("input_ids", "attention_mask", "token_type_ids")}
    out = session.run(None, feed)[0]

    # ONNX может вернуть labels (строки) или probs (числа)
    first = out[0]
    if isinstance(first, str):
        # labels: out = ['MUSIC_PLAY'], out[0] = 'MUSIC_PLAY'
        return first, 1.0
    # probs/logits: out[0] = array([...])
    probs = np.array(first)
    idx = int(np.argmax(probs))
    # Если не вероятности (сумма ≠ 1), применим softmax
    s = float(probs.sum())
    if abs(s - 1.0) > 0.05:
        # логиты → softmax
        e = np.exp(probs - probs.max())
        probs = e / e.sum()
    return labels[idx], float(probs[idx])

# 4. Контрольные фразы
PROBES = [
    ("включи мою музыку", "MUSIC_PLAY"),
    ("ария колизей", "MUSIC_PLAY"),
    ("включи король и шут лесник", "MUSIC_PLAY"),
    ("громче", "VOLUME_UP"),
    ("тише", "VOLUME_DOWN"),
    ("что за песня", "MUSIC_NOW_PLAYING"),
    ("привет", "SMALLTALK"),
    ("погода", "WEATHER_QUERY"),
]

print("\n=== Контрольные фразы ===")
ok = 0
for text, expected in PROBES:
    t = time.time()
    pred, conf = predict(text)
    elapsed = (time.time() - t) * 1000
    mark = "✅" if pred == expected else "❌"
    if pred == expected:
        ok += 1
    print(f"{mark} {text!r} → {pred} (conf={conf:.2f}, {elapsed:.1f}мс) [ожидалось {expected}]")

print(f"\nИтого: {ok}/{len(PROBES)}")

# 5. Отладка: показать сырой выход для одной фразы
print("\n=== RAW output (одна фраза) ===")
enc = tokenizer(["тест"], padding=True, truncation=True, return_tensors="np")
feed = {k: v.astype(np.int64) for k, v in enc.items()
        if k in ("input_ids", "attention_mask", "token_type_ids")}
out = session.run(None, feed)[0]
print(f"Тип: {type(out)}")
print(f"Форма: {getattr(out, 'shape', 'n/a')}")
print(f"Первые 5 значений: {out[:5] if hasattr(out, '__getitem__') else out}")