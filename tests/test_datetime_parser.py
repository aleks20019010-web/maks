# tests/test_datetime_parser.py
# Тесты brain/datetime_parser.py.

from datetime import datetime, timedelta

from brain.datetime_parser import parse_when, parse_delay


def _close_to(actual: datetime, expected: datetime, tolerance_sec: float = 5.0) -> bool:
    return abs((actual - expected).total_seconds()) <= tolerance_sec


# ── parse_delay ────────────────────────────────────────────

def test_delay_10_minutes():
    result = parse_delay("через 10 минут позвонить маме")
    assert result is not None
    delay_min, clean = result
    assert delay_min == 10
    assert clean == "позвонить маме"


def test_delay_2_hours():
    result = parse_delay("через 2 часа проверить духовку")
    assert result is not None
    delay_min, clean = result
    assert delay_min == 120
    assert clean == "проверить духовку"


def test_delay_word_minute():
    result = parse_delay("через одну минуту выключить свет")
    assert result is not None
    delay_min, clean = result
    assert delay_min == 1
    assert clean == "выключить свет"


def test_delay_no_text():
    # «через 10 минут» без текста возвращает (10, '') для доуточнения текста
    result = parse_delay("через 10 минут")
    assert result == (10, "")


def test_delay_not_a_delay():
    assert parse_delay("напомни через час") is None


# ── parse_when: конкретная дата ────────────────────────────

def test_when_29_october_10_00():
    result = parse_when("29 октября в 10:00 УЗИ")
    assert result is not None
    due, clean = result
    assert due.month == 10
    assert due.day == 29
    assert due.hour == 10
    assert due.minute == 0
    assert clean == "УЗИ"


def test_when_23_october_nine_zero_zero():
    result = parse_when("двадцать третьего октября в девять ноль-ноль врач")
    assert result is not None
    due, clean = result
    assert due.month == 10
    assert due.day == 23
    assert due.hour == 9
    assert due.minute == 0
    assert clean == "врач"


def test_when_29_october_no_time_defaults_to_9():
    result = parse_when("29 октября встреча")
    assert result is not None
    due, clean = result
    assert due.month == 10
    assert due.day == 29
    assert due.hour == 9
    assert due.minute == 0
    assert clean == "встреча"


# ── parse_when: сегодня/завтра/послезавтра ─────────────────

def test_when_tomorrow_9():
    today = datetime.now().date()
    result = parse_when("завтра в 9:00 зарядка")
    assert result is not None
    due, clean = result
    assert due.date() == today + timedelta(days=1)
    assert due.hour == 9
    assert due.minute == 0
    assert clean == "зарядка"


def test_when_tomorrow_default_9():
    today = datetime.now().date()
    result = parse_when("завтра совещание")
    assert result is not None
    due, clean = result
    assert due.date() == today + timedelta(days=1)
    assert due.hour == 9
    assert due.minute == 0
    assert clean == "совещание"


def test_when_day_after_tomorrow():
    today = datetime.now().date()
    result = parse_when("послезавтра в 15:30 звонок")
    assert result is not None
    due, clean = result
    assert due.date() == today + timedelta(days=2)
    assert due.hour == 15
    assert due.minute == 30
    assert clean == "звонок"


# ── parse_when: только время (сегодня/завтра) ──────────────

def test_when_time_only_future_or_tomorrow():
    now = datetime.now()
    result = parse_when("в 23:59 проверить почту")
    assert result is not None
    due, clean = result
    assert due.hour == 23
    assert due.minute == 59
    assert due > now, f"due={due} должен быть в будущем (now={now})"
    assert clean == "проверить почту"


# ── parse_when: не распознано ──────────────────────────────

def test_when_not_recognized():
    assert parse_when("просто текст без даты") is None