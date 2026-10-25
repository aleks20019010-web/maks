"""
tests/test_phrase.py — тесты PhrasePicker (temperature + weights + анти-повтор).

Запуск:
    python tests/run_all.py phrase
"""

import os
import sys
import tempfile
import json
from collections import Counter
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from personality_core.phrase import PhrasePicker


def _make_picker(phrases: dict, temperature: float = 0.7) -> PhrasePicker:
    tmp = tempfile.mkdtemp(prefix="phrase_test_")
    path = os.path.join(tmp, "test.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(phrases, f, ensure_ascii=False)
    return PhrasePicker(tmp, temperature=temperature)


def _sample(picker: PhrasePicker, phrase_set: str, n: int = 3000,
            anti_repeat: bool = True) -> Counter:
    c = Counter()
    for _ in range(n):
        r = picker.pick(phrase_set, anti_repeat=anti_repeat)
        if r:
            c[r["text"]] += 1
    return c


# ── температура ────────────────────────────────────────────────

def test_low_temp_picks_favorite():
    """T=0.01 → доминирует фраза с максимальным весом."""
    phrases = {
        "TEST": [
            {"text": "редкая",  "weight": 0.2},
            {"text": "обычная", "weight": 1.0},
            {"text": "любимая", "weight": 5.0},
        ]
    }
    p = _make_picker(phrases, temperature=0.01)
    c = _sample(p, "TEST", n=500)
    top = c.most_common(1)[0][0]
    assert top == "любимая", f"ожидали 'любимая', получили {top!r}"
    # анти-повтор мягко штрафует последнюю → «любимая» занимает ~половину,
    # но всё равно заметно чаще остальных
    assert c["любимая"] > 200, f"T=0.01: любимая={c['любимая']}, ожидали >200"
    assert c["любимая"] > 2 * c["обычная"], \
        f"любимая должна доминировать: {c['любимая']} vs {c['обычная']}"


def test_high_temp_is_flat():
    """T=10 → почти равномерное распределение."""
    phrases = {
        "TEST": [
            {"text": "a", "weight": 0.1},
            {"text": "b", "weight": 1.0},
            {"text": "c", "weight": 10.0},
        ]
    }
    p = _make_picker(phrases, temperature=10.0)
    c = _sample(p, "TEST", n=3000, anti_repeat=False)
    for t in ("a", "b", "c"):
        assert 700 < c[t] < 1300, f"{t}: {c[t]} вне ожидаемого коридора"


def test_weights_respected_at_t1():
    """T=1 → пропорционально весам (анти-повтор выключен)."""
    phrases = {
        "TEST": [
            {"text": "лёгкая",  "weight": 1.0},
            {"text": "тяжёлая", "weight": 3.0},
        ]
    }
    p = _make_picker(phrases, temperature=1.0)
    c = _sample(p, "TEST", n=4000, anti_repeat=False)
    ratio = c["тяжёлая"] / max(1, c["лёгкая"])
    assert 2.5 < ratio < 3.5, f"ожидали ratio≈3, получили {ratio:.2f}"


# ── анти-повтор ────────────────────────────────────────────────

def test_anti_repeat():
    """Анти-повтор: повторов подряд заметно меньше случайного (~50%)."""
    phrases = {
        "TEST": [
            {"text": "одна"},
            {"text": "две"},
        ]
    }
    p = _make_picker(phrases, temperature=2.0)
    prev = None
    repeats = 0
    for _ in range(200):
        r = p.pick("TEST")
        t = r["text"]
        if t == prev:
            repeats += 1
        prev = t
    # случайно было бы ~100/200; с анти-повтором должно быть заметно меньше
    assert repeats < 80, f"анти-повтор слабо работает: {repeats}/200"


# ── фильтры и крайние случаи ───────────────────────────────────

def test_night_filter():
    """night=true → только ночью; night=false → только днём."""
    phrases = {
        "TEST": [
            {"text": "день",  "night": False},
            {"text": "ночь",  "night": True},
            {"text": "всегда"},
        ]
    }
    h = datetime.now().hour
    is_night = h >= 23 or h < 5
    p = _make_picker(phrases, temperature=0.5)
    seen = set()
    for _ in range(100):
        r = p.pick("TEST")
        seen.add(r["text"])
    if is_night:
        assert "день" not in seen, f"день не должен появляться ночью: {seen}"
    else:
        assert "ночь" not in seen, f"ночь не должна появляться днём: {seen}"


def test_no_weight_still_works():
    """Фразы без weight → ведут себя как weight=1.0."""
    phrases = {
        "TEST": [
            {"text": "a"},
            {"text": "b"},
            {"text": "c"},
        ]
    }
    p = _make_picker(phrases, temperature=1.0)
    c = _sample(p, "TEST", n=3000, anti_repeat=False)
    for t in ("a", "b", "c"):
        assert 800 < c[t] < 1200, f"{t}: {c[t]} вне коридора равномерного"


def test_nonexistent_set():
    """Несуществующий набор → None."""
    p = _make_picker({"TEST": [{"text": "x"}]})
    assert p.pick("NOPE") is None
    assert p.pick("") is None