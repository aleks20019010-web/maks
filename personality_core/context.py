"""
personality_core/context.py — ContextBuilder.

Собирает Context из:
    - memory/        (ссылка на preferences для ленивых запросов)
    - time           (когда сейчас)
    - Event          (что пришло)
    - self_state     (mood, energy, valence, arousal)
    - character      (формальность, юмор, стиль)
    - identity       (манифест «кто я»)

Единственное место, где контекст расширяется.
Новые поля Context — новые ленивые методы в types.py.

current_track / current_artist / current_mode:
    Заполняются из event.data (если пришли в payload),
    иначе — пустые. Music worker — отдельный процесс,
    brain не имеет прямого доступа к его state.
"""

import os
from datetime import datetime

from personality_core.types import Event, Context


def _time_of_day(dt: datetime) -> str:
    h = dt.hour
    if 5 <= h < 12:
        return "morning"
    if 12 <= h < 18:
        return "day"
    if 18 <= h < 23:
        return "evening"
    return "night"


def _day_of_week(dt: datetime) -> str:
    return ["monday", "tuesday", "wednesday", "thursday",
            "friday", "saturday", "sunday"][dt.weekday()]


class ContextBuilder:
    def __init__(self):
        pass

    def build(self, event: Event) -> Context:
        """Собрать контекст для события."""
        ctx = Context()
        ctx.event = event

        # --- Время ---
        now = datetime.now()
        ctx.now = now
        ctx.time_of_day = _time_of_day(now)
        ctx.day_of_week = _day_of_week(now)
        ctx.hour = now.hour
        ctx.is_weekend = now.weekday() >= 5

        # --- Запрос ---
        ctx.query = event.text
        ctx.query_intent = event.name
        ctx.query_source = event.source

        # --- Ссылка на preferences (для ленивых запросов) ---
        try:
            from memory import memory
            ctx._prefs = memory.preferences
        except Exception as e:
            print(f"⚠ context: memory.preferences недоступна: {e}")
            ctx._prefs = None

        # --- Self-state (mood, energy, valence, arousal) ---
        try:
            from personality_core import self_state as _self_state
            ctx.self_state = _self_state.get()
        except Exception as e:
            print(f"⚠ context: self_state недоступна: {e}")
            ctx.self_state = None

        # --- Character (формальность, юмор, стиль) ---
        try:
            from personality_core import character as _character
            base_dir = os.path.dirname(
                os.path.dirname(os.path.abspath(__file__))
            )
            ctx.character = _character.get(base_dir=base_dir)
        except Exception as e:
            print(f"⚠ context: character недоступна: {e}")
            ctx.character = None

        # --- Identity (манифест «кто я») ---
        try:
            from personality_core import identity as _identity
            base_dir = os.path.dirname(
                os.path.dirname(os.path.abspath(__file__))
            )
            ctx.identity = _identity.get(base_dir=base_dir)
        except Exception as e:
            print(f"⚠ context: identity недоступна: {e}")
            ctx.identity = None

        # --- Текущий трек / режим ---
        # Music worker — отдельный процесс, brain не имеет прямого доступа
        # к его state. Если payload содержит track/artist/mode — берём оттуда.
        payload = event.data or {}
        ctx.current_track = payload.get("track", "") or ""
        ctx.current_artist = payload.get("artist", "") or ""
        ctx.current_mode = payload.get("mode", "") or ""

        return ctx