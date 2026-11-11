import os
from setfit import SetFitModel
from setfit.exporters.onnx import export_onnx

MODEL_DIR = "D:/Maks/models/setfit_intents"
OUT_DIR = "D:/Maks/models/setfit_intents_onnx"
OUT = f"{OUT_DIR}/model.onnx"

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
print(f"ONNX exported: {OUT}")
print(f"Размер: {os.path.getsize(OUT) / 1024 / 1024:.1f} МБ")