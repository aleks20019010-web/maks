# shared/time_bucket.py
# Один источник правды для «времени суток».
#
# Раньше: brain/main.py::_time_bucket и brain/skills/music/skill.py::_try_ask
# считали «утро/день/вечер/ночь» каждый по-своему. В music/skill.py было
# hour < 9 → «утро», и в 00:11 Макс говорил «доброе утро».
#
# Теперь: time_bucket() и ritual_for_bucket() живут здесь, оба места
# импортируют отсюда. Один источник правды.
#
# time_bucket(dt=None) — если dt не передан, берётся datetime.now().
# Принимает datetime, чтобы тесты не мокали время.

from datetime import datetime


def time_bucket(dt: datetime | None = None) -> str:
    """Вернуть 'morning' / 'day' / 'evening' / 'night'.

    Границы:
        05:00–11:59 → morning
        12:00–17:59 → day
        18:00–22:59 → evening
        23:00–04:59 → night
    """
    if dt is None:
        dt = datetime.now()
    h = dt.hour
    if 5 <= h < 12:
        return "morning"
    if 12 <= h < 18:
        return "day"
    if 18 <= h < 23:
        return "evening"
    return "night"


def ritual_for_bucket(bucket: str) -> str | None:
    """Вернуть phrase_set ритуала для бакета или None.

    morning → RITUAL_MORNING
    evening → RITUAL_EVENING
    night   → RITUAL_NIGHT
    day     → None (ритуала нет)
    """
    if bucket == "morning":
        return "RITUAL_MORNING"
    if bucket == "evening":
        return "RITUAL_EVENING"
    if bucket == "night":
        return "RITUAL_NIGHT"
    return None