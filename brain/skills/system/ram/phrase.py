"""RAM: речь. Короткая фраза для голоса + строки для MAX."""


def phrase(data: dict) -> dict:
    """
    Вход: {"ok": True, "percent": 69, "free_pct": 31,
           "used_gb": 4.1, "total_gb": 5.9, "free_gb": 1.8}
    Выход:
        {"short": "RAM 31% свободно",
         "max_lines": ["⚡ Память: 1.8 ГБ свободно (31%) из 5.9"]}
    """
    if not data.get("ok"):
        return {
            "short": "RAM не вижу",
            "max_lines": ["⚡ Память: данные недоступны"],
        }

    free_pct = data.get("free_pct", 0)
    free_gb = data.get("free_gb", 0)
    total_gb = data.get("total_gb", 0)

    short = f"RAM {free_pct}% свободно"
    max_line = f"⚡ Память: {free_gb} ГБ свободно ({free_pct}%)"
    if total_gb:
        max_line += f" из {total_gb}"

    return {"short": short, "max_lines": [max_line]}