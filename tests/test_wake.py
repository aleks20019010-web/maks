# tests/test_wake.py
# Тесты A2: Vosk-wake (ViolaWake убран).
#
# Vosk кормится всегда, _find_wake_word ищет «макс» в тексте.
# Нашёл → play_beep(), after = команда после «макс».
#
# Проверяем:
#   - _find_wake_word: «макс» в начале / в середине / нет.
#   - границы слов: «макароны» ≠ «макс».

import pytest

from workers.asr.main import _find_wake_word


# ── _find_wake_word ───────────────────────────────────────────

def test_wake_word_at_start():
    """«макс включи киша» → wake=«макс», after=«включи киша»."""
    wake, after = _find_wake_word("макс включи киша")
    assert wake == "макс"
    assert after == "включи киша"


def test_wake_word_at_middle():
    """«слушай макс включи киша» → wake=«макс», after=«включи киша»."""
    wake, after = _find_wake_word("слушай макс включи киша")
    assert wake == "макс"
    assert after == "включи киша"


def test_wake_word_only():
    """«макс» → wake=«макс», after=«»."""
    wake, after = _find_wake_word("макс")
    assert wake == "макс"
    assert after == ""


def test_wake_word_not_found():
    """«включи киша» → wake=None, after=None."""
    wake, after = _find_wake_word("включи киша")
    assert wake is None
    assert after is None


def test_wake_word_makarony():
    """«макароны» → wake=None (границы слов, не «мак»)."""
    wake, after = _find_wake_word("макароны вкусные")
    assert wake is None


def test_wake_word_maksu():
    """«максу» → wake=«максу» (границы слов)."""
    wake, after = _find_wake_word("максу включи киша")
    assert wake == "максу"
    assert after == "включи киша"


def test_wake_word_with_punctuation():
    """«Макс, включи киша» → wake=«макс», after=«включи киша»."""
    wake, after = _find_wake_word("Макс, включи киша")
    assert wake == "макс"
    assert after == "включи киша"