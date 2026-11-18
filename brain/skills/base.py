# brain/skills/base.py
# Базовый класс навыка и глобальный реестр.

import threading
from shared.log import log


class Skill:
    """Базовый навык. Наследники переопределяют name, priority и методы."""

    name = "base"
    priority = 50  # меньше = раньше проверяется
    accepts_command = False  # True -> router передаёт Command вместо str

    def can_handle(self, text: str) -> bool:
        """Быстрая проверка: умею ли я это обработать?"""
        return False

    def handle(self, text) -> str | None:
        """Обработать команду (str или Command).

        Навыки отправляют озвучку самостоятельно через respond_and_say()
        и возвращают None (router возвращаемое значение не озвучивает).
        """
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
    with _registry_lock:
        return list(_registry)


def find_handler(text: str) -> Skill | None:
    """Найти первый навык, который может обработать текст."""
    for skill in get_all():
        try:
            if skill.can_handle(text):
                return skill
        except Exception as e:
            log(f"Навык {skill.name}.can_handle упал: {e}", tag="BRAIN", level="WARN")
    return None


def find_by_name(name: str) -> Skill | None:
    """Найти навык по имени. None — если нет."""
    if not name:
        return None
    with _registry_lock:
        for skill in _registry:
            if skill.name == name:
                return skill
    return None


def get_by_name(name: str) -> Skill | None:
    """Алиас find_by_name."""
    return find_by_name(name)