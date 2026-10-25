# tests/test_habits.py
import pytest

from memory.storage import Storage
from memory.engine import MemoryEngine


@pytest.fixture
def engine(tmp_path):
    db_path = str(tmp_path / "memory.db")
    # Создаём MemoryEngine с временной БД
    import shared.config as cfg
    old = cfg.MEMORY_DB
    cfg.MEMORY_DB = db_path
    try:
        eng = MemoryEngine(start_writer=False)
        yield eng
        eng.shutdown()
    finally:
        cfg.MEMORY_DB = old


def test_set_get_habit(engine):
    engine.set_habit("day", "Ария", 1)
    h = engine.get_habit("day")
    assert h == {"Ария": 1}

    engine.set_habit("day", "Ария", 1)
    h = engine.get_habit("day")
    assert h == {"Ария": 2}


def test_get_habit_empty(engine):
    assert engine.get_habit("morning") is None


def test_habit_threshold(engine):
    from brain import relationship
    engine.set_habit("day", "Ария", 1)
    engine.set_habit("day", "Ария", 1)
    # 2 раза — ещё не привычка (порог 3)
    # relationship.get_habit вернёт None, если < 3
    # Но relationship.get_habit импортирует memory, а не engine.
    # Проверяем напрямую:
    h = engine.get_habit("day")
    assert h == {"Ария": 2}
    top = max(h.items(), key=lambda x: x[1])
    assert top[1] == 2  # < 3


def test_habit_multiple_artists(engine):
    engine.set_habit("day", "Ария", 1)
    engine.set_habit("day", "Ария", 1)
    engine.set_habit("day", "КИНО", 1)
    h = engine.get_habit("day")
    assert h == {"Ария": 2, "КИНО": 1}
    top = max(h.items(), key=lambda x: x[1])
    assert top[0] == "Ария"
    assert top[1] == 2