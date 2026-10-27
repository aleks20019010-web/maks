# tests/test_time_bucket.py
# Тесты shared/time_bucket.py.
#
# Границы (10 тестов):
#   04:59:59 → night      05:00:00 → morning
#   11:59:59 → morning    12:00:00 → day
#   17:59:59 → day        18:00:00 → evening
#   22:59:59 → evening    23:00:00 → night
#   00:00:00 → night      00:11:00 → night (тот самый баг)
#
# ritual_for_bucket (4 теста):
#   morning → RITUAL_MORNING
#   evening → RITUAL_EVENING
#   night   → RITUAL_NIGHT
#   day     → None

from datetime import datetime

from shared.time_bucket import time_bucket, ritual_for_bucket


# ── Границы: 5:00 ──────────────────────────────────────────

def test_0459_is_night():
    assert time_bucket(datetime(2026, 10, 27, 4, 59, 59)) == "night"


def test_0500_is_morning():
    assert time_bucket(datetime(2026, 10, 27, 5, 0, 0)) == "morning"


# ── Границы: 12:00 ─────────────────────────────────────────

def test_1159_is_morning():
    assert time_bucket(datetime(2026, 10, 27, 11, 59, 59)) == "morning"


def test_1200_is_day():
    assert time_bucket(datetime(2026, 10, 27, 12, 0, 0)) == "day"


# ── Границы: 18:00 ─────────────────────────────────────────

def test_1759_is_day():
    assert time_bucket(datetime(2026, 10, 27, 17, 59, 59)) == "day"


def test_1800_is_evening():
    assert time_bucket(datetime(2026, 10, 27, 18, 0, 0)) == "evening"


# ── Границы: 23:00 ─────────────────────────────────────────

def test_2259_is_evening():
    assert time_bucket(datetime(2026, 10, 27, 22, 59, 59)) == "evening"


def test_2300_is_night():
    assert time_bucket(datetime(2026, 10, 27, 23, 0, 0)) == "night"


# ── Полночь и тот самый баг ────────────────────────────────

def test_0000_is_night():
    assert time_bucket(datetime(2026, 10, 27, 0, 0, 0)) == "night"


def test_0011_is_night_not_morning():
    # Регрессия: раньше в 00:11 Макс говорил «доброе утро».
    assert time_bucket(datetime(2026, 10, 27, 0, 11, 0)) == "night"


# ── ritual_for_bucket ──────────────────────────────────────

def test_ritual_morning():
    assert ritual_for_bucket("morning") == "RITUAL_MORNING"


def test_ritual_evening():
    assert ritual_for_bucket("evening") == "RITUAL_EVENING"


def test_ritual_night():
    assert ritual_for_bucket("night") == "RITUAL_NIGHT"


def test_ritual_day_is_none():
    assert ritual_for_bucket("day") is None