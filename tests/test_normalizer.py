# tests/test_normalizer.py
# Контракт normalizer.normalize:
#   lowercase, пунктуация, STOP_PREFIXES («макс», «слушаю»), _FILLERS («давай»),
#   пробелы.
#
# Числа словами → цифрами — НЕ задача normalizer:
#   Это controls.parse_volume (NUMBER_WORDS + VOLUME_PATTERNS). Дублировать
#   не надо. Тест test_numbers_words_to_digits убран — он проверял чужую зону.

from brain.nlu.normalizer import normalize


def test_empty():
    assert normalize("") == ""


def test_lowercase():
    assert normalize("ВКЛЮЧИ АРИЮ") == "включи арию"


def test_strips_maks():
    assert "макс" not in normalize("макс включи арию")


def test_strips_punctuation():
    assert "!" not in normalize("включи арию!")
    assert "," not in normalize("макс, включи арию")


def test_strips_slushayu():
    # «слушаю вас» — STOP_PREFIX
    assert "слушаю" not in normalize("слушаю вас включи рок")


def test_strips_davai():
    # «давай» — _FILLERS (общий шум)
    assert "давай" not in normalize("давай послушаем киша")


def test_collapses_spaces():
    assert normalize("  включи   арию  ") == "включи арию"