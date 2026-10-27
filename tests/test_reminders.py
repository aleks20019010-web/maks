# tests/test_reminders.py
# Тесты brain/reminders.py.
#
# ВАЖНО: тесты идут ДО переноса _fmt_due/_strip_reminder_trigger из router.py
# в reminders.py (п.5.15). Это страховка: перенёс → тесты зелёные → не сломал.
#
# Каждый тест изолирован: фикстура isolated_reminders подменяет
# reminders.REMINDERS_FILE на tmp_path и сбрасывает reminders._cache.
#
# FIX (день 28): добавлены тесты на reminders.fmt_due и
# reminders.strip_reminder_trigger — они переехали сюда из router.py.

from datetime import datetime

import pytest

from brain import reminders


@pytest.fixture
def isolated_reminders(tmp_path, monkeypatch):
    """Изолировать reminders: свой файл, чистый кэш."""
    f = tmp_path / "reminders.json"
    monkeypatch.setattr(reminders, "REMINDERS_FILE", str(f))
    monkeypatch.setattr(reminders, "_cache", None)
    monkeypatch.setattr(reminders, "_reminded_slots", set())
    yield f


# ── get_reminder_times: kind="delay" ───────────────────────

def test_delay_one_slot():
    due = datetime(2026, 10, 29, 10, 0)
    slots = reminders.get_reminder_times(due, kind="delay")
    assert slots == [due]


# ── get_reminder_times: kind="date", до 12:00 ──────────────

def test_date_before_noon_slots_prev_day():
    due = datetime(2026, 10, 29, 10, 0)
    slots = reminders.get_reminder_times(due, kind="date")
    assert slots == [
        datetime(2026, 10, 28, 21, 0),
        datetime(2026, 10, 28, 22, 0),
    ]


# ── get_reminder_times: kind="date", после 12:00 ───────────

def test_date_after_noon_slots_same_day():
    due = datetime(2026, 10, 29, 15, 30)
    slots = reminders.get_reminder_times(due, kind="date")
    assert slots == [
        datetime(2026, 10, 29, 10, 0),
        datetime(2026, 10, 29, 11, 0),
    ]


# ── add: delay ─────────────────────────────────────────────

def test_add_delay_creates_entry(isolated_reminders):
    r = reminders.add("позвонить маме", 10)
    assert r is not None
    assert r["text"] == "позвонить маме"
    assert r["kind"] == "delay"
    assert r["done"] is False
    due = datetime.fromisoformat(r["due_at"])
    delta = (due - datetime.now()).total_seconds()
    assert 9 * 60 < delta < 11 * 60, f"due_at={r['due_at']}, delta={delta}s"


def test_add_empty_text_returns_none(isolated_reminders):
    assert reminders.add("", 10) is None
    assert reminders.add("   ", 10) is None


def test_add_non_positive_delay_returns_none(isolated_reminders):
    assert reminders.add("что-то", 0) is None
    assert reminders.add("что-то", -5) is None


# ── add_at: date ───────────────────────────────────────────

def test_add_at_creates_date_entry(isolated_reminders):
    due = datetime(2026, 10, 29, 15, 30)
    r = reminders.add_at("УЗИ", due)
    assert r is not None
    assert r["text"] == "УЗИ"
    assert r["kind"] == "date"
    assert r["due_at"] == "2026-10-29T15:30:00"


# ── find_by_text / mark_done_by_text ───────────────────────

def test_find_by_text_case_insensitive(isolated_reminders):
    reminders.add_at("Позвонить Маме", datetime(2026, 10, 29, 15, 30))
    found = reminders.find_by_text("маме")
    assert found is not None
    assert found["text"] == "Позвонить Маме"


def test_mark_done_by_text(isolated_reminders):
    reminders.add_at("УЗИ", datetime(2026, 10, 29, 15, 30))
    assert reminders.mark_done_by_text("узи") is True
    assert reminders.find_by_text("узи") is None


# ── get_pending ────────────────────────────────────────────

def test_get_pending_excludes_done(isolated_reminders):
    r1 = reminders.add_at("дело 1", datetime(2026, 10, 29, 10, 0))
    r2 = reminders.add_at("дело 2", datetime(2026, 10, 29, 15, 0))
    reminders.mark_done(r1["id"])

    pending = reminders.get_pending()
    assert len(pending) == 1
    assert pending[0]["text"] == "дело 2"


# ── fmt_due (перенесено из router.py, 5.15) ────────────────

def test_fmt_due_october():
    assert reminders.fmt_due(datetime(2026, 10, 29, 10, 0)) == "29 октября в 10:00"


def test_fmt_due_may():
    assert reminders.fmt_due(datetime(2026, 5, 9, 18, 30)) == "9 мая в 18:30"


# ── strip_reminder_trigger (перенесено из router.py, 5.15) ─

def test_strip_trigger_napomni():
    assert reminders.strip_reminder_trigger("напомни купить хлеб") == "купить хлеб"


def test_strip_trigger_zapomni():
    assert reminders.strip_reminder_trigger("запомни 29 октября в 10:00 УЗИ") == "29 октября в 10:00 УЗИ"


def test_strip_trigger_none():
    assert reminders.strip_reminder_trigger("включи музыку") is None