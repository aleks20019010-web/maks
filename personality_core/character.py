# personality_core/character.py
# Характер Макса: параметры стиля, юмора, формальности.
# Загружается из config/personality.json. Недостающие поля — дефолты.
#
# Живёт ОТДЕЛЬНО от Context — статичен, не меняется между вызовами.
# Context получает ссылку — правила читают через ctx.character.
#
# FIX: DEFAULT_CHARACTER["name"] = "Макс" (было "Jarvis").
#
# ── FIX (этап 0.5): print → log(tag="PC") ────────────────────────
#   Раньше character.py шёл в stderr (print), в jarvis.log его не было.
#   Теперь: log(tag="PC", level=...).
#     🎭 character (на импорте) — DEBUG (шумно, но нужно для отладки).
#     ⚠ character (ошибки загрузки) — WARN.

import os
import json
from dataclasses import dataclass

from shared.log import log


# ============================================================
# КОНСТАНТЫ
# ============================================================
DEFAULT_CHARACTER = {
    "name": "Макс",
    "style": "calm_confident",
    "humor": 0.35,
    "sarcasm": 0.15,
    "verbosity": 0.25,
    "politeness": 0.9,
    "allow_teasing": True,
    "formality": 0.7,
    "initiative": 0.5,
    "temperature": 0.7,
}


# ============================================================
# CHARACTER
# ============================================================
@dataclass
class Character:
    """Параметры характера Макса.

    Все числа — 0.0–1.0 (кроме style и name).

    temperature — «живость» выбора фраз в PhrasePicker:
        0.3 — почти всегда любимая фраза (детерминизм)
        0.7 — «живой» Макс, узнаваемый, но не дословный (по умолчанию)
        1.5 — непредсказуемый, редкие фразы выпадают часто
    """
    name: str = "Макс"
    style: str = "calm_confident"
    humor: float = 0.35
    sarcasm: float = 0.15
    verbosity: float = 0.25
    politeness: float = 0.9
    allow_teasing: bool = True

    formality: float = 0.7
    initiative: float = 0.5
    temperature: float = 0.7

    def is_formal(self) -> bool:
        return self.formality >= 0.6

    def is_humorous(self) -> bool:
        return self.humor >= 0.4

    def is_sarcastic(self) -> bool:
        return self.sarcasm >= 0.3

    def is_verbose(self) -> bool:
        return self.verbosity >= 0.6

    def is_polite(self) -> bool:
        return self.politeness >= 0.7

    def summary(self) -> str:
        return (f"style={self.style} "
                f"humor={self.humor:.2f} "
                f"sarcasm={self.sarcasm:.2f} "
                f"formality={self.formality:.2f} "
                f"politeness={self.politeness:.2f} "
                f"temperature={self.temperature:.2f}")


# ============================================================
# ЗАГРУЗКА ИЗ JSON
# ============================================================
def _load_from_json(base_dir: str) -> dict:
    path = os.path.join(base_dir, "config", "personality.json")
    if not os.path.exists(path):
        log(f"character: не найден {path}, использую дефолты",
            tag="PC", level="DEBUG")
        return {}
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            log(f"character: {path} — не dict, использую дефолты",
                tag="PC", level="WARN")
            return {}
        return data
    except Exception as e:
        log(f"character: ошибка чтения {path}: {e}",
            tag="PC", level="WARN")
        return {}


# ============================================================
# СИНГЛТОН — один на процесс
# ============================================================
_character: Character | None = None


def get(base_dir: str | None = None) -> Character:
    global _character
    if _character is None:
        data = {}
        if base_dir:
            data = _load_from_json(base_dir)
        merged = {}
        for key, default in DEFAULT_CHARACTER.items():
            merged[key] = data.get(key, default)
        _character = Character(**merged)
        log(f"character: {_character.summary()}", tag="PC", level="DEBUG")
    return _character


def reset(base_dir: str | None = None):
    global _character
    _character = None
    if base_dir:
        get(base_dir)