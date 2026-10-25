"""
tests/test_play_intent.py — тесты определения намерения play-команд
и char-n-grams (fuzzy-поиск по алиасам).

Проверяем:
    _detect_intent(query)      → (force_artist, force_track, cleaned)
    _is_short_query(query)     → bool
    _similarity(a, b)          → float (Jaccard по char-3-grams)

Все функции чистые, без state.client / сети — тесты быстрые.

Запуск:
    python tests/run_all.py play
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from skills.music.handlers import (
    _detect_intent,
    _is_short_query,
    SHORT_QUERY_WORDS,
)


# ── _detect_intent ─────────────────────────────────────────────

def test_detect_intent_artist():
    """«группу Nightwish» → force_artist=True, query='Nightwish'."""
    fa, ft, q = _detect_intent("группу Nightwish")
    assert fa is True, f"force_artist={fa}"
    assert ft is False, f"force_track={ft}"
    assert q == "Nightwish", f"cleaned={q!r}"


def test_detect_intent_track():
    """«песню Wishmaster» → force_track=True, query='Wishmaster'."""
    fa, ft, q = _detect_intent("песню Wishmaster")
    assert fa is False, f"force_artist={fa}"
    assert ft is True, f"force_track={ft}"
    assert q == "Wishmaster", f"cleaned={q!r}"


def test_detect_intent_none():
    """«Nightwish» — без маркеров."""
    fa, ft, q = _detect_intent("Nightwish")
    assert fa is False, f"force_artist={fa}"
    assert ft is False, f"force_track={ft}"
    assert q == "Nightwish", f"cleaned={q!r}"


def test_detect_intent_both():
    """«группу и песню» — оба флага (кто-то сказал странное)."""
    fa, ft, q = _detect_intent("группу и песню")
    assert fa is True, f"force_artist={fa}"
    assert ft is True, f"force_track={ft}"
    assert "групп" not in q.lower(), f"cleaned={q!r}"
    assert "песн" not in q.lower(), f"cleaned={q!r}"


def test_detect_intent_artist_variants():
    """Разные формы маркеров артиста."""
    for word in ("группу", "группа", "исполнителя", "артиста", "коллектив"):
        fa, ft, q = _detect_intent(f"{word} Кино")
        assert fa is True, f"'{word} Кино' → force_artist={fa}"
        assert q == "Кино", f"'{word} Кино' → cleaned={q!r}"


def test_detect_intent_track_variants():
    """Разные формы маркеров трека."""
    for word in ("песню", "трек", "композицию", "песни"):
        fa, ft, q = _detect_intent(f"{word} Wishmaster")
        assert ft is True, f"'{word} Wishmaster' → force_track={ft}"
        assert q == "Wishmaster", f"'{word} Wishmaster' → cleaned={q!r}"


# ── _is_short_query ────────────────────────────────────────────

def test_is_short_query_single_word():
    """Одно слово — всегда короткий."""
    assert _is_short_query("найтвиш") is True
    assert _is_short_query("Nightwish") is True
    assert _is_short_query("Кино") is True
    assert _is_short_query("Ария") is True


def test_is_short_query_two_words():
    """Два слова — ещё короткий (на границе)."""
    assert _is_short_query("группа крови") is True
    assert _is_short_query("Nightwish Wishmaster") is True


def test_is_short_query_three_plus():
    """Три и больше — длинный."""
    assert _is_short_query("Сектор газа пора домой") is False
    assert _is_short_query("ария беспечный ангел") is False
    assert _is_short_query("кино группа крови") is False


def test_is_short_query_respects_threshold():
    """Порог SHORT_QUERY_WORDS уважается."""
    n = SHORT_QUERY_WORDS
    short = " ".join(["а"] * n)
    long_ = " ".join(["а"] * (n + 1))
    assert _is_short_query(short) is True
    assert _is_short_query(long_) is False


def test_is_short_query_empty():
    """Пустой запрос — 0 слов ≤ порога → True (не падаем)."""
    assert _is_short_query("") is True
    assert _is_short_query("   ") is True


# ── char-n-grams (fuzzy) ───────────────────────────────────────

def test_char_ngrams_similarity():
    """Jaccard по char-3-grams: похожие — выше 0.3, далёкие — ниже 0.2."""
    from skills.music.aliases import _similarity

    # Похожие (опечатки, окончания) — заметное сходство
    assert _similarity("лесник", "лисник") > 0.3, \
        f"лесник/лисник = {_similarity('лесник', 'лисник'):.2f}"
    assert _similarity("ария", "ариа") > 0.3, \
        f"ария/ариа = {_similarity('ария', 'ариа'):.2f}"
    assert _similarity("киш", "кишь") > 0.3, \
        f"киш/кишь = {_similarity('киш', 'кишь'):.2f}"

    # Далекие (разные слова) — низкое сходство
    assert _similarity("ночной", "ария") < 0.2, \
        f"ночной/ария = {_similarity('ночной', 'ария'):.2f}"
    assert _similarity("привет", "металлика") < 0.2, \
        f"привет/металлика = {_similarity('привет', 'металлика'):.2f}"


def test_char_ngrams_symmetry():
    """_similarity симметрична: (a,b) == (b,a)."""
    from skills.music.aliases import _similarity
    pairs = [
        ("лесник", "лисник"),
        ("кино", "кина"),
        ("ария", "ариа"),
        ("nightwish", "найтвиш"),
    ]
    for a, b in pairs:
        s1 = _similarity(a, b)
        s2 = _similarity(b, a)
        assert abs(s1 - s2) < 1e-6, f"{a}/{b}: {s1} != {s2}"


def test_char_ngrams_empty():
    """Пустые строки → 0.0, не падаем."""
    from skills.music.aliases import _similarity
    assert _similarity("", "") == 0.0
    assert _similarity("а", "") == 0.0
    assert _similarity("", "а") == 0.0