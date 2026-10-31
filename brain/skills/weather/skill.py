# brain/skills/weather/skill.py
# Класс WeatherSkill — точка входа для роутера.
#
# ЭТАП 9 (часть 2, новое): handle принимает Command | str.
#   - Command (NLU-путь): handle_weather_query(command.original_text).
#     day (today/tomorrow) — не здесь: handlers.handle_weather_query
#     сам парсит «завтра»/«сегодня» из original_text.
#   - str (fast-path): handle_weather_query(text) (как раньше).
#   router._execute_decision: if skill.name in ("music", "controls", "weather")
#   → skill.handle(decision.command). Остальные — текст (переходный период).

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
    priority = 35

    def can_handle(self, text: str) -> bool:
        t = text.lower().strip()
        if not t:
            return False
        return any(w in t for w in WEATHER_TRIGGERS)

    def handle(self, text_or_command) -> str | None:
        """Handle Command (NLU-путь) или str (fast-path).

        Command → handle_weather_query(command.original_text).
        str     → handle_weather_query(text) (как раньше).
        """
        from brain.nlu.types import Command
        if isinstance(text_or_command, Command):
            text = text_or_command.original_text or text_or_command.normalized_text
        else:
            text = text_or_command
        if not text:
            return None
        handle_weather_query(text)
        return None


register(WeatherSkill())