#!/usr/bin/env python3
"""
Подготовка данных для SetFit из config/intent_routes.json.

Режимы:
  --dry-run (по умолчанию)   только отчёт, ничего не пишет в setfit_train.json
  --apply                    записать data/setfit_train.json
  --apply-resolution FILE    применить ручные решения из JSON-файла
  --auto-resolve {majority,drop}
                             авторезолв коллизий: majority = оставить в интенте
                             с большим числом примеров; drop = выкинуть фразу
  --keep-yo                  не схлопывать ё→е при сравнении
  --drop-suspicious          выкинуть фразы, помеченные как suspicious
  --out PATH                 путь для setfit_train.json
                             (по умолчанию data/setfit_train.json)
  --min-per-class N          порог «мало примеров» (по умолчанию 10)
"""

import argparse
import json
import re
import sys
from collections import defaultdict, Counter
from pathlib import Path


DEFAULT_INPUT = "config/intent_routes.json"
DEFAULT_OUT = "data/setfit_train.json"
DEFAULT_REPORT_JSON = "data/duplicates_report.json"
DEFAULT_REPORT_MD = "data/duplicates_report.md"


# ---------- нормализация ----------

_WS_RE = re.compile(r"\s+")
_TRAIL_PUNCT_RE = re.compile(r"[.,!?;:]+$")


def normalize(text: str, keep_yo: bool = False) -> str:
    t = text.strip().lower()
    t = _WS_RE.sub(" ", t)
    t = _TRAIL_PUNCT_RE.sub("", t)
    if not keep_yo:
        t = t.replace("ё", "е")
    return t


# ---------- эвристики «suspicious» ----------

# Слова-обрывки: фраза заканчивается на них — почти наверняка недописана.
_DANGLING_TAILS = {
    "когда", "мне", "что", "я", "как", "где", "почему",
    "и", "а", "но", "или", "же", "бы", "ли",
}


def suspicious_reason(text: str, intent: str, all_intents: dict) -> str | None:
    t = text.strip().lower()
    words = t.split()
    if not words:
        return "пустая строка"
    # обрывок в конце
    if words[-1] in _DANGLING_TAILS:
        return f"заканчивается на служебное слово «{words[-1]}»"
    # очень короткая, но не из известных команд
    if len(t) <= 3 and intent not in ("MUSIC_STOP", "MUSIC_PAUSE", "MUSIC_NEXT", "MUSIC_PREV"):
        return "очень короткая фраза"
    # имя собственное в нижнем регистре, которого нет в других фразах этого интента
    # (мягкая эвристика: слово с заглавной буквы невозможно в нижнем регистре,
    # поэтому ловим только кириллические имена/бренды через белый список)
    suspicious_names = {"ваню", "ваня", "авто"}
    for w in words:
        if w in suspicious_names:
            return f"возможное имя собственное «{w}» — проверь вручную"
    return None


# ---------- загрузка ----------

