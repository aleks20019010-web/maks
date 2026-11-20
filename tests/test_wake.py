# tests/test_wake.py
# Тесты A2: Vosk-wake (after_wake в workers.asr.text_utils).

import pytest

from workers.asr.text_utils import after_wake


def test_wake_word_at_start():
    """«макс включи киша» → «включи киша»."""
    assert after_wake("макс включи киша") == "включи киша"


def test_wake_word_at_middle():
    """«слушай макс включи киша» → «включи киша»."""
    assert after_wake("слушай макс включи киша") == "включи киша"


def test_wake_word_only():
    """«макс» → «»."""
    assert after_wake("макс") == ""


def test_wake_word_not_found():
    """«включи киша» → None."""
    assert after_wake("включи киша") is None


def test_wake_word_makarony():
    """«макароны» → None (границы слов, не «мак»)."""
    assert after_wake("макароны вкусные") is None


def test_wake_word_maksu():
    """«максу» → «включи киша» (нечёткое совпадение с «макс»)."""
    assert after_wake("максу включи киша") == "включи киша"


def test_wake_word_with_punctuation():
    """«Макс, включи киша» → «включи киша»."""
    assert after_wake("Макс, включи киша") == "включи киша"