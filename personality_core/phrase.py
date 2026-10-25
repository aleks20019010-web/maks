"""
personality_core/phrase.py — PhrasePicker.

Выбирает конкретную фразу из responses/*.json по phrase_set.
Держит анти-повтор (не повторять одну и ту же фразу дважды).
Подставляет переменные ({artist}, {track}, {greeting}).

Фильтры:
    night=True   — только ночью (23:00–5:00)
    night=False  — только днём
    drowsy=True  — только когда self_state.is_drowsy
    drowsy=False — только когда НЕ drowsy
    style="friendly" — только для этого стиля (= mood через маппинг)
    slot="core"  — только для этого слота (opening / core / closing)

Фразы БЕЗ style — универсальные, подходят для любого mood.
Фразы БЕЗ slot — универсальные, подходят для любого слота.

Temperature + weighted sampling:
    p_i ∝ weight_i ^ (1 / T)
"""

import os
import json
import math
import random
from typing import Optional
from datetime import datetime

from personality_core import state as pstate


DEFAULT_TEMPERATURE       = 0.7
DEFAULT_WEIGHT            = 1.0
ANTI_REPEAT_PENALTY       = 0.1
ANTI_REPEAT_MIN_TEMP      = 0.3
DEBUG_PHRASE_PICK         = False


MOOD_TO_STYLE = {
    "friendly":  "friendly",
    "ironic":    "ironic",
    "sarcastic": "sarcastic",
    "tired":     "normal",
    "playful":   "friendly",
    "excited":   "friendly",
    "focused":   "formal",
    "neutral":   "normal",
}


def _time_of_day_greeting() -> str:
    h = datetime.now().hour
    if 5 <= h < 12:
        return "Доброе утро"
    if 12 <= h < 18:
        return "Добрый день"
    if 18 <= h < 23:
        return "Добрый вечер"
    return "Доброй ночи"


class PhrasePicker:
    def __init__(self, responses_dir: str, temperature: float = DEFAULT_TEMPERATURE):
        self.responses_dir = responses_dir
        self.temperature = temperature
        self._phrases: dict = {}
        self._load()

    def _load(self):
        if not os.path.isdir(self.responses_dir):
            return
        for fname in sorted(os.listdir(self.responses_dir)):
            if not fname.endswith(".json"):
                continue
            if fname == "composer.json":
                continue  # правила composer — не фразы
            path = os.path.join(self.responses_dir, fname)
            try:
                with open(path, "r", encoding="utf-8-sig") as f:
                    data = json.load(f)
            except Exception as e:
                print(f"⚠ phrase: ошибка чтения {fname}: {e}")
                continue
            if isinstance(data, dict):
                for event, items in data.items():
                    if isinstance(items, list):
                        self._phrases[event] = items

    def reload(self):
        self._phrases = {}
        self._load()
        print(f"♻ phrase: загружено {len(self._phrases)} наборов фраз")

    def pick(self, phrase_set: str, temperature: Optional[float] = None,
             anti_repeat: bool = True, slot: Optional[str] = None, **kw) -> Optional[dict]:
        if not phrase_set or phrase_set not in self._phrases:
            return None

        variants = self._phrases[phrase_set]
        if not variants:
            return None

        last = pstate.get_last_pick(phrase_set)

        h = datetime.now().hour
        is_night = h >= 23 or h < 5

        is_drowsy = False
        current_mood = ""
        current_style = ""
        try:
            from personality_core import self_state as _ss
            st = _ss.get()
            is_drowsy = bool(st.is_drowsy)
            current_mood = getattr(st, "mood", "") or ""
            current_style = MOOD_TO_STYLE.get(current_mood, "")
        except Exception:
            pass

        def _passes_common(v):
            if v.get("night") is True and not is_night:
                return False
            if v.get("night") is False and is_night:
                return False
            if v.get("drowsy") is True and not is_drowsy:
                return False
            if v.get("drowsy") is False and is_drowsy:
                return False
            return True

        def _passes_slot(v):
            if slot is None:
                return True
            fslot = v.get("slot")
            if not fslot:
                return True  # универсальная — подходит для любого слота
            return fslot == slot

        # 1. Строгий фильтр: night + drowsy + style + slot
        filtered = []
        for v in variants:
            if not isinstance(v, dict):
                continue
            if not _passes_common(v):
                continue
            fstyle = v.get("style")
            if fstyle and current_style and fstyle != current_style:
                continue
            if not _passes_slot(v):
                continue
            filtered.append(v)

        # 2. Fallback 1: без style (только night + drowsy + slot)
        if not filtered:
            filtered = [v for v in variants
                        if isinstance(v, dict) and _passes_common(v) and _passes_slot(v)]

        # 3. Fallback 2: без slot (только night + drowsy)
        if not filtered:
            filtered = [v for v in variants
                        if isinstance(v, dict) and _passes_common(v)]

        # 4. Fallback 3: всё
        if not filtered:
            filtered = [v for v in variants if isinstance(v, dict)]

        if not filtered:
            filtered = variants

        temp = self.temperature if temperature is None else temperature
        try:
            temp = float(temp)
        except (TypeError, ValueError):
            temp = DEFAULT_TEMPERATURE
        temp = max(0.01, temp)

        last_penalty = {}
        if anti_repeat and last and temp > ANTI_REPEAT_MIN_TEMP:
            last_penalty = {last: ANTI_REPEAT_PENALTY}

        weights = []
        for v in filtered:
            w = v.get("weight", DEFAULT_WEIGHT)
            try:
                w = float(w)
            except (TypeError, ValueError):
                w = DEFAULT_WEIGHT
            if w <= 0:
                w = DEFAULT_WEIGHT
            if v.get("text") in last_penalty:
                w *= last_penalty[v["text"]]
            weights.append(w)

        exp = 1.0 / temp
        tempered = [w ** exp for w in weights]
        total = sum(tempered)

        if total <= 0 or not math.isfinite(total):
            choice = random.choice(filtered)
        else:
            probs = [t / total for t in tempered]
            choice = random.choices(filtered, weights=probs, k=1)[0]

        text = self._substitute(choice.get("text", ""), kw)

        if DEBUG_PHRASE_PICK:
            w_chosen = choice.get("weight", DEFAULT_WEIGHT)
            print(f"[phrase] set={phrase_set!r} T={temp:.2f} "
                  f"mood={current_mood!r} style={current_style!r} slot={slot!r} "
                  f"pool={len(filtered)} picked={text!r} w={w_chosen}")

        result = dict(choice)
        result["text"] = text
        pstate.remember_pick(phrase_set, text)
        return result

    def _substitute(self, text: str, kw: dict) -> str:
        if not text:
            return ""
        subs = {
            "greeting": _time_of_day_greeting(),
        }
        subs.update({k: str(v) if v is not None else "" for k, v in kw.items()})
        for k, v in subs.items():
            text = text.replace("{" + k + "}", v)
        return text