"""Диски: речь. Короткая фраза (только C:) + строки для MAX (все, кроме not_found)."""


# Пороги для эмодзи. >25% — 🟢, 10–25% — 🟡, <10% — 🔴.
def _status_emoji(free_pct: int) -> str:
    if free_pct > 25:
        return "🟢"
    if free_pct > 10:
        return "🟡"
    return "🔴"


def _reason_ru(reason: str) -> str:
    if reason == "timeout":
        return "не отвечает"
    if reason == "permission":
        return "нет доступа"
    if reason == "error":
        return "ошибка"
    if reason == "no_data":
        return "нет данных"
    return "недоступен"


def phrase(data: dict) -> dict:
    """
    Вход: {"ok": True, "disks": {"C:": {...}, "D:": {...}, ...}}
    Выход:
        {
            "short": "на C: 45 ГБ свободно",
            "max_lines": [
                "💽 C: 45 ГБ свободно (18% 🟡)",
                "💽 D: 8 ГБ свободно (5% 🔴)",
                "💽 E: не отвечает",
            ]
        }
    """
    if not data.get("ok"):
        return {
            "short": "диски не вижу",
            "max_lines": ["💽 Диски: данные недоступны"],
        }

    disks = data.get("disks", {})

    # --- short: только C: ---
    c_info = disks.get("C:", {})
    if c_info.get("ok"):
        short = f"на C: {c_info.get('free_gb', 0)} ГБ свободно"
        if c_info.get("free_pct", 100) < 10:
            short += " — место кончается"
    else:
        reason = c_info.get("reason", "error")
        short = f"C: {_reason_ru(reason)}"

    # --- max_lines: все, кроме not_found ---
    max_lines = []
    for letter in ["C:", "D:", "E:", "F:", "G:"]:
        info = disks.get(letter)
        if not info:
            continue  # нет в списке — пропускаем

        if info.get("ok"):
            free_gb = info.get("free_gb", 0)
            free_pct = info.get("free_pct", 0)
            emoji = _status_emoji(free_pct)
            max_lines.append(
                f"💽 {letter} {free_gb} ГБ свободно ({free_pct}% {emoji})"
            )
        else:
            reason = info.get("reason", "error")
            if reason == "not_found":
                continue  # диск отсутствует — не мусорим
            max_lines.append(f"💽 {letter} {_reason_ru(reason)}")

    if not max_lines:
        max_lines = ["💽 Диски: данные недоступны"]

    return {"short": short, "max_lines": max_lines}