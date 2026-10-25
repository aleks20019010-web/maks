# brain/skills/base.py
# Базовый класс навыка и глобальный реестр.
# Навык регистрируется, роутер идёт по реестру и вызывает первый подходящий.

import threading


class Skill:
    """Базовый навык. Наследники переопределяют name, priority и методы."""

    name = "base"
    priority = 50  # меньше = раньше проверяется

    def can_handle(self, text: str) -> bool:
        """Быстрая проверка: умею ли я это обработать?"""
        return False

    def handle(self, text: str) -> str | None:
        """Обработать команду и вернуть текст для TTS (или None)."""
        return None


# ── Реестр ────────────────────────────────────────────────────
_registry: list[Skill] = []
_registry_lock = threading.Lock()


def register(skill: Skill):
    """Зарегистрировать навык в реестре."""
    with _registry_lock:
        _registry.append(skill)
        _registry.sort(key=lambda s: s.priority)


def get_all() -> list[Skill]:
    """Вернуть список всех навыков, отсортированный по приоритету."""
    return list(_registry)


def find_handler(text: str) -> Skill | None:
    """Найти первый навык, который может обработать текст."""
    for skill in _registry:
        try:
            if skill.can_handle(text):
                return skill
        except Exception as e:
            print(f"⚠ Навык {skill.name}.can_handle упал: {e}")
    return None