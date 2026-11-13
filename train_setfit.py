#!/usr/bin/env python3
"""
Обучение SetFit на data/setfit_train.json.

Схема:
  1) contrastive-обучение энкодера (SetFit Trainer) — 1 эпоха;
  2) ПЕРЕОБУЧЕНИЕ ГОЛОВЫ с class_weight="balanced" на train-эмбеддингах —
     лечит перекос MUSIC_PLAY (126) против остальных (10);
  3) метрики на val, confusion matrix, контрольные фразы;
  4) сохранение модели в models/setfit_intents/, отчёт в data/train_report.md.

База: cointegrated/rubert-tiny2.

Запуск (откуда угодно):
    python train_setfit.py

── FIX (2026-11-13): PROBES для REMINDER_* ───────────────────────
   Раньше: 6 контрольных фраз, ни одна не про reminders. После
   переобучения с новыми интентами (REMINDER_WHEN/LIST/DONE) модель
   могла «зелёно» пройти старые пробы, а новые интенты никто не
   проверил. Теперь: +4 фразы на REMINDER_ADD/WHEN/LIST/DONE.

── FIX (2026-11-13, #2): load_data() из data/intents/*.json ──────
   Датасет теперь по файлу на интент (data/intents/MUSIC_PLAY.json
   и т.д.). Fallback: старый data/setfit_train.json.
   Позволяет добавлять фразы в один файл, не трогая остальные.

── FIX (2026-11-13, #3): find_project_root() без setfit_train.json ──
   Раньше: искал только data/setfit_train.json. Если его удалить
   (перейдя полностью на data/intents/*.json), скрипт падал с
   sys.exit. Теперь: ищет data/intents/ ИЛИ data/setfit_train.json.
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


# ---------- автопоиск корня проекта ----------

def find_project_root() -> Path:
    """Ищем корень проекта: data/intents/ или data/setfit_train.json."""
    candidates = [
        Path.cwd(),
        Path(__file__).resolve().parent,
        Path(__file__).resolve().parent.parent,
    ]
    for base in candidates:
        for parent in [base, *base.parents]:
            if (parent / "data" / "intents").is_dir():
                return parent
            if (parent / "data" / "setfit_train.json").is_file():
                return parent
    sys.exit(
        "Не найден ни data/intents/, ни data/setfit_train.json. "
        "Искал вверх от:\n  " + "\n  ".join(str(c) for c in candidates)
    )


ROOT = find_project_root()
DATA_PATH = ROOT / "data" / "setfit_train.json"
MODEL_DIR = ROOT / "models" / "setfit_intents"
REPORT_PATH = ROOT / "data" / "train_report.md"

DEFAULT_BASE = "cointegrated/rubert-tiny2"

# Контрольные фразы: рискованные пары + то, что проверяли руками.
# FIX (2026-11-13): +4 на REMINDER_* (ADD/WHEN/LIST/DONE).
PROBES = [
    # музыка / общие
    ("включи мою музыку",              "MUSIC_WAVE"),
    ("что за песня",                   "MUSIC_NOW_PLAYING"),
    ("почему песня так называется",    "LLM_QUESTION"),
    ("расскажи про группу",            "LLM_QUESTION"),
    ("громче",                         "VOLUME_UP"),
    ("почисти корзину",                "CLEANUP_RECYCLE"),
    # напоминания (2026-11-13)
    ("напомни через 10 минут позвонить", "REMINDER_ADD"),
    ("когда к врачу",                    "REMINDER_WHEN"),
    ("мои напоминания",                  "REMINDER_LIST"),
    ("готово позвонить маме",            "REMINDER_DONE"),
]


def load_data():
    """Загрузить датасет из data/intents/*.json (по файлу на интент).

    Fallback: старый data/setfit_train.json (list[{text,label}]).
    """
    intents_dir = ROOT / "data" / "intents"
    if intents_dir.is_dir():
        rows = []
        for path in sorted(intents_dir.glob("*.json")):
            label = path.stem  # MUSIC_PLAY.json → MUSIC_PLAY
            try:
                items = json.loads(path.read_text(encoding="utf-8"))
            except Exception as e:
                sys.exit(f"{path}: {e}")
            if not isinstance(items, list):
                sys.exit(f"{path}: ожидался list")
            for item in items:
                if isinstance(item, str):
                    text = item.strip()
                elif isinstance(item, dict) and "text" in item:
                    text = str(item["text"]).strip()
                else:
                    sys.exit(f"{path}: плохая запись {item!r}")
                if text:
                    rows.append({"text": text, "label": label})
        if not rows:
            sys.exit(f"{intents_dir}: пусто")
        print(f"Датасет: {len(rows)} фраз из {len(list(intents_dir.glob('*.json')))} файлов")
        return rows

    # Fallback: старый единый файл
    with open(DATA_PATH, encoding="utf-8") as f:
        rows = json.load(f)
    if not isinstance(rows, list) or not rows:
        sys.exit(f"{DATA_PATH}: ожидался непустой list[{{text, label}}]")
    for r in rows:
        if not isinstance(r, dict) or "text" not in r or "label" not in r:
            sys.exit(f"Плохая запись в {DATA_PATH}: {r!r}")
    return rows


def stratified_split(rows, val_frac=0.2, seed=42):
    import random
    rnd = random.Random(seed)
    by_label = {}
    for r in rows:
        by_label.setdefault(r["label"], []).append(r)
    train, val = [], []
    for label, items in by_label.items():
        rnd.shuffle(items)
        n_val = max(1, int(round(len(items) * val_frac))) if len(items) >= 5 else 1
        n_val = min(n_val, max(1, len(items) - 1))
        val.extend(items[:n_val])
        train.extend(items[n_val:])
    rnd.shuffle(train)
    rnd.shuffle(val)
    return train, val


def render_confusion(cm, labels):
    header = "true\\pred".ljust(20) + " ".join(l[:6].rjust(7) for l in labels)
    out = [header]
    for i, lab in enumerate(labels):
        row = " ".join(str(cm[i][j]).rjust(7) for j in range(len(labels)))
        out.append(lab[:19].ljust(20) + row)
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=DEFAULT_BASE,
                    help="базовая sentence-transformers модель")
    ap.add_argument("--epochs", type=int, default=1,
                    help="эпох SetFit (1 обычно достаточно)")
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--val-frac", type=float, default=0.2)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--C", type=float, default=1.0,
                    help="регуляризация логистической регрессии головы")
    args = ap.parse_args()

    rows = load_data()
    labels = sorted({r["label"] for r in rows})
    counts = Counter(r["label"] for r in rows)
    print(f"Данных: {len(rows)}, классов: {len(labels)}")
    print("Распределение:", dict(sorted(counts.items(), key=lambda x: -x[1])))

    # тяжёлые импорты — после проверки данных
    try:
        from datasets import Dataset
        from setfit import SetFitModel, Trainer, TrainingArguments
        from sklearn.linear_model import LogisticRegression
        from sklearn.metrics import (
            accuracy_score, classification_report,
            confusion_matrix, f1_score,
        )
    except ImportError as e:
        sys.exit(f"Не хватает зависимости: {e}\nУстанови: pip install setfit datasets scikit-learn")

    # stratified split
    train_rows, val_rows = stratified_split(rows, args.val_frac, args.seed)
    print(f"Train: {len(train_rows)}, Val: {len(val_rows)}")

    train_ds = Dataset.from_list(train_rows)
    val_ds = Dataset.from_list(val_rows) if val_rows else None

    # ---------- 1. contrastive ----------
    model = SetFitModel.from_pretrained(args.base)

    training_args = TrainingArguments(
        batch_size=args.batch_size,
        num_epochs=args.epochs,
        num_iterations=20,
        seed=args.seed,
        report_to="none",
        save_strategy="no",
    )

    trainer_kwargs = dict(model=model, args=training_args, train_dataset=train_ds)
    if val_ds is not None:
        trainer_kwargs["eval_dataset"] = val_ds

    trainer = Trainer(**trainer_kwargs)
    print("Обучение (contrastive)...")
    trainer.train()

    # ---------- 2. balanced-голова ----------
    print("\nПереобучение головы с class_weight='balanced'...")
    train_texts = [r["text"] for r in train_rows]
    train_labels = [r["label"] for r in train_rows]
    train_emb = model.model_body.encode(
        train_texts,
        batch_size=32,
        show_progress_bar=False,
        convert_to_numpy=True,
    )

    head = LogisticRegression(
        class_weight="balanced",
        max_iter=2000,
        C=args.C,
        random_state=args.seed,
    )
    head.fit(train_emb, train_labels)

    # подменяем голову и синхронизируем классы
    model.model_head = head
    try:
        model.labels = list(head.classes_)
    except Exception:
        pass
    print("Голова переобучена.")

    # ---------- 3. метрики ----------
    lines = ["# Отчёт обучения SetFit (balanced)\n"]
    lines.append(f"- База: `{args.base}`")
    lines.append(f"- Train: {len(train_rows)}, Val: {len(val_rows)}")
    lines.append(f"- Классов: {len(labels)}")
    lines.append(f"- Голова: LogisticRegression(class_weight='balanced', C={args.C})\n")

    val_metrics = {}
    if val_rows:
        val_texts = [r["text"] for r in val_rows]
        y_true = [r["label"] for r in val_rows]
        y_pred = [str(x) for x in model.predict(val_texts)]
        acc = accuracy_score(y_true, y_pred)
        macro_f1 = f1_score(y_true, y_pred, average="macro", zero_division=0)
        val_metrics = {"accuracy": acc, "macro_f1": macro_f1}
        lines.append("## Метрики на валидации (balanced)\n")
        lines.append(f"- accuracy: **{acc:.3f}**")
        lines.append(f"- macro-F1: **{macro_f1:.3f}**\n")
        lines.append("### Per-class\n```")
        lines.append(classification_report(y_true, y_pred, zero_division=0))
        lines.append("```\n")
        lines.append("### Confusion matrix (val)\n```")
        cm = confusion_matrix(y_true, y_pred, labels=labels)
        lines.append(render_confusion(cm, labels))
        lines.append("```\n")

    # ---------- 4. контрольные фразы ----------
    lines.append("## Контрольные фразы\n")
    lines.append("| Фраза | Ожидалось | Предсказано | OK |")
    lines.append("|---|---|---|---|")
    ok_all = True
    for text, expected in PROBES:
        pred = str(model.predict([text])[0])
        ok = "✅" if pred == expected else "❌"
        if pred != expected:
            ok_all = False
        lines.append(f"| {text} | {expected} | {pred} | {ok} |")
    lines.append("")
    lines.append(f"**Итог контрольных: {'все верно' if ok_all else 'есть ошибки — смотри выше'}**\n")

    # ---------- 5. сохранение ----------
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(str(MODEL_DIR))
    print(f"Модель сохранена: {MODEL_DIR}")

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"Отчёт: {REPORT_PATH}")

    if val_metrics:
        print(f"Val accuracy: {val_metrics['accuracy']:.3f}, macro-F1: {val_metrics['macro_f1']:.3f}")
    if not ok_all:
        print("ВНИМАНИЕ: часть контрольных фраз предсказана неверно — смотри отчёт.")


if __name__ == "__main__":
    main()