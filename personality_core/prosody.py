# personality_core/prosody.py
# Prosody: rate, pitch, emotion, паузы, ударения, акценты.
#
# Не зависит от LLM. Работает по self_state.
#
# get_answer_style(self_state, explicit_detail) → "full" / "short" / "minimal"
# get_rate_pitch_emotion(self_state, style) → dict
# apply_prosody(text) → текст с разметкой Яндекса
#
# Словарь ударений — в personality_core/stress_words.json.
# Загружается при импорте. Можно пополнять без правки кода.

import os
import json
import re


# ============================================================
# ПУТИ
# ============================================================
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_STRESS_FILE = os.path.join(_BASE_DIR, "personality_core", "stress_words.json")


# ============================================================
# СЛОВАРЬ УДАРЕНИЙ
# ============================================================
def _load_stress_words() -> dict:
    """Загрузить словарь ударений из JSON."""
    if not os.path.exists(_STRESS_FILE):
        return {}
    try:
        with open(_STRESS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"⚠ prosody: не загрузил stress_words.json: {e}")
        return {}


STRESS_WORDS = _load_stress_words()


def reload_stress_words():
    """Перезагрузить словарь (для отладки)."""
    global STRESS_WORDS
    STRESS_WORDS = _load_stress_words()


# ============================================================
# ВЫБОР ВАРИАНТА ОТВЕТА
# ============================================================
def get_answer_style(self_state, explicit_detail: bool = False) -> str:
    """Выбрать full / short / minimal.

    explicit_detail → full (пользователь сам попросил подробно).
    is_drowsy → minimal.
    energy < 0.5 → short.
    energy >= 0.5 → full.
    """
    if explicit_detail:
        return "full"
    if self_state is None:
        return "full"
    if getattr(self_state, "is_drowsy", False):
        return "minimal"
    if getattr(self_state, "energy", 1.0) < 0.5:
        return "short"
    return "full"


# ============================================================
# RATE / PITCH / EMOTION
# ============================================================
def get_rate_pitch_emotion(self_state, style: str) -> dict:
    """Вернуть rate, pitch, emotion по self_state и стилю."""
    result = {"rate": None, "pitch": None, "emotion": None}
    if self_state is None:
        return result

    # Ночь
    if style == "night":
        result["rate"] = "-12%"
        result["pitch"] = "-10Hz"
        result["emotion"] = "neutral"
        return result

    # Усталость / дремлет
    if getattr(self_state, "is_drowsy", False):
        result["rate"] = "-15%"
        result["pitch"] = "-12Hz"
        result["emotion"] = "tired"
        return result

    if getattr(self_state, "energy", 1.0) < 0.3:
        result["rate"] = "-10%"
        result["emotion"] = "tired"
        return result

    # Настроение
    mood = getattr(self_state, "mood", "neutral")
    if mood == "ironic":
        result["pitch"] = "-5Hz"
        result["rate"] = "+5%"
        result["emotion"] = "good"
    elif mood == "sarcastic":
        result["pitch"] = "-8Hz"
        result["rate"] = "-5%"
        result["emotion"] = "good"
    elif mood == "friendly":
        result["rate"] = "+5%"
        result["emotion"] = "good"
    elif mood == "excited":
        result["rate"] = "+8%"
        result["emotion"] = "good"
    else:
        result["emotion"] = "neutral"

    return result


# ============================================================
# РАЗМЕТКА ТЕКСТА
# ============================================================
PAUSE_COMMA = "sil<[50]>"
PAUSE_DOT = "sil<[150]>"
PAUSE_DASH = "sil<[75]>"
PAUSE_SEMICOLON = "sil<[100]>"


def _apply_stress(text: str) -> str:
    """Заменить слова на ударные варианты."""
    for word, stressed in STRESS_WORDS.items():
        # только целые слова, регистронезависимо
        text = re.sub(
            r'\b' + re.escape(word) + r'\b',
            stressed,
            text,
            flags=re.IGNORECASE,
        )
    return text


def apply_prosody(text: str) -> str:
    """Добавить разметку: паузы вместо знаков, ударения."""
    if not text:
        return text

    # Паузы вместо знаков
    text = text.replace(",", f" {PAUSE_COMMA} ")
    text = text.replace(";", f" {PAUSE_SEMICOLON} ")
    text = text.replace("—", f" {PAUSE_DASH} ")
    text = text.replace("–", f" {PAUSE_DASH} ")
    text = text.replace(".", f" {PAUSE_DOT} ")

    # Ударения
    text = _apply_stress(text)

    # Убрать двойные пробелы
    text = re.sub(r'\s+', ' ', text).strip()

    # Убрать паузу в конце (TTS сам делает паузу)
    if text.endswith(PAUSE_DOT):
        text = text[:-len(PAUSE_DOT)].strip()
    elif text.endswith(PAUSE_COMMA):
        text = text[:-len(PAUSE_COMMA)].strip()

    return text