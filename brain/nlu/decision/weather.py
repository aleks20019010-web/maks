# brain/nlu/decision/weather.py
# DecisionEngine для weather: Command → Decision.
#
# WEATHER_QUERY → EXECUTE(action="weather").
#   day (today/tomorrow) — НЕ здесь: handlers.handle_weather_query сам
#   парсит «завтра»/«сегодня» из original_text. Дублировать не надо.
#   Город — фиксированный (config/weather.json: Боровичи). Не извлекается.
#
# ПОЧЕМУ DecisionEngine, А НЕ ХАРДКОД В PIPELINE:
#   Без DecisionEngine pipeline для WEATHER_QUERY → FALLBACK → LLM
#   (медленно, 4.5 сек). С DecisionEngine — EXECUTE → weather.handle(Command)
#   → handle_weather_query → быстро.
#
# ЧЕГО DecisionEngine НЕ ДЕЛАЕТ:
#   - не извлекает entity (город/дата — фиксированы/парсятся в handlers)
#   - не выбирает скил (это skill_resolver)
#   - не говорит (это router)

from brain.nlu.types import (
    Command, Decision, DecisionAction, Intent,
)
from shared.log import log


class WeatherDecisionEngine:
    """DecisionEngine для weather (WEATHER_QUERY → EXECUTE(weather))."""

    supported_intents: tuple[Intent, ...] = (Intent.WEATHER_QUERY,)

    def supports(self, intent: Intent) -> bool:
        return intent in self.supported_intents

    def resolve(self, command: Command) -> Decision:
        """Command → Decision. Не бросает."""
        if command is None:
            return self._fallback(None, "command is None")

        try:
            if not self.supports(command.intent):
                return self._fallback(
                    command,
                    f"intent {command.intent.value} not supported",
                )

            command.action = "weather"
            return Decision(
                command=command,
                action=DecisionAction.EXECUTE,
                reason="WEATHER_QUERY → weather",
                confidence=command.confidence,
            )
        except Exception as e:
            log(f"decision.weather: {e}", tag="BRAIN", level="WARN")
            return self._fallback(command, f"exception: {e}")

    def _fallback(self, command: Command | None, reason: str) -> Decision:
        if command is None:
            command = Command(
                intent=Intent.UNKNOWN,
                entity=None,
                skill_name=None,
                action=None,
                confidence=0.0,
                normalized_text="",
                original_text="",
            )
        return Decision(
            command=command,
            action=DecisionAction.FALLBACK,
            reason=reason,
            confidence=0.0,
        )


_engine: WeatherDecisionEngine | None = None


def get() -> WeatherDecisionEngine:
    global _engine
    if _engine is None:
        _engine = WeatherDecisionEngine()
    return _engine