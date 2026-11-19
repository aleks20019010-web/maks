import json
import os
from setfit import SetFitModel
from setfit.exporters.onnx import export_onnx

MODEL_DIR = "D:/Maks/models/setfit_intents"
OUT_DIR = "D:/Maks/models/setfit_intents_onnx"
OUT = f"{OUT_DIR}/model.onnx"
LABELS_OUT = f"{OUT_DIR}/labels.json"

os.makedirs(OUT_DIR, exist_ok=True)

print("Загружаю SetFit...")
model = SetFitModel.from_pretrained(MODEL_DIR)

print("Экспортирую в ONNX (opset=14)...")
export_onnx(
    model.model_body,
    model.model_head,
    opset=14,
    output_path=OUT,
)

# Синхронизируем labels.json с классами обученной головы.
# APP_OPEN/APP_CLOSE (2026-11-19): после добавления новых классов
# старый labels.json разъезжается по индексам с новой model.onnx —
# теперь labels.json всегда перезаписывается из model.labels.
labels = [str(x) for x in model.labels]
with open(LABELS_OUT, "w", encoding="utf-8") as f:
    json.dump(labels, f, ensure_ascii=False, indent=2)

print(f"ONNX exported: {OUT} ({os.path.getsize(OUT) / 1024 / 1024:.1f} МБ)")
print(f"Labels exported: {LABELS_OUT} ({len(labels)} классов: {labels})")