# brain/skills/weather/handlers.py
# Обработка запросов погоды.
# Все фразы — через brain/skills/_helpers.

from brain.skills._helpers import respond_and_say as _respond_and_say
from brain.skills.weather.sources import fetch_weather
from brain.skills.weather.classify import classify


def handle_weather_query(text=None):
    """Обработать запрос погоды. Возвращает True, если сработало."""
    data = fetch_weather()
    vars_out = classify(data)

    print(f"   🌤 weather: temp={vars_out['temp']} "
          f"bucket={vars_out['temp_bucket']} "
          f"precip={vars_out['precip']} "
          f"wind={vars_out['wind']}")

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