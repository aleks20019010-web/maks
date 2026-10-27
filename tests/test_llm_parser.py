# tests/test_llm_parser.py
# Тесты brain/skills/llm/_parser.py.
#
# Проверяем parse_response на всём, что реально приходит от LLM:
#   - чистый JSON
#   - обёртка ```json ... ```
#   - префикс «Вот ответ: {...}»
#   - мусор вокруг JSON
#   - битый JSON
#   - command с whitelist (разрешённый / запрещённый)
#   - answer с полями и без
#   - unknown
#   - пустой вход
#
# _parser импортирует _whitelist.is_command_allowed на уровне модуля —
# тест «формат диск» проверяет реальный whitelist, не мок.

from brain.skills.llm._parser import parse_response


# ── command: разрешённый ───────────────────────────────────

def test_command_allowed():
    raw = '{"type": "command", "text": "включи музыку"}'
    result = parse_response(raw)
    assert result == {"type": "command", "text": "включи музыку"}


def test_command_pause():
    raw = '{"type": "command", "text": "пауза"}'
    result = parse_response(raw)
    assert result == {"type": "command", "text": "пауза"}


# ── command: запрещённый (whitelist) ───────────────────────

def test_command_forbidden_format():
    # «формат» в FORBIDDEN — whitelist блокирует
    raw = '{"type": "command", "text": "формат диск"}'
    result = parse_response(raw)
    assert result == {"type": "unknown"}


def test_command_forbidden_shutdown():
    raw = '{"type": "command", "text": "shutdown"}'
    result = parse_response(raw)
    assert result == {"type": "unknown"}


# ── answer ─────────────────────────────────────────────────

def test_answer_full():
    raw = (
        '{"type": "answer", '
        '"full": "Рассеяние Рэлея.", '
        '"short": "Рассеяние.", '
        '"minimal": "Свет.", '
        '"explicit_detail": false}'
    )
    result = parse_response(raw)
    assert result is not None
    assert result["type"] == "answer"
    assert result["full"] == "Рассеяние Рэлея."
    assert result["short"] == "Рассеяние."
    assert result["minimal"] == "Свет."
    assert result["explicit_detail"] is False


def test_answer_explicit_detail_true():
    raw = (
        '{"type": "answer", '
        '"full": "Длинный ответ.", '
        '"short": "Короткий.", '
        '"explicit_detail": true}'
    )
    result = parse_response(raw)
    assert result["explicit_detail"] is True


def test_answer_no_fields_returns_none():
    # answer без full/short/minimal — невалиден
    raw = '{"type": "answer"}'
    assert parse_response(raw) is None


def test_answer_empty_strings_returns_none():
    raw = '{"type": "answer", "full": "", "short": "", "minimal": ""}'
    assert parse_response(raw) is None


# ── unknown ────────────────────────────────────────────────

def test_unknown():
    raw = '{"type": "unknown"}'
    result = parse_response(raw)
    assert result == {"type": "unknown"}


# ── Обёртка ```json ... ``` ────────────────────────────────

def test_markdown_wrapper():
    raw = (
        "```json\n"
        '{"type": "answer", "short": "Рассеяние.", "explicit_detail": false}'
        "\n```"
    )
    result = parse_response(raw)
    assert result is not None
    assert result["type"] == "answer"
    assert result["short"] == "Рассеяние."


def test_markdown_wrapper_no_lang():
    raw = "```\n" '{"type": "unknown"}' "\n```"
    result = parse_response(raw)
    assert result == {"type": "unknown"}


# ── Префикс / суффикс вокруг JSON ──────────────────────────

def test_prefix_text_before_json():
    raw = (
        "Вот ответ:\n"
        '{"type": "answer", "short": "Да.", "explicit_detail": false}'
    )
    result = parse_response(raw)
    assert result is not None
    assert result["type"] == "answer"
    assert result["short"] == "Да."


def test_json_with_braces_inside_string():
    # Внутри строки есть { и } — _extract_json не должен сломаться
    raw = (
        '{"type": "answer", '
        '"short": "Формула: {x} + {y}.", '
        '"explicit_detail": false}'
    )
    result = parse_response(raw)
    assert result is not None
    assert result["short"] == "Формула: {x} + {y}."


# ── Битый / пустой вход ────────────────────────────────────

def test_garbage_no_json():
    assert parse_response("просто текст, не JSON") is None


def test_broken_json():
    assert parse_response("{не json}") is None


def test_empty_string():
    assert parse_response("") is None


def test_none_input():
    assert parse_response(None) is None


# ── Неизвестный type ───────────────────────────────────────

def test_unknown_type_returns_none():
    raw = '{"type": "some_other_type", "text": "что-то"}'
    assert parse_response(raw) is None