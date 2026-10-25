# personality_core/character.py
# Характер Макса: параметры стиля, юмора, формальности.
# Загружается из config/personality.json. Недостающие поля — дефолты.
#
# Живёт ОТДЕЛЬНО от Context — статичен, не меняется между вызовами.
# Context получает ссылку — правила читают через ctx.character.

import os
import json
from dataclasses import dataclass


# ============================================================
# КОНСТАНТЫ
# ============================================================
DEFAULT_CHARACTER = {
    "name": "Jarvis",
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
    # --- Из personality.json ---
    name: str = "Jarvis"
    style: str = "calm_confident"   # глобальный тон (не то же, что style фразы)
    humor: float = 0.35
    sarcasm: float = 0.15
    verbosity: float = 0.25         # 0=короткие, 1=длинные
    politeness: float = 0.9
    allow_teasing: bool = True

    # --- Дополнительные (дефолты, могут быть в JSON) ---
    formality: float = 0.7          # 0=друг, 1=дворецкий
    initiative: float = 0.5         # 0=молчит, 1=говорит часто
    temperature: float = 0.7        # 0.3=детерминизм, 0.7=живой, 1.5=хаос

    # ========================================================
    # ЧТЕНИЕ — для правил и логов
    # ========================================================
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
    """Прочитать config/personality.json. Вернуть dict (пустой при ошибке)."""
    path = os.path.join(base_dir, "config", "personality.json")
    if not os.path.exists(path):
        print(f"⚠ character: не найден {path}, использую дефолты")
        return {}
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            print(f"⚠ character: {path} — не dict, использую дефолты")
            return {}
        return data
    except Exception as e:
        print(f"⚠ character: ошибка чтения {path}: {e}")
        return {}


# ============================================================
# СИНГЛТОН — один на процесс
# ============================================================
_character: Character | None = None


def get(base_dir: str | None = None) -> Character:
    """Вернуть синглтон Character.

    При первом вызове — загружает из config/personality.json.
    base_dir — путь к D:\\Jarvis (если первый вызов).
    """
    global _character
    if _character is None:
        data = {}
        if base_dir:
            data = _load_from_json(base_dir)
        # Мержим: дефолты + данные из JSON. Лишние поля игнорируем.
        merged = {}
        for key, default in DEFAULT_CHARACTER.items():
            merged[key] = data.get(key, default)
        _character = Character(**merged)
        print(f"🎭 character: {_character.summary()}")
    return _character


def reset(base_dir: str | None = None):
    """Сбросить (для тестов)."""
    global _character
    _character = None
    if base_dir:
        get(base_dir)