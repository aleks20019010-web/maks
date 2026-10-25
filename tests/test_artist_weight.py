# tests/test_artist_weight.py
import pytest

from memory.engine import MemoryEngine


@pytest.fixture
def engine(tmp_path):
    db_path = str(tmp_path / "memory.db")
    import shared.config as cfg
    old = cfg.MEMORY_DB
    cfg.MEMORY_DB = db_path
    try:
        eng = MemoryEngine(start_writer=False)
        # Создаём артиста с весом 100
        eng.storage.execute("""
            INSERT OR REPLACE INTO artist_stats(artist, weight)
            VALUES (?, ?)
        """, ("Ария", 100.0))
        yield eng
        eng.shutdown()
    finally:
        cfg.MEMORY_DB = old


def test_penalize(engine):
    engine.penalize_artist("Ария", -5.0)
    w = engine.get_artist_weight("Ария")
    assert w == 95.0


def test_penalize_multiple(engine):
    for _ in range(3):
        engine.penalize_artist("Ария", -5.0)
    w = engine.get_artist_weight("Ария")
    assert w == 85.0


def test_reward(engine):
    engine.reward_artist("Ария", 2.0)
    w = engine.get_artist_weight("Ария")
    assert w == 102.0


def test_penalize_unknown_artist(engine):
    # Нет артиста — UPDATE не найдёт, вес не изменится
    engine.penalize_artist("Неизвестный", -5.0)
    w = engine.get_artist_weight("Неизвестный")
    assert w == 0.0


def test_penalize_empty(engine):
    engine.penalize_artist("", -5.0)  # не падает
    engine.penalize_artist(None, -5.0)