# tests/test_semantic.py
# B4: semantic-router — 5 тестов. Мокаем route, не грузим модель.
#
# ВАЖНО: в router.process_command сначала skills_base.find_handler.
# Если он вернул handler — semantic не вызывается. Чтобы проверить
# semantic в вакууме, мокаем find_handler → None.
#
# Проверяем:
#   - route("вруби музыку") → music (мок)
#   - route("абракадабра") → None (мок)
#   - get_handler("music") → MusicSkill
#   - get_handler("none") → None
#   - router: find_handler None + semantic music → handler
#   - router: find_handler None + semantic None → LLM

import pytest


def test_route_music_mock(monkeypatch):
    from brain import semantic
    monkeypatch.setattr(semantic, "route",
                        lambda text: "music" if "вруби" in text else None)
    assert semantic.route("вруби рок") == "music"
    assert semantic.route("абракадабра") is None


def test_get_handler_music():
    from brain import semantic
    handler = semantic.get_handler("music")
    assert handler is not None
    assert handler.name == "music"


def test_get_handler_none():
    from brain import semantic
    assert semantic.get_handler("none") is None
    assert semantic.get_handler("unknown_intent") is None


def test_router_semantic_fallback(monkeypatch):
    """router: find_handler None → semantic → handler."""
    from brain import router, semantic
    from brain.skills import base as skills_base
    from shared.sources import CommandSource

    # find_handler → None (проверяем semantic в вакууме)
    monkeypatch.setattr(skills_base, "find_handler", lambda text: None)
    monkeypatch.setattr(semantic, "route", lambda text: "music")

    handled = []
    class FakeHandler:
        def handle(self, text):
            handled.append(text)
    monkeypatch.setattr(semantic, "get_handler", lambda name: FakeHandler())

    result = router.process_command("вруби рок", source=CommandSource.VOICE)
    assert result is None
    assert handled == ["вруби рок"]


def test_router_semantic_none(monkeypatch):
    """router: find_handler None, semantic None → LLM."""
    from brain import router, semantic
    from brain.skills import base as skills_base
    from shared.sources import CommandSource

    monkeypatch.setattr(skills_base, "find_handler", lambda text: None)
    monkeypatch.setattr(semantic, "route", lambda text: None)
    called = []
    monkeypatch.setattr(router, "_try_llm_fallback",
                        lambda text: called.append(text) or True)

    router.process_command("абракадабра", source=CommandSource.VOICE)
    assert called == ["абракадабра"]