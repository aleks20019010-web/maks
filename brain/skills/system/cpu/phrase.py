"""CPU: речь. Короткая фраза для голоса + строки для MAX."""


def _status_ru(percent: int) -> str:
    if percent < 20:
        return "отдыхает"
    if percent < 50:
        return "в норме"
    if percent < 80:
        return "работает"
    if percent < 95:
        return "нагружен"
    return "работает на износ"


def phrase(data: dict) -> dict:
    """
    Вход: {"ok": True, "percent": 3, "cores": 8}
    Выход:
        {"short": "процессор 3%, отдыхает",
         "max_lines": ["💻 Процессор: 3% — отдыхает"]}
    """
    if not data.get("ok"):
        return {
            "short": "процессор не вижу",
            "max_lines": ["💻 Процессор: данные недоступны"],
        }

    percent = data.get("percent", 0)
    status = _status_ru(percent)

    short = f"процессор {percent}%, {status}"   # ← по-русски, для голоса
    max_line = f"💻 Процессор: {percent}% — {status}"

    return {"short": short, "max_lines": [max_line]}