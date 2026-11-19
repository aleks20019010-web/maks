# brain/nlu/decision/apps.py
# DecisionEngine для управления программами (APP_OPEN, APP_CLOSE).

from brain.nlu.types import (
    ClarifyRequest, Command, Decision, DecisionAction, EntityState, Intent,
)
from shared.log import log


class AppsDecisionEngine:
    supported_intents: tuple[Intent, ...] = (Intent.APP_OPEN, Intent.APP_CLOSE)

    def supports(self, intent: Intent) -> bool:
        return intent in self.supported_intents

    def resolve(self, command: Command) -> Decision:
        if command is None or not self.supports(command.intent):
            return Decision(
                command=command,
                action=DecisionAction.FALLBACK,
                reason="unsupported intent in AppsDecisionEngine",
                confidence=0.0,
            )

        is_open = (command.intent == Intent.APP_OPEN)
        action_name = "app_open" if is_open else "app_close"
        ent = command.entity

        # 1. Программа не названа («открой» / «закрой») → переспрашиваем (pending)
        if ent is None or ent.state == EntityState.ABSENT:
            command.action = action_name
            phrase_key = "APP_OPEN_CLARIFY" if is_open else "APP_CLOSE_CLARIFY"
            return Decision(
                command=command,
                action=DecisionAction.CLARIFY,
                reason=f"{command.intent.value} ABSENT → clarify",
                confidence=command.confidence,
                clarify=ClarifyRequest(
                    phrase_set=phrase_key,
                    reason="missing_app_name",
                ),
            )

        # 2. Найдена в config/apps.json → запускаем / закрываем
        if ent.state == EntityState.FOUND and ent.value:
            command.action = action_name
            return Decision(
                command=command,
                action=DecisionAction.EXECUTE,
                reason=f"FOUND app={ent.value!r} → {action_name}",
                confidence=min(command.confidence, ent.match_confidence),
            )

        # 3. Неизвестная программа («открой фотошоп», которого нет в apps.json)
        command.action = "app_unknown"
        log(f"decision.apps: неизвестная программа {ent.value!r}", tag="BRAIN")
        return Decision(
            command=command,
            action=DecisionAction.EXECUTE,
            reason=f"UNRESOLVED app={ent.value!r} → app_unknown",
            confidence=command.confidence,
        )


_engine: AppsDecisionEngine | None = None


def get() -> AppsDecisionEngine:
    global _engine
    if _engine is None:
        _engine = AppsDecisionEngine()
    return _engine