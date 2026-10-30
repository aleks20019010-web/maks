# tests/test_relationship.py
# A2-фикс (день 30): weather_music без TTL, was_asked_today.
#
# Проверяем:
#   - set_weather_music("rain", "calm") → weather_known("rain") True,
#     get_weather_music("rain") == "calm".
#   - TTL убран: если asked_at 8 дней назад — weather_known всё равно True.
#   - mark_asked_today / was_asked_today — сегодня True, завтра False
#     (мокаем date.today через monkeypatch).

import json
from datetime import date, datetime, timedelta

import pytest

from brain import relationship


@pytest.fixture
def isolated_relationship(tmp_path, monkeypatch):
    """Изолировать relationship.json."""
    f = tmp_path / "relationship.json"
    monkeypatch.setattr(relationship, "RELATIONSHIP_FILE", str(f))
    monkeypatch.setattr(relationship, "_cache", None)
    yield f
    if f.exists():
        f.unlink()


def test_set_get_weather_music(isolated_relationship):
    relationship.set_weather_music("rain", "calm")
    assert relationship.get_weather_music("rain") == "calm"
    assert relationship.weather_known("rain") is True


def test_weather_music_no_ttl(isolated_relationship):
    """TTL убран: 8 дней назад — weather_known всё равно True."""
    relationship.set_weather_music("rain", "calm")
    # Подменяем asked_at в файле на 8 дней назад
    with open(isolated_relationship, "r", encoding="utf-8") as f:
        data = json.load(f)
    old = (datetime.now() - timedelta(days=8)).isoformat(timespec="seconds")
    data["weather_music"]["rain"]["asked_at"] = old
    with open(isolated_relationship, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)

    # Сброс кэша, чтобы прочитать свежий файл
    relationship._cache = None
    assert relationship.get_weather_music("rain") == "calm"
    assert relationship.weather_known("rain") is True


def test_was_asked_today(isolated_relationship, monkeypatch):
    """mark_asked_today → was_asked_today True. Завтра — False."""
    relationship.mark_asked_today("weather_rain")
    assert relationship.was_asked_today("weather_rain") is True

    # Мокаем date.today → завтра
    tomorrow = date.today() + timedelta(days=1)
    class FakeDate(date):
        @classmethod
        def today(cls):
            return tomorrow
    monkeypatch.setattr(relationship, "date", FakeDate)

    # Перечитываем кэш (asked_today хранит дату строкой)
    assert relationship.was_asked_today("weather_rain") is False