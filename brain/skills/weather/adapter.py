# brain/skills/weather/adapter.py
# Адаптер: NLU Command → WeatherQuery.
#
# ЦЕЛЬ: доказать, что NLU не привязан к музыке. WeatherSkill принимает
# текст (handle_weather_query(text)), NLU возвращает Command. Адаптер
# переводит Command → то, что понимает WeatherSkill.
#
# ЭТО НЕ ПОДКЛЮЧЕНИЕ К PIPELINE. WeatherSkill пока работает по-старому
# (router → find_handler → WeatherSkill.handle). Адаптер — для будущего
# этапа 10: когда WeatherDecisionEngine появится, pipeline сможет
# вернуть Decision(EXECUTE, action="weather_query"), а router вызовет
# adapter.from_nlu(command) → handle_weather_query(...).
#
# ПОЧЕМУ НЕ РЕГИСТРИРУЕМ В PIPELINE СЕЙЧАС:
#   У погоды пока нет EntityExtractor и DecisionEngine. Без них pipeline
#   вернёт FALLBACK для WEATHER_QUERY. Регистрировать «полуготовое» —
#   хуже, чем не регистрировать. Адаптер — доказательство, что контракт
#   работает, а не «включение в продакшн».
#
# ЧТО ДЕЛАЕТ:
#   from_nlu(command) -> WeatherQuery
#     WeatherQuery = {"when": "now" | "tomorrow", "city": str | None}
#   Из Command.entity (если есть) берём date → when, location → city.
#   Если entity нет — when="now" (как parse_intent(None) → "now").

from dataclasses import dataclass
from typing import Optional

from brain.nlu.types import Command, Entity, EntityState, Intent


@dataclass(frozen=True)
class WeatherQuery:
    """Параметры запроса погоды. То, что нужно handle_weather_query()."""
    when: str = "now"          # "now" | "tomorrow"
    city: Optional[str] = None  # None — из config/weather.json


def from_nlu(command: Command) -> Optional[WeatherQuery]:
    """Command → WeatherQuery. None — если command не про погоду.

    Не бросает. При ошибке — None.
    """
    if command is None:
        return None
    if command.intent != Intent.WEATHER_QUERY:
        return None

    try:
        entity = command.entity

        # Entity ABSENT или None — «погода» без уточнений → now.
        if entity is None or entity.state != EntityState.FOUND:
            return WeatherQuery(when="now", city=None)

        # FOUND date → when="tomorrow", если дата = завтра.
        # FOUND location → city.
        # entity.type у погоды пока не заполняется (нет WeatherEntityExtractor),
        # но контракт готов.
        if entity.type == "location" and entity.value:
            return WeatherQuery(when="now", city=entity.value)

        if entity.type == "date" and entity.value:
            # entity.value = "2026-10-31" (ISO). Сравниваем с завтра.
            from datetime import datetime, timedelta
            try:
                due = datetime.fromisoformat(entity.value).date()
                tomorrow = (datetime.now() + timedelta(days=1)).date()
                when = "tomorrow" if due == tomorrow else "now"
            except Exception:
                when = "now"
            return WeatherQuery(when=when, city=None)

        # FOUND с неизвестным типом — now, без города.
        return WeatherQuery(when="now", city=None)

    except Exception:
        return None


def to_handle_kwargs(query: WeatherQuery) -> dict:
    """WeatherQuery → kwargs для handle_weather_query.

    handle_weather_query(text) принимает ТЕКСТ и сам зовёт parse_intent(text).
    Это не идеально (адаптер знает про text-сигнатуру), но пока WeatherSkill
    не переписан — это честный мост.

    TODO(этап 10): когда WeatherSkill начнёт принимать WeatherQuery
    (а не текст), to_handle_kwargs станет ненужным. handle_weather_query
    будет принимать when=query.when, city=query.city.
    """
    # Собираем текст-подсказку, чтобы parse_intent понял when.
    # Это костыль, но ВРЕМЕННЫЙ и явный. Когда WeatherSkill перепишем —
    # уйдёт. Не прячем.
    if query.when == "tomorrow":
        return {"text": "погода на завтра"}
    return {"text": "погода"}