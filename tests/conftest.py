# tests/conftest.py
# Фикстуры для тестов: временная IPC-шина, временная БД, чистый memory.

import os
import sys
import shutil
import pytest

# Патч sys.path, чтобы импортировать из D:\Maks
BASE_DIR = r'D:\Maks'
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)


@pytest.fixture
def tmp_data_dir(tmp_path):
    r"""Временная папка для data/ (вместо D:\Maks\data)."""
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