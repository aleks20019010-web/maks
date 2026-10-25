# brain/skills/weather/skill.py
# Класс WeatherSkill — точка входа для роутера.

from brain.skills.base import Skill, register
from brain.skills.weather.handlers import handle_weather_query


WEATHER_TRIGGERS = (
    "погода",
    "погоду",
    "погоде",
    "погоды",
    "что с погодой",
    "какая погода",
    "как погода",
    "что за окном",
    "на улице",
    "дождь будет",
    "будет дождь",
    "снег будет",
    "будет снег",
    "градусов на улице",
    "температура на улице",
    "погода на завтра",
    "погода завтра",
    "погода сегодня",
    "weather",
)


class WeatherSkill(Skill):
    name = "weather"
    priority = 35  # между smalltalk (50) и music (40)? — проверь порядок

    def can_handle(self, text: str) -> bool:
        t = text.lower().strip()
        if not t:
            return False
        return any(w in t for w in WEATHER_TRIGGERS)

    def handle(self, text: str) -> str | None:
        handle_weather_query(text)
        return None


register(WeatherSkill())