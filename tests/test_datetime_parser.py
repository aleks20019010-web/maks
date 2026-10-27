# tests/test_datetime_parser.py
# Тесты brain/datetime_parser.py.
#
# Не мокаем datetime.now() — тесты относительные:
#   - parse_delay: проверяем, что due ≈ now + delay (с допуском).
#   - parse_when: проверяем день/месяц/час/минуту, не проверяем год.
#   - «сегодня/завтра»: проверяем, что due.date() == today/tomorrow.
#
# Так тесты не падают от смены фазы луны и от того, что запустили в 23:59.

from datetime import datetime, timedelta

from brain.datetime_parser import parse_when, parse_delay


def _close_to(actual: datetime, expected: datetime, tolerance_sec: float = 5.0) -> bool:
    """Сравнить два datetime с допуском (на случай, если между now и due
    пролетела пара секунд)."""
    return abs((actual - expected).total_seconds()) <= tolerance_sec


# ── parse_delay ────────────────────────────────────────────

def test_delay_10_minutes():
    now = datetime.now()
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
    # «через одну минуту» — числительное словом
    result = parse_delay("через одну минуту выключить свет")
    assert result is not None
    delay_min, clean = result
    assert delay_min == 1
    assert clean == "выключить свет"


def test_delay_no_text():
    # «через 10 минут» без текста — не парсится (нужен текст после)
    assert parse_delay("через 10 минут") is None


def test_delay_not_a_delay():
    # «напомни через час» — «час» без числа, не парсится
    assert parse_delay("напомни через час") is None


# ── parse_when: конкретная дата ────────────────────────────

def test_when_29_october_10_00():
    result = parse_when("29 октября в 10:00 УЗИ")
    assert result is not None
    due, clean = result
    # Год не проверяем — если 29 октября уже прошло, парсер возьмёт следующий
    assert due.month == 10
    assert due.day == 29
    assert due.hour == 10
    assert due.minute == 0
    assert clean == "УЗИ"


def test_when_23_october_nine_zero_zero():
    # «двадцать третьего октября в девять ноль-ноль» — словами
    result = parse_when("двадцать третьего октября в девять ноль-ноль врач")
    assert result is not None
    due, clean = result
    assert due.month == 10
    assert due.day == 23
    assert due.hour == 9
    assert due.minute == 0
    assert clean == "врач"


def test_when_29_october_no_time_defaults_to_9():
    # Без времени — 09:00 по умолчанию
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
    # «завтра» без времени — 09:00
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
    # «в 23:59» — если сейчас до 23:59, то сегодня; иначе завтра.
    # Проверяем: due > now (в будущем) и due.hour == 23, due.minute == 59.
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
    # «просто текст» — не дата и не время
    assert parse_when("просто текст без даты") is None