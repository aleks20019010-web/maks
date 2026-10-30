# brain/skills/base.py
# Базовый класс навыка и глобальный реестр.
# Навык регистрируется, роутер идёт по реестру и вызывает первый подходящий.
#
# FIX (NLU v2): добавлены find_by_name / get_by_name.
#   Раньше router.py вызывал skills_base.find_by_name(skill_name), но функции
#   в этом модуле не было — NLU-путь падал с AttributeError на первом же
#   NLU-хите. Теперь функция есть. find_by_name — для одного скила по имени,
#   get_by_name — алиас (на случай, если кто-то зовёт get_by_name).

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


def find_by_name(name: str) -> Skill | None:
    """Найти навык по имени. None — если нет.

    Используется router.py (NLU-путь) и skill_resolver.py.
    """
    if not name:
        return None
    with _registry_lock:
        for skill in _registry:
            if skill.name == name:
                return skill
    return None


def get_by_name(name: str) -> Skill | None:
    """Алиас find_by_name. На случай, если кто-то зовёт get_by_name."""
    return find_by_name(name)