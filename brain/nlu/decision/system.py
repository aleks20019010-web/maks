# brain/nlu/decision/system.py
# DecisionEngine для system: Command → Decision.
#
# SYSTEM_STATUS → EXECUTE(action="status").
#   ACK + сбор CPU/RAM/дисков + TTS — всё внутри SystemSkill.handle(Command).
#   DecisionEngine только говорит: «это EXECUTE, action=status».
#
# ПОЧЕМУ DecisionEngine, А НЕ ХАРДКОД В PIPELINE:
#   Без DecisionEngine pipeline для SYSTEM_STATUS → FALLBACK → LLM
#   (медленно, 4.5 сек, и LLM не соберёт CPU/RAM). С DecisionEngine —
#   EXECUTE → SystemSkill.handle(Command) → ACK → сбор → TTS.
#
# ЧЕГО DecisionEngine НЕ ДЕЛАЕТ:
#   - не собирает CPU/RAM/диски (это SystemSkill)
#   - не выбирает скил (это skill_resolver: SYSTEM_STATUS → system)
#   - не говорит (это router → SystemSkill → ipc.put(TTS_INBOX))

from brain.nlu.types import (
    Command, Decision, DecisionAction, Intent,
)
from shared.log import log


class SystemDecisionEngine:
    """DecisionEngine для system (SYSTEM_STATUS → EXECUTE(status))."""

    supported_intents: tuple[Intent, ...] = (Intent.SYSTEM_STATUS,)

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

            command.action = "status"
            return Decision(
                command=command,
                action=DecisionAction.EXECUTE,
                reason="SYSTEM_STATUS → status",
                confidence=command.confidence,
            )
        except Exception as e:
            log(f"decision.system: {e}", tag="BRAIN", level="WARN")
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


_engine: SystemDecisionEngine | None = None


def get() -> SystemDecisionEngine:
    global _engine
    if _engine is None:
        _engine = SystemDecisionEngine()
    return _engine