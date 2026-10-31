# brain/nlu/decision/controls.py
# DecisionEngine для controls: Command → Decision.
#
# По Intent:
#   VOLUME_UP    → EXECUTE(action="volume_up")
#   VOLUME_DOWN  → EXECUTE(action="volume_down")
#   VOLUME_SET   → EXECUTE(action="volume_set")   (value — из original_text, parse_volume)
#   MUSIC_PAUSE  → EXECUTE(action="pause")
#   MUSIC_STOP   → EXECUTE(action="stop")
#   MUSIC_RESUME → EXECUTE(action="resume")
#   MUSIC_NEXT   → EXECUTE(action="next")
#   MUSIC_PREV   → EXECUTE(action="prev")
#
# ПОЧЕМУ DecisionEngine, А НЕ ХАРДКОД В PIPELINE:
#   У controls своя логика (intent → action). Без DecisionEngine pipeline
#   для VOLUME_UP → FALLBACK → LLM (медленно, 4.5 сек). С DecisionEngine —
#   EXECUTE → controls.handle(Command) → ipc.put → быстро.
#
# VOLUME_SET: entity НЕТ (числа не извлекаются в entity/music). controls.handle
#   сам зовёт parse_volume(command.original_text) — «громкость 50» → 50.
#   Это не дублирование: parse_volume уже есть, Command несёт original_text.
#
# ЧЕГО DecisionEngine НЕ ДЕЛАЕТ:
#   - не извлекает entity (это entity/*)
#   - не выбирает скил (это skill_resolver)
#   - не говорит (это router)
#   Он только отвечает: «что делать с этой Command?».

from brain.nlu.types import (
    Command, Decision, DecisionAction, Intent,
)
from shared.log import log


# ── Intent → action ─────────────────────────────────────────
_INTENT_TO_ACTION: dict[Intent, str] = {
    Intent.VOLUME_UP: "volume_up",
    Intent.VOLUME_DOWN: "volume_down",
    Intent.VOLUME_SET: "volume_set",
    Intent.MUSIC_PAUSE: "pause",
    Intent.MUSIC_STOP: "stop",
    Intent.MUSIC_RESUME: "resume",
    Intent.MUSIC_NEXT: "next",
    Intent.MUSIC_PREV: "prev",
}


class ControlsDecisionEngine:
    """DecisionEngine для controls (громкость, пауза, стоп, next/prev)."""

    supported_intents: tuple[Intent, ...] = tuple(_INTENT_TO_ACTION.keys())

    def supports(self, intent: Intent) -> bool:
        return intent in self.supported_intents

    def resolve(self, command: Command) -> Decision:
        """Command → Decision. Не бросает.

        При ошибке — FALLBACK (router отдаст в LLM / NOT_UNDERSTOOD).
        """
        if command is None:
            return self._fallback(None, "command is None")

        try:
            if not self.supports(command.intent):
                return self._fallback(
                    command,
                    f"intent {command.intent.value} not supported",
                )

            action = _INTENT_TO_ACTION[command.intent]
            command.action = action
            return Decision(
                command=command,
                action=DecisionAction.EXECUTE,
                reason=f"{command.intent.value} → {action}",
                confidence=command.confidence,
            )
        except Exception as e:
            log(f"decision.controls: {e}", tag="BRAIN", level="WARN")
            return self._fallback(command, f"exception: {e}")

    def _fallback(self, command: Command | None, reason: str) -> Decision:
        """FALLBACK — router отдаст в LLM / NOT_UNDERSTOOD."""
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


# ── Синглтон ────────────────────────────────────────────────
_engine: ControlsDecisionEngine | None = None


def get() -> ControlsDecisionEngine:
    global _engine
    if _engine is None:
        _engine = ControlsDecisionEngine()
    return _engine