def load_routes(path: str) -> dict[str, list[str]]:
    p = Path(path)
    if not p.exists():
        sys.exit(f"Файл не найден: {path}")
    with p.open(encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            sys.exit(f"Некорректный JSON в {path}: {e}")
    if not isinstance(data, dict):
        sys.exit(f"Ожидался dict[str, list[str]], получено {type(data).__name__}")
    for k, v in data.items():
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            sys.exit(f"Интент {k}: ожидался list[str]")
    return data


# ---------- анализ ----------

def analyze(routes: dict[str, list[str]], keep_yo: bool) -> dict:
    # 1. внутренние дубли (по нормализованному ключу)
    internal_dupes = {}  # intent -> {key: [original, original, ...]}
    cleaned = {}         # intent -> list[original] без внутренних дублей
    for intent, phrases in routes.items():
        seen = defaultdict(list)
        for ph in phrases:
            key = normalize(ph, keep_yo)
            seen[key].append(ph)
        internal_dupes[intent] = {k: v for k, v in seen.items() if len(v) > 1}
        cleaned[intent] = [v[0] for v in seen.values()]  # первое вхождение

    # 2. межинтентовые коллизии
    key_to_intents = defaultdict(set)
    key_to_originals = defaultdict(set)
    for intent, phrases in cleaned.items():
        for ph in phrases:
            key = normalize(ph, keep_yo)
            key_to_intents[key].add(intent)
            key_to_originals[key].add(ph)
    cross_collisions = {
        k: {"intents": sorted(v), "originals": sorted(key_to_originals[k])}
        for k, v in key_to_intents.items()
        if len(v) > 1
    }

    # 3. семантически близкие пары интентов (по общим токенам)
    token_by_intent = {}
    for intent, phrases in cleaned.items():
        toks = Counter()
        for ph in phrases:
            for w in normalize(ph, keep_yo).split():
                toks[w] += 1
        token_by_intent[intent] = toks
    close_pairs = []
    intents = sorted(token_by_intent)
    for i in range(len(intents)):
        for j in range(i + 1, len(intents)):
            a, b = intents[i], intents[j]
            common = set(token_by_intent[a]) & set(token_by_intent[b])
            # отбрасываем слишком общие слова
            common -= {"включи", "поставь", "вруби", "хочу", "давай", "навали",
                       "зажги", "музыку", "музыка", "музыки", "что", "как",
                       "мне", "на", "в", "и", "с", "не", "по"}
            if len(common) >= 2:
                close_pairs.append({
                    "a": a, "b": b,
                    "common_tokens": sorted(common),
                    "n_common": len(common),
                })
    close_pairs.sort(key=lambda x: -x["n_common"])

    # 4. suspicious
    suspicious = []
    for intent, phrases in cleaned.items():
        for ph in phrases:
            reason = suspicious_reason(ph, intent, cleaned)
            if reason:
                suspicious.append({"intent": intent, "text": ph, "reason": reason})

    # 5. дисбаланс
    counts = {intent: len(phrases) for intent, phrases in cleaned.items()}
    values = sorted(counts.values())
    balance = {
        "min": min(values) if values else 0,
        "max": max(values) if values else 0,
        "median": values[len(values) // 2] if values else 0,
        "counts": counts,
        "below_threshold": [],  # заполняется в main с учётом min_per_class
    }

    return {
        "internal_dupes": internal_dupes,
        "cross_collisions": cross_collisions,
        "close_pairs": close_pairs,
        "suspicious": suspicious,
        "balance": balance,
        "cleaned": cleaned,
        "total_after_dedup": sum(len(v) for v in cleaned.values()),
    }


# ---------- резолюции ----------

def apply_resolution(collisions: dict, resolution_path: str, keep_yo: bool) -> dict:
    p = Path(resolution_path)
    if not p.exists():
        sys.exit(f"Файл резолюций не найден: {resolution_path}")
    with p.open(encoding="utf-8") as f:
        resolution = json.load(f)
    if not isinstance(resolution, dict):
        sys.exit("Файл резолюций должен быть JSON-объектом {фраза: интент}")

    # нормализуем ключи резолюции для сверки
    norm_res = {normalize(k, keep_yo): v for k, v in resolution.items()}
    leftover = set(norm_res) - set(collisions)
    if leftover:
        sys.exit(
            "В файле резолюций есть фразы, которых нет среди коллизий "
            "(данные изменились — перепроверь вручную):\n  " + "\n  ".join(sorted(leftover))
        )
    return norm_res


def auto_resolve(collisions: dict, mode: str, cleaned: dict, keep_yo: bool) -> dict:
    """Возвращает {normalized_key: intent} — какой интент оставить."""
    res = {}
    for key, info in collisions.items():
        if mode == "majority":
            # интент с большим числом примеров
            best = max(info["intents"], key=lambda it: len(cleaned[it]))
            res[key] = best
        elif mode == "drop":
            res[key] = "__DROP__"
        else:
            sys.exit(f"Неизвестный режим авторезолва: {mode}")
    return res


# ---------- финальная сборка ----------

def build_final(cleaned: dict, resolutions: dict, drop_suspicious: bool,
                suspicious: list, keep_yo: bool) -> list[dict]:
    # нормализованные ключи, которые надо удалить из всех интентов
    drop_keys = {k for k, v in resolutions.items() if v == "__DROP__"}
    # keep_keys: key -> intent, в котором оставить
    keep_keys = {k: v for k, v in resolutions.items() if v != "__DROP__"}

    # suspicious-фразы, которые надо выкинуть (по точному совпадению)
    drop_susp_texts = set()
    if drop_suspicious:
        for s in suspicious:
            drop_susp_texts.add((s["intent"], s["text"]))

    final = []
    for intent, phrases in cleaned.items():
        for ph in phrases:
            key = normalize(ph, keep_yo)
            if key in drop_keys:
                continue
            if key in keep_keys and keep_keys[key] != intent:
                continue
            if (intent, ph) in drop_susp_texts:
                continue
            final.append({"text": ph, "label": intent})
    return final


# ---------- отчёт ----------

def render_md(report: dict, min_per_class: int, final_size: int) -> str:
    lines = []
    lines.append("# Отчёт подготовки данных для SetFit\n")

    lines.append("## 1. Внутренние дубли (внутри одного интента)\n")
    if not report["internal_dupes"]:
        lines.append("_Нет._\n")
    else:
        for intent, dupes in report["internal_dupes"].items():
            lines.append(f"### `{intent}`\n")
            for key, originals in dupes.items():
                lines.append(f"- ключ `{key}` → оригиналы: {originals}")
            lines.append("")

    lines.append("## 2. Межинтентовые коллизии (одна фраза в разных интентах)\n")
    if not report["cross_collisions"]:
        lines.append("_Формальных точных дублей нет._\n")
    else:
        for key, info in report["cross_collisions"].items():
            lines.append(f"- `{key}` → интенты: {info['intents']}")
        lines.append("")

    lines.append("## 3. Семантически близкие пары интентов (по общим токенам)\n")
    if not report["close_pairs"]:
        lines.append("_Нет пар с ≥2 общими значимыми токенами._\n")
    else:
        for p in report["close_pairs"]:
            lines.append(
                f"- `{p['a']}` ↔ `{p['b']}` — общих токенов: {p['n_common']} "
                f"({', '.join(p['common_tokens'])})"
            )
        lines.append("")

    lines.append("## 4. Подозрительные фразы\n")
    if not report["suspicious"]:
        lines.append("_Нет._\n")
    else:
        for s in report["suspicious"]:
            lines.append(f"- `{s['intent']}` / `{s['text']}` — {s['reason']}")
        lines.append("")

    lines.append("## 5. Дисбаланс классов\n")
    b = report["balance"]
    lines.append(f"- min = {b['min']}, max = {b['max']}, median = {b['median']}\n")
    lines.append("\n| Интент | Примеров | Статус |\n|---|---|---|")
    for intent, n in sorted(b["counts"].items(), key=lambda x: x[1]):
        status = "OK" if n >= min_per_class else f"**мало (<{min_per_class})**"
        lines.append(f"| `{intent}` | {n} | {status} |")
    lines.append("")

    lines.append("## 6. Итог\n")
    lines.append(f"- Примеров после дедупа внутри интентов: **{report['total_after_dedup']}**")
    lines.append(f"- Примеров в финальном датасете (с учётом резолюций и drop-suspicious): **{final_size}**")
    if b["counts"]:
        below = [i for i, n in b["counts"].items() if n < min_per_class]
        if below:
            lines.append(f"- Интенты ниже порога {min_per_class}: {below}")
    lines.append("")
    return "\n".join(lines)


# ---------- main ----------

def main():
    ap = argparse.ArgumentParser(description="Подготовка данных для SetFit")
    ap.add_argument("--input", default=DEFAULT_INPUT)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--report-json", default=DEFAULT_REPORT_JSON)
    ap.add_argument("--report-md", default=DEFAULT_REPORT_MD)
    ap.add_argument("--dry-run", action="store_true", default=True)
    ap.add_argument("--apply", dest="dry_run", action="store_false")
    ap.add_argument("--apply-resolution", metavar="FILE")
    ap.add_argument("--auto-resolve", choices=["majority", "drop"])
    ap.add_argument("--keep-yo", action="store_true")
    ap.add_argument("--drop-suspicious", action="store_true")
    ap.add_argument("--min-per-class", type=int, default=10)
    args = ap.parse_args()

    routes = load_routes(args.input)
    report = analyze(routes, keep_yo=args.keep_yo)

    # резолюции
    resolutions = {}
    if args.apply_resolution:
        resolutions = apply_resolution(
            report["cross_collisions"], args.apply_resolution, args.keep_yo
        )
    elif args.auto_resolve:
        resolutions = auto_resolve(
            report["cross_collisions"], args.auto_resolve, report["cleaned"], args.keep_yo
        )

    final = build_final(
        report["cleaned"], resolutions,
        drop_suspicious=args.drop_suspicious,
        suspicious=report["suspicious"],
        keep_yo=args.keep_yo,
    )

    # запись отчётов (всегда, даже в dry-run)
    Path(args.report_json).parent.mkdir(parents=True, exist_ok=True)
    with open(args.report_json, "w", encoding="utf-8") as f:
        json.dump({
            "internal_dupes": report["internal_dupes"],
            "cross_collisions": report["cross_collisions"],
            "close_pairs": report["close_pairs"],
            "suspicious": report["suspicious"],
            "balance": report["balance"],
            "total_after_dedup": report["total_after_dedup"],
            "final_size": len(final),
        }, f, ensure_ascii=False, indent=2)

    md = render_md(report, args.min_per_class, len(final))
    with open(args.report_md, "w", encoding="utf-8") as f:
        f.write(md)

    print(f"Отчёт записан: {args.report_md}")
    print(f"JSON-отчёт:   {args.report_json}")

    if args.dry_run:
        print(f"\nDry-run: setfit_train.json НЕ записан. Финальный размер был бы: {len(final)}")
        print("Запусти с --apply, чтобы записать.")
        return

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(final, f, ensure_ascii=False, indent=2)
    print(f"\nДатасет записан: {args.out} ({len(final)} примеров)")
    if args.min_per_class:
        below = [i for i, n in report["balance"]["counts"].items() if n < args.min_per_class]
        if below:
            print(f"Внимание: интенты ниже порога {args.min_per_class}: {below}")


if __name__ == "__main__":
    main()