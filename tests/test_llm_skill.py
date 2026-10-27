# tests/test_llm_skill.py
# Тесты brain/skills/llm/skill.py: _engine через модуль _helpers.
#
# Регрессия аудита 1.2: раньше `from brain.skills._helpers import _engine`
# делал снимок значения. Если llm_worker стартует до set_engine —
# _engine навсегда None. Тест проверяет, что _helpers._engine читается
# всегда актуально.

import pytest

from brain.skills import _helpers


@pytest.fixture(autouse=True)
def reset_engine():
    """Перед каждым тестом — _engine = None. После — тоже."""
    old = _helpers._engine
    _helpers.set_engine(None)
    yield
    _helpers.set_engine(old)


def test_engine_is_none_by_default():
    assert _helpers._engine is None


def test_set_engine_none_works():
    _helpers.set_engine(None)
    assert _helpers._engine is None


def test_set_engine_sets_value():
    class FakeEngine:
        pass

    fake = FakeEngine()
    _helpers.set_engine(fake)
    assert _helpers._engine is fake


def test_engine_is_not_snapshot():
    """Главный тест аудита 1.2: after set_engine, _helpers._engine обновился.

    Если бы skill.py делал `from _helpers import _engine`, снимок остался
    бы старым. Тест проверяет, что модуль читает актуальное значение.
    """
    class Fake1:
        pass

    class Fake2:
        pass

    _helpers.set_engine(Fake1())
    first = _helpers._engine
    _helpers.set_engine(Fake2())
    second = _helpers._engine

    assert first is not second
    assert type(first).__name__ == "Fake1"
    assert type(second).__name__ == "Fake2"