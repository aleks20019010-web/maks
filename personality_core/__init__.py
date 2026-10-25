"""
personality_core — Personality Core Макса.

Публичный API:
    get(base_dir) — вернуть синглтон PersonalityEngine.

Совместимость:
    На время миграции класс PersonalityEngine живёт здесь,
    а навыки используют personality.pick_full(...).
"""

from typing import Optional


_engine = None


def get(base_dir: Optional[str] = None):
    """Вернуть синглтон PersonalityEngine.

    base_dir — путь к D:\\Jarvis (если первый вызов).
    """
    global _engine
    if _engine is None:
        if base_dir is None:
            import os
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        from personality_core.engine import PersonalityEngine
        _engine = PersonalityEngine(base_dir)
    return _engine


# Прямой реэкспорт для `from personality_core import PersonalityEngine`
from personality_core.engine import PersonalityEngine  # noqa: E402