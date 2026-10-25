# personality_core/style.py
# Стиль ответа: normal / formal / friendly / night / tired.
#
# Стиль — это КАК говорить (тон, темп). Влияет на prosody (rate/pitch/emotion).
# Не путать с mood (состояние) — mood → style через маппинг.
#
# pick_style(ctx) — по контексту (время, self_state, событие).
# pick_style_from_state(st) — только по self_state (без ctx).
#
# Порядок приоритетов:
#   1. is_drowsy → "tired"
#   2. ночь (23:00–05:00) → "night"
#   3. mood=focused/formal → "formal"
#   4. mood=friendly/playful/excited → "friendly"
#   5. иначе → "normal"

from datetime import datetime


# ── маппинг mood → style ──────────────────────────────────────
MOOD_TO_STYLE = {
    "friendly":  "friendly",
    "playful":   "friendly",
    "excited":   "friendly",
    "focused":   "formal",
    "ironic":    "normal",     # ironic — это тон, не стиль (prosody сам добавит pitch)
    "sarcastic": "normal",     # sarcastic — тон, не стиль
    "tired":     "tired",
    "neutral":   "normal",
}


def _is_night(hour: int) -> bool:
    return hour >= 23 or hour < 5


def pick_style(ctx) -> str:
    """Выбрать стиль по контексту.

    ctx — Context из personality_core.types.
    Если ctx.self_state is None — "normal".
    """
    if ctx is None:
        return "normal"

    st = getattr(ctx, "self_state", None)
    if st is None:
        return "normal"

    # 1. Drowsy — приоритет выше ночи
    if getattr(st, "is_drowsy", False):
        return "tired"

    # 2. Ночь
    hour = getattr(ctx, "hour", None)
    if hour is None:
        hour = datetime.now().hour
    if _is_night(hour):
        return "night"

    # 3. Mood → style
    mood = getattr(st, "mood", "") or ""
    return MOOD_TO_STYLE.get(mood, "normal")


def pick_style_from_state(st) -> str:
    """Выбрать стиль только по self_state (без ctx).

    Используется в process_llm_answer, когда ctx=None.
    """
    if st is None:
        return "normal"

    # 1. Drowsy
    if getattr(st, "is_drowsy", False):
        return "tired"

    # 2. Ночь (по текущему времени)
    if _is_night(datetime.now().hour):
        return "night"

    # 3. Mood → style
    mood = getattr(st, "mood", "") or ""
    return MOOD_TO_STYLE.get(mood, "normal")