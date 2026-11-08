# personality_core/identity.py
# Манифест Макса: «кто я», «зачем», «что важно», «чего не делаю».
# Загружается из config/identity.json. Недостающие поля — дефолты.
#
# ── FIX (этап 0.5): print → log(tag="PC") ────────────────────────
#   🎭 identity (на импорте) — DEBUG. ⚠ identity — WARN.

import os
import json
from dataclasses import dataclass, field

from shared.log import log


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

    def is_jarvis(self) -> bool:
        return self.name == "Макс"

    def summary(self) -> str:
        return f"{self.name} ({self.role})"

    def about(self) -> str:
        parts = [f"Я — {self.name}. {self.role.capitalize()}."]
        if self.purpose:
            parts.append(self.purpose)
        if self.origin:
            parts.append(self.origin)
        return " ".join(parts)

    def can(self) -> str:
        if not self.values:
            return "Умею слушать. И действовать."
        return "Умею: " + ", ".join(self.values).lower() + "."

    def wont(self) -> str:
        if not self.limits:
            return ""
        return "Не буду: " + ", ".join(self.limits).lower() + "."


# ============================================================
# ЗАГРУЗКА
# ============================================================
def _load_from_json(base_dir: str) -> dict:
    path = os.path.join(base_dir, "config", "identity.json")
    if not os.path.exists(path):
        log(f"identity: не найден {path}, использую дефолты",
            tag="PC", level="DEBUG")
        return {}
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            log(f"identity: {path} — не dict, использую дефолты",
                tag="PC", level="WARN")
            return {}
        return data
    except Exception as e:
        log(f"identity: ошибка чтения {path}: {e}",
            tag="PC", level="WARN")
        return {}


# ============================================================
# СИНГЛТОН
# ============================================================
_identity: Identity | None = None


def get(base_dir: str | None = None) -> Identity:
    global _identity
    if _identity is None:
        data = {}
        if base_dir:
            data = _load_from_json(base_dir)
        merged = {}
        for key, default in DEFAULT_IDENTITY.items():
            merged[key] = data.get(key, default)
        _identity = Identity(**merged)
        log(f"identity: {_identity.summary()}", tag="PC", level="DEBUG")
    return _identity


def reset(base_dir: str | None = None):
    global _identity
    _identity = None
    if base_dir:
        get(base_dir)