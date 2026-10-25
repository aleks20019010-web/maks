# personality_core/identity.py
# Манифест Макса: «кто я», «зачем», «что важно», «чего не делаю».
# Загружается из config/identity.json. Недостающие поля — дефолты.
#
# Живёт ОТДЕЛЬНО от Context — статичен.
# Context получает ссылку — правила читают через ctx.identity.

import os
import json
from dataclasses import dataclass, field


# ============================================================
# ДЕФОЛТЫ
# ============================================================
DEFAULT_IDENTITY = {
    "name": "Макс",
    "role": "дворецкий",
    "purpose": "Служу вам.",
    "origin": "",
    "values": [],
    "limits": [],
}


# ============================================================
# IDENTITY
# ============================================================
@dataclass
class Identity:
    """Манифест Макса."""
    name: str = "Макс"
    role: str = "дворецкий"
    purpose: str = "Служу вам."
    origin: str = ""
    values: list = field(default_factory=list)
    limits: list = field(default_factory=list)

    # ========================================================
    # ЧТЕНИЕ
    # ========================================================
    def is_jarvis(self) -> bool:
        return self.name == "Макс"

    def summary(self) -> str:
        """Короткая строка для логов."""
        return f"{self.name} ({self.role})"

    def about(self) -> str:
        """Полный манифест — для WHO_ARE_YOU."""
        parts = [f"Я — {self.name}. {self.role.capitalize()}."]
        if self.purpose:
            parts.append(self.purpose)
        if self.origin:
            parts.append(self.origin)
        return " ".join(parts)

    def can(self) -> str:
        """Что умею — для WHAT_CAN_YOU_DO."""
        if not self.values:
            return "Умею слушать. И действовать."
        return "Умею: " + ", ".join(self.values).lower() + "."

    def wont(self) -> str:
        """Чего не делаю — для объяснений."""
        if not self.limits:
            return ""
        return "Не буду: " + ", ".join(self.limits).lower() + "."


# ============================================================
# ЗАГРУЗКА
# ============================================================
def _load_from_json(base_dir: str) -> dict:
    path = os.path.join(base_dir, "config", "identity.json")
    if not os.path.exists(path):
        print(f"⚠ identity: не найден {path}, использую дефолты")
        return {}
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            print(f"⚠ identity: {path} — не dict, использую дефолты")
            return {}
        return data
    except Exception as e:
        print(f"⚠ identity: ошибка чтения {path}: {e}")
        return {}


# ============================================================
# СИНГЛТОН
# ============================================================
_identity: Identity | None = None


def get(base_dir: str | None = None) -> Identity:
    """Вернуть синглтон Identity."""
    global _identity
    if _identity is None:
        data = {}
        if base_dir:
            data = _load_from_json(base_dir)
        merged = {}
        for key, default in DEFAULT_IDENTITY.items():
            merged[key] = data.get(key, default)
        _identity = Identity(**merged)
        print(f"🎭 identity: {_identity.summary()}")
    return _identity


def reset(base_dir: str | None = None):
    """Сброс (для тестов)."""
    global _identity
    _identity = None
    if base_dir:
        get(base_dir)