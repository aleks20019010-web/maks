# memory/__init__.py
# Память Макса — опыт взаимодействия с пользователем.
# Не логи, а смысловая история: запросы, прослушивания, реакции, предпочтения.

from memory.engine import MemoryEngine

# Глобальный синглтон
memory = MemoryEngine()


__all__ = ['memory', 'MemoryEngine']