# brain/skills/llm/_parser.py
# Парсер JSON-ответа от LLM.
#
# LLM возвращает JSON:
#   {"type": "command", "text": "включи КИНО"}
#   {"type": "answer", "full": "...", "short": "...", "minimal": "...", "explicit_detail": false}
#   {"type": "unknown"}
#
# Парсер:
#   1. Достаёт JSON из текста (может быть обёрнут в ```json ... ```).
#   2. Валидирует поля.
#   3. Проверяет whitelist для command.
#   4. Возвращает словарь или None (если не удалось).

import json
import re

from brain.skills.llm._whitelist import is_command_allowed


def parse_response(text: str) -> dict | None:
    """Распарсить ответ LLM.

    Возвращает:
    - {"type": "command", "text": "..."} — если валидная команда из whitelist.
    - {"type": "answer", "full": "...", "short": "...", "minimal": "...", "explicit_detail": false}
    - {"type": "unknown"}
    - None — если не удалось распарсить.
    """
    if not text:
        return None

    # 1. Достаём JSON из текста
    raw = _extract_json(text)
    if raw is None:
        return None

    # 2. Парсим JSON
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None

    if not isinstance(data, dict):
        return None

    # 3. Валидируем type
    t = data.get("type", "")
    if t not in ("command", "answer", "unknown"):
        return None

    # 4. command — проверяем whitelist
    if t == "command":
        cmd_text = data.get("text", "")
        if not isinstance(cmd_text, str) or not cmd_text.strip():
            return None
        if not is_command_allowed(cmd_text):
            return {"type": "unknown"}  # команда не из whitelist
        return {"type": "command", "text": cmd_text.strip()}

    # 5. answer — валидируем поля
    if t == "answer":
        full = data.get("full", "")
        short = data.get("short", "")
        minimal = data.get("minimal", "")
        explicit = bool(data.get("explicit_detail", False))

        # Хотя бы одно поле должно быть непустым
        if not (full or short or minimal):
            return None

        return {
            "type": "answer",
            "full": str(full) if full else "",
            "short": str(short) if short else "",
            "minimal": str(minimal) if minimal else "",
            "explicit_detail": explicit,
        }

    # 6. unknown
    return {"type": "unknown"}


def _extract_json(text: str) -> str | None:
    """Достать JSON из текста.

    LLM может вернуть:
    - Чистый JSON: {"type": "answer", ...}
    - Обёрнутый: ```json\n{...}\n```
    - С префиксом: Вот ответ: {...}
    """
    if not text:
        return None

    # Убираем ```json ... ```
    m = re.search(r'```(?:json)?\s*(.+?)\s*```', text, re.DOTALL)
    if m:
        return m.group(1).strip()

    # Ищем первый { ... }
    start = text.find("{")
    if start == -1:
        return None
    end = text.rfind("}")
    if end == -1 or end <= start:
        return None

    return text[start:end + 1].strip()