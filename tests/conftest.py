# tests/conftest.py
# Фикстуры для тестов: временная IPC-шина, временная БД, чистый memory.
#
# FIX: BASE_DIR = Path(__file__).parent.parent, не хардкод D:\Maks.
# Тесты работают, где бы ни лежал проект.

import os
import sys
import shutil
from pathlib import Path

import pytest

# Корень проекта — родитель папки tests/
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


@pytest.fixture
def tmp_data_dir(tmp_path):
    r"""Временная папка для data/ (вместо <BASE_DIR>\data)."""
    data = tmp_path / "data"
    data.mkdir()
    yield str(data)
    shutil.rmtree(str(tmp_path), ignore_errors=True)


@pytest.fixture
def tmp_memory_db(tmp_path):
    """Временная БД памяти (пустая, со схемой)."""
    db_path = str(tmp_path / "memory.db")
    from memory.storage import Storage
    storage = Storage(db_path)
    yield storage
    storage.close()


@pytest.fixture
def clean_relationship(tmp_path, monkeypatch):
    """Подменить relationship.json на временный."""
    rel_path = str(tmp_path / "relationship.json")
    from brain import relationship
    monkeypatch.setattr(relationship, "RELATIONSHIP_FILE", rel_path)
    monkeypatch.setattr(relationship, "_cache", None)
    yield rel_path
    if os.path.exists(rel_path):
        os.remove(rel_path)