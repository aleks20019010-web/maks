# tools/test_parser.py
# Тест парсера LLM-ответов.

import sys
import os

# Добавить корень проекта в sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brain.skills.llm._parser import parse_response


tests = [
    ('{"type": "command", "text": "включи музыку"}', "command"),
    ('```json\n{"type": "answer", "full": "Потому что...", "short": "Рассеяние.", "minimal": "Рассеяние.", "explicit_detail": false}\n```', "answer"),
    ('{"type": "command", "text": "формат диск"}', "unknown"),
    ("просто текст", None),
    ('{"type": "unknown"}', "unknown"),
]


for text, expected_type in tests:
    result = parse_response(text)
    result_type = result["type"] if result else None
    status = "OK" if result_type == expected_type else "FAIL"
    print(f"{status}: {text[:50]!r} → {result_type} (ожидалось {expected_type})")