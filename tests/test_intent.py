# tests/test_intent.py
# Этап 3: IntentResolver. 4 теста. Мокаем resolve, не грузим модель.
#
# Порог проверяется живьём на реальных фразах (калибровка), не в моке.

import pytest

from brain.nlu.types import Intent


def test_intent_music_mock(monkeypatch):
    from brain.nlu import intent as intent_mod
    monkeypatch.setattr(intent_mod, "resolve",
                        lambda text: (Intent.MUSIC_PLAY, 0.94) if "арию" in text else (Intent.UNKNOWN, 0.1))
    assert intent_mod.resolve("навали арию")[0] == Intent.MUSIC_PLAY
    assert intent_mod.resolve("абракадабра")[0] == Intent.UNKNOWN


def test_intent_weather_mock(monkeypatch):
    from brain.nlu import intent as intent_mod
    monkeypatch.setattr(intent_mod, "resolve",
                        lambda text: (Intent.WEATHER_QUERY, 0.92))
    i, c = intent_mod.resolve("что с погодой")
    assert i == Intent.WEATHER_QUERY
    assert c >= 0.9


def test_intent_system_mock(monkeypatch):
    from brain.nlu import intent as intent_mod
    monkeypatch.setattr(intent_mod, "resolve",
                        lambda text: (Intent.SYSTEM_STATUS, 0.88))
    assert intent_mod.resolve("статус")[0] == Intent.SYSTEM_STATUS


def test_intent_smalltalk_mock(monkeypatch):
    from brain.nlu import intent as intent_mod
    monkeypatch.setattr(intent_mod, "resolve",
                        lambda text: (Intent.SMALLTALK, 0.91))
    assert intent_mod.resolve("привет")[0] == Intent.SMALLTALK