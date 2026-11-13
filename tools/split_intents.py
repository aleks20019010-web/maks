#!/usr/bin/env python3
"""
Разбить data/setfit_train.json на data/intents/<LABEL>.json.

Формат каждого файла — простой список строк (label в имени файла).
После разбивки единый setfit_train.json можно оставить как backup
или удалить — cv_setfit.py / train_setfit.py читают папку.

Запуск:
    python tools/split_intents.py
"""

import json
from collections import defaultdict
from pathlib import Path


def find_root() -> Path:
    candidates = [
        Path.cwd(),
        Path(__file__).resolve().parent,
        Path(__file__).resolve().parent.parent,
    ]
    for base in candidates:
        for parent in [base, *base.parents]:
            if (parent / "data" / "setfit_train.json").is_file():
                return parent
    raise SystemExit("Не найден data/setfit_train.json")


ROOT = find_root()
SRC = ROOT / "data" / "setfit_train.json"
OUT = ROOT / "data" / "intents"


def main():
    rows = json.loads(SRC.read_text(encoding="utf-8"))
    by_label: dict[str, list[str]] = defaultdict(list)
    seen: dict[str, set[str]] = defaultdict(set)

    for r in rows:
        label = r["label"]
        text = r["text"].strip()
        if not text:
            continue
        # дедуп внутри класса
        if text in seen[label]:
            continue
        seen[label].add(text)
        by_label[label].append(text)

    OUT.mkdir(parents=True, exist_ok=True)
    for label, texts in sorted(by_label.items()):
        path = OUT / f"{label}.json"
        path.write_text(
            json.dumps(texts, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"{label:24s} {len(texts):3d} → {path.name}")

    print(f"\nВсего классов: {len(by_label)}, фраз: {sum(len(v) for v in by_label.values())}")


if __name__ == "__main__":
    main()