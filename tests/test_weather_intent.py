# tests/test_weather_intent.py
# Тесты A5: parse_intent — «погода» vs «погода на завтра».
#
# Не мокаем fetch_weather. Проверяем только parse_intent — это контракт.
# _tomorrow_phrase_set — простой if/elif, тестируем отдельно (3 теста).

import pytest

from brain.skills.weather.intent import parse_intent
from brain.skills.weather.handlers import _tomorrow_phrase_set


# ── parse_intent: now ────────────────────────────────────────

def test_now_plain():
    assert parse_intent("погода") == "now"
    assert parse_intent("какая погода") == "now"
    assert parse_intent("что за окном") == "now"


def test_now_with_other_words():
    assert parse_intent("погода сегодня") == "now"
    assert parse_intent("дождь будет") == "now"


def test_now_empty():
    assert parse_intent("") == "now"
    assert parse_intent(None) == "now"


# ── parse_intent: tomorrow ───────────────────────────────────

def test_tomorrow_plain():
    assert parse_intent("погода завтра") == "tomorrow"
    assert parse_intent("завтра погода") == "tomorrow"


def test_tomorrow_na():
    assert parse_intent("погода на завтра") == "tomorrow"
    assert parse_intent("на завтра погода") == "tomorrow"


def test_tomorrow_adjective():
    assert parse_intent("завтрашняя погода") == "tomorrow"
    assert parse_intent("какая завтрашняя погода") == "tomorrow"


def test_tomorrow_not_poslezavtra():
    # «послезавтра» — не tomorrow, излишки
    assert parse_intent("погода послезавтра") == "now"


# ── _tomorrow_phrase_set ─────────────────────────────────────

def test_tomorrow_phrase_storm():
    v = {"precip": "storm", "temp_bucket": "cold", "wind": "calm"}
    assert _tomorrow_phrase_set(v) == "WEATHER_TOMORROW_STORM"


def test_tomorrow_phrase_rain_cold():
    v = {"precip": "rain", "temp_bucket": "cold", "wind": "calm"}
    assert _tomorrow_phrase_set(v) == "WEATHER_TOMORROW_COLD_RAIN"


def test_tomorrow_phrase_clear_warm():
    v = {"precip": "clear", "temp_bucket": "warm", "wind": "calm"}
    assert _tomorrow_phrase_set(v) == "WEATHER_TOMORROW_WARM_CLEAR"