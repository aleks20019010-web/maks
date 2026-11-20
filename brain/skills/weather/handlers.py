# brain/skills/weather/handlers.py
# Обработка запросов погоды.

from brain.skills._helpers import respond_and_say as _respond_and_say
from brain.skills.weather.sources import fetch_weather
from brain.skills.weather.classify import classify
from brain.skills.weather.intent import parse_intent
from shared.log import log


def _tomorrow_phrase_set(vars_out: dict) -> str:
    precip = vars_out.get("precip", "")
    bucket = vars_out.get("temp_bucket", "")
    wind = vars_out.get("wind", "")

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

    if wind in ("strong", "very_strong") and precip in ("clear", "cloudy"):
        return "WEATHER_TOMORROW_WINDY"

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
    if data is None:
        log(f"weather: нет данных (when={when})", tag="BRAIN", level="WARN")
        _respond_and_say("ERROR_GENERIC")
        return True

    vars_out = classify(data)

    log(f"weather: when={when} temp={vars_out['temp']} "
        f"bucket={vars_out['temp_bucket']} "
        f"precip={vars_out['precip']} "
        f"wind={vars_out['wind']}", tag="BRAIN", level="DEBUG")

    if when == "tomorrow":
        phrase_set = _tomorrow_phrase_set(vars_out)
    else:
        phrase_set = 'WEATHER_NOW'

    _respond_and_say(
        phrase_set,
        temp=vars_out["temp"],
        gradus=vars_out["gradus"],
        sky=vars_out["sky"],
        wind=vars_out["wind_ru"],
        advice=vars_out["advice"],
        temp_bucket=vars_out["temp_bucket"],
        precip=vars_out["precip"],
    )

    return True