# tests/test_normalizer.py
# Этап 2: TextNormalizer. 3 теста.

from brain.nlu.normalizer import normalize


def test_normalize_strips_maks():
    """«Макс, навали арию!» → «навали арию»."""
    assert normalize("Макс, навали арию!") == "навали арию"


def test_normalize_punctuation():
    """Пунктуация убирается, пробелы нормализуются."""
    assert normalize("  Включи,   рок!  ") == "включи рок"


def test_normalize_empty():
    """Пустая строка → пустая."""
    assert normalize("") == ""
    assert normalize(None) == ""