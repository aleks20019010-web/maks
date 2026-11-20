# tests/test_reminders.py
# Тесты brain/reminders.py (с актуальной логикой слотов: 22:00/23:00 накануне + за 1ч / за 5м).

from datetime import datetime, timedelta
import pytest

from brain import reminders


@pytest.fixture
def isolated_reminders(tmp_path, monkeypatch):
    f = tmp_path / "reminders.json"
    monkeypatch.setattr(reminders, "REMINDERS_FILE", str(f))
    monkeypatch.setattr(reminders, "_cache", None)
    monkeypatch.setattr(reminders, "_reminded_slots", set())
    yield f


def test_delay_one_slot():
    due = datetime(2026, 11, 25, 10, 0)
    slots = reminders.get_reminder_times(due, kind="delay")
    assert slots == [due]


def test_date_next_day_slots_evening_and_hour_before():
    ref_now = datetime(2026, 11, 20, 12, 0)
    due = datetime(2026, 11, 21, 10, 0)
    slots = reminders.get_reminder_times(due, kind="date", ref_now=ref_now)
    assert slots == [
        datetime(2026, 11, 20, 22, 0),
        datetime(2026, 11, 20, 23, 0),
        datetime(2026, 11, 21, 9, 0),
    ]


def test_date_same_day_slots_hour_before():
    ref_now = datetime(2026, 11, 20, 12, 0)
    due = datetime(2026, 11, 20, 15, 30)
    slots = reminders.get_reminder_times(due, kind="date", ref_now=ref_now)
    assert slots == [
        datetime(2026, 11, 20, 14, 30),
    ]


def test_add_delay_creates_entry(isolated_reminders):
    r = reminders.add("позвонить маме", 10)
    assert r is not None
    assert r["text"] == "позвонить маме"
    assert r["kind"] == "delay"
    assert r["done"] is False
    due = datetime.fromisoformat(r["due_at"])
    delta = (due - datetime.now()).total_seconds()
    assert 9 * 60 < delta < 11 * 60


def test_add_empty_text_returns_none(isolated_reminders):
    assert reminders.add("", 10) is None
    assert reminders.add("   ", 10) is None


def test_add_non_positive_delay_returns_none(isolated_reminders):
    assert reminders.add("что-то", 0) is None
    assert reminders.add("что-то", -5) is None


def test_add_at_creates_date_entry(isolated_reminders):
    due = datetime.now() + timedelta(days=2)
    due = due.replace(hour=15, minute=30, second=0, microsecond=0)
    r = reminders.add_at("УЗИ", due)
    assert r is not None
    assert r["text"] == "УЗИ"
    assert r["kind"] == "date"
    assert r["due_at"] == due.isoformat(timespec="seconds")


def test_find_by_text_case_insensitive(isolated_reminders):
    due = datetime.now() + timedelta(days=2)
    reminders.add_at("Позвонить Маме", due)
    found = reminders.find_by_text("маме")
    assert found is not None
    assert found["text"] == "Позвонить Маме"


def test_mark_done_by_text(isolated_reminders):
    due = datetime.now() + timedelta(days=2)
    reminders.add_at("УЗИ", due)
    assert reminders.mark_done_by_text("узи") is True
    assert reminders.find_by_text("узи") is None


def test_get_pending_excludes_done(isolated_reminders):
    due1 = datetime.now() + timedelta(days=2)
    due2 = datetime.now() + timedelta(days=3)
    r1 = reminders.add_at("дело 1", due1)
    r2 = reminders.add_at("дело 2", due2)
    reminders.mark_done(r1["id"])

    pending = reminders.get_pending()
    assert len(pending) == 1
    assert pending[0]["text"] == "дело 2"


def test_fmt_due_october():
    assert reminders.fmt_due(datetime(2026, 10, 29, 10, 0)) == "29 октября в 10:00"


def test_fmt_due_may():
    assert reminders.fmt_due(datetime(2026, 5, 9, 18, 30)) == "9 мая в 18:30"


def test_strip_trigger_napomni():
    assert reminders.strip_reminder_trigger("напомни купить хлеб") == "купить хлеб"


def test_strip_trigger_zapomni():
    assert reminders.strip_reminder_trigger("запомни 29 октября в 10:00 УЗИ") == "29 октября в 10:00 УЗИ"


def test_strip_trigger_none():
    assert reminders.strip_reminder_trigger("включи музыку") is None