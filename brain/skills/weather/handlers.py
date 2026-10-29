# brain/skills/weather/handlers.py
# Обработка запросов погоды.
# Все фразы — через brain/skills/_helpers.
#
# A5 (день 29): parse_intent(text) → "now" | "tomorrow".
#   now → WEATHER_NOW (как было).
#   tomorrow → WEATHER_TOMORROW_* (по temp_bucket × precip).
#
# Почему WEATHER_TOMORROW_* отдельно, а не «WEATHER_NOW + слово завтра»:
#   фразы разные («завтра днём», «завтра к обеду»), категории разные,
#   composer-правила разные. Смешивать — хуже.

from brain.skills._helpers import respond_and_say as _respond_and_say
from brain.skills.weather.sources import fetch_weather
from brain.skills.weather.classify import classify
from brain.skills.weather.intent import parse_intent


def _tomorrow_phrase_set(vars_out: dict) -> str:
    """Выбрать WEATHER_TOMORROW_* по temp_bucket × precip.

    Приоритет: storm/fog/ice/hail/sleet/heavy_rain > snow/rain > clear/cloudy.
    """
    precip = vars_out.get("precip", "")
    bucket = vars_out.get("temp_bucket", "")
    wind = vars_out.get("wind", "")

    # Экстремальные — всегда
    if precip == "storm":
        return "WEATHER_TOMORROW_STORM"
    if precip == "fog":
        return "WEATHER_TOMORROW_FOG"
    if precip == "ice":
        return "WEATHER_TOMORROW_ICE"
    if precip == "hail":
        return "WEATHER_TOMORROW_HAIL"
    if precip == "sleet":
        return "WEATHER_TOMORROW_SLEET"
    if precip == "heavy_rain":
        return "WEATHER_TOMORROW_HEAVY_RAIN"

    # Сильный ветер
    if wind in ("strong", "very_strong") and precip in ("clear", "cloudy"):
        return "WEATHER_TOMORROW_WINDY"

    # Дождь/снег по температуре
    if precip == "rain":
        if bucket in ("cold", "freeze"):
            return "WEATHER_TOMORROW_COLD_RAIN"
        return "WEATHER_TOMORROW_RAIN"
    if precip == "snow":
        if bucket == "warm":
            return "WEATHER_TOMORROW_WARM_SNOW"
        return "WEATHER_TOMORROW_SNOW"
    if precip == "drizzle":
        return "WEATHER_TOMORROW_DRIZZLE"

    # Ясно/облачно по температуре
    if bucket == "hot":
        return "WEATHER_TOMORROW_HOT"
    if bucket in ("warm", "mild"):
        if precip == "clear":
            return "WEATHER_TOMORROW_WARM_CLEAR"
        return "WEATHER_TOMORROW_WARM_CLOUDY"
    if bucket in ("cold", "freeze"):
        if precip == "clear":
            return "WEATHER_TOMORROW_COLD_CLEAR"
        return "WEATHER_TOMORROW_COLD_CLOUDY"

    return "WEATHER_TOMORROW_DEFAULT"


def handle_weather_query(text=None):
    """Обработать запрос погоды. Возвращает True, если сработало."""
    when = parse_intent(text)
    data = fetch_weather(when=when)
    vars_out = classify(data)

    print(f"   🌤 weather: when={when} temp={vars_out['temp']} "
          f"bucket={vars_out['temp_bucket']} "
          f"precip={vars_out['precip']} "
          f"wind={vars_out['wind']}")

    if when == "tomorrow":
        phrase_set = _tomorrow_phrase_set(vars_out)
        _respond_and_say(
            phrase_set,
            temp=vars_out["temp"],
            sky=vars_out["sky"],
            wind=vars_out["wind_ru"],
            advice=vars_out["advice"],
            temp_bucket=vars_out["temp_bucket"],
            precip=vars_out["precip"],
        )
    else:
        _respond_and_say(
            'WEATHER_NOW',
            temp=vars_out["temp"],
            sky=vars_out["sky"],
            wind=vars_out["wind_ru"],
            advice=vars_out["advice"],
            temp_bucket=vars_out["temp_bucket"],
            precip=vars_out["precip"],
        )

    return True