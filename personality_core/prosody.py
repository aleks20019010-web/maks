# personality_core/prosody.py
# Prosody: rate, pitch, emotion, паузы, ударения, акценты.

import os
import json
import re

from shared.log import log


_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_STRESS_FILE = os.path.join(_BASE_DIR, "personality_core", "stress_words.json")


def _load_stress_words() -> dict:
    if not os.path.exists(_STRESS_FILE):
        return {}
    try:
        with open(_STRESS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log(f"prosody: не загрузил stress_words.json: {e}", tag="PC", level="WARN")
        return {}


STRESS_WORDS = _load_stress_words()


def reload_stress_words():
    global STRESS_WORDS
    STRESS_WORDS = _load_stress_words()


def get_answer_style(self_state, explicit_detail: bool = False) -> str:
    if explicit_detail:
        return "full"
    if self_state is None:
        return "full"
    if getattr(self_state, "is_drowsy", False):
        return "minimal"
    if getattr(self_state, "energy", 1.0) < 0.5:
        return "short"
    return "full"


def get_rate_pitch_emotion(self_state, style: str) -> dict:
    result = {"rate": None, "pitch": None, "emotion": None}
    if self_state is None:
        return result

    if style == "night":
        result["rate"] = "-12%"
        result["pitch"] = "-10Hz"
        result["emotion"] = "neutral"
        return result

    if getattr(self_state, "is_drowsy", False):
        result["rate"] = "-15%"
        result["pitch"] = "-12Hz"
        result["emotion"] = "tired"
        return result

    if getattr(self_state, "energy", 1.0) < 0.3:
        result["rate"] = "-10%"
        result["emotion"] = "tired"
        return result

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
    elif mood == "focused":
        result["emotion"] = "curious"
    else:
        result["emotion"] = "neutral"

    return result


PAUSE_COMMA = "sil<[50]>"
PAUSE_DOT = "sil<[100]>"
PAUSE_DASH = "sil<[75]>"
PAUSE_SEMICOLON = "sil<[100]>"


def _apply_stress(text: str) -> str:
    for word, stressed in STRESS_WORDS.items():
        text = re.sub(
            r'\b' + re.escape(word) + r'\b',
            stressed,
            text,
            flags=re.IGNORECASE,
        )
    return text


def apply_prosody(text: str) -> str:
    if not text:
        return text

    text = re.sub(r'(?<!\d),(?!\d)', f' {PAUSE_COMMA} ', text)
    text = text.replace(";", f" {PAUSE_SEMICOLON} ")
    text = text.replace("—", f" {PAUSE_DASH} ")
    text = text.replace("–", f" {PAUSE_DASH} ")
    text = re.sub(r'(?<!\d)\.(?!\d)', f' {PAUSE_DOT} ', text)

    text = _apply_stress(text)
    text = re.sub(r'\s+', ' ', text).strip()

    if text.endswith(PAUSE_DOT):
        text = text[:-len(PAUSE_DOT)].strip()
    elif text.endswith(PAUSE_COMMA):
        text = text[:-len(PAUSE_COMMA)].strip()

    return text