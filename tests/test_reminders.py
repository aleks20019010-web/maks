# tests/test_reminders.py
# Тесты brain/reminders.py.
#
# ВАЖНО: тесты идут ДО переноса _fmt_due/_strip_reminder_trigger из router.py
# в reminders.py (п.5.15). Это страховка: перенёс → тесты зелёные → не сломал.
#
# Каждый тест изолирован: фикстура isolated_reminders подменяет
# reminders.REMINDERS_FILE на tmp_path и сбрасывает reminders._cache.
# Без этого второй тест увидит записи первого.

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
    # Ничего не чистим — tmp_path сам удалится


# ── get_reminder_times: kind="delay" ───────────────────────

def test_delay_one_slot():
    due = datetime(2026, 10, 29, 10, 0)
    slots = reminders.get_reminder_times(due, kind="delay")
    assert slots == [due]


# ── get_reminder_times: kind="date", до 12:00 ──────────────

def test_date_before_noon_slots_prev_day():
    # Событие в 10:00 → слоты 21:00 и 22:00 ПРЕДЫДУЩЕГО дня
    due = datetime(2026, 10, 29, 10, 0)
    slots = reminders.get_reminder_times(due, kind="date")
    assert slots == [
        datetime(2026, 10, 28, 21, 0),
        datetime(2026, 10, 28, 22, 0),
    ]


# ── get_reminder_times: kind="date", после 12:00 ───────────

def test_date_after_noon_slots_same_day():
    # Событие в 15:30 → слоты 10:00 и 11:00 ЭТОГО дня
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
    # due_at примерно now + 10 минут
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
    # После mark_done — find_by_text не найдёт (done=True)
    assert reminders.find_by_text("узи") is None


# ── get_pending ────────────────────────────────────────────

def test_get_pending_excludes_done(isolated_reminders):
    r1 = reminders.add_at("дело 1", datetime(2026, 10, 29, 10, 0))
    r2 = reminders.add_at("дело 2", datetime(2026, 10, 29, 15, 0))
    reminders.mark_done(r1["id"])

    pending = reminders.get_pending()
    assert len(pending) == 1
    assert pending[0]["text"] == "дело 2"