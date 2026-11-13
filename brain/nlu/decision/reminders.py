# brain/nlu/decision/reminders.py
# DecisionEngine для напоминаний.
#
# По интенту:
#   REMINDER_ADD:
#     FOUND (delay/datetime) → EXECUTE(add)
#     ABSENT                 → CLARIFY(REMINDER_ADD_CLARIFY)
#   REMINDER_WHEN:
#     FOUND (text) → EXECUTE(when)
#     ABSENT       → FALLBACK (не понял)
#   REMINDER_LIST:
#     FOUND (date) | ABSENT → EXECUTE(list)
#   REMINDER_DONE:
#     FOUND (text) → EXECUTE(done)
#     ABSENT       → CLARIFY(REMINDER_DONE_CLARIFY)
#
# CLARIFY → pipeline ставит PendingCommand (TTL 60с). Следующий текст
# попадёт в _try_fill_pending → DateTimeExtractor/ReminderTextExtractor
# заполнит entity → EXECUTE. Пользователь говорит второй раз «Макс, ...».
#
# FALLBACK для WHEN-ABSENT — потому что «когда» без объекта не имеет
# смысла; router отдаст в LLM / NOT_UNDERSTOOD.

from brain.nlu.types import (
    Command, Decision, DecisionAction, ClarifyRequest,
    EntityState, Intent,
)
from shared.log import log


class RemindersDecisionEngine:
    """DecisionEngine для REMINDER_*."""

    supported_intents = (
        Intent.REMINDER_ADD,
        Intent.REMINDER_WHEN,
        Intent.REMINDER_LIST,
        Intent.REMINDER_DONE,
    )

    def supports(self, intent: Intent) -> bool:
        return intent in self.supported_intents

    def resolve(self, command: Command) -> Decision:
        if command is None:
            return self._fallback(None, "command is None")

        try:
            if not self.supports(command.intent):
                return self._fallback(
                    command, f"intent {command.intent.value} not supported",
                )

            entity = command.entity
            state = entity.state if entity is not None else EntityState.ABSENT

            # ── REMINDER_ADD ──
            if command.intent == Intent.REMINDER_ADD:
                if state == EntityState.FOUND and entity.type in ("delay", "datetime"):
                    command.action = "add"
                    return Decision(
                        command=command,
                        action=DecisionAction.EXECUTE,
                        reason=f"ADD {entity.type}={entity.value!r} → add",
                        confidence=command.confidence,
                    )
                # ABSENT или FOUND не того типа — clarify
                return self._clarify(
                    command,
                    "REMINDER_ADD_CLARIFY",
                    reason="ADD без delay/datetime",
                )

            # ── REMINDER_WHEN ──
            if command.intent == Intent.REMINDER_WHEN:
                if state == EntityState.FOUND and entity.type == "text" and entity.value:
                    command.action = "when"
                    return Decision(
                        command=command,
                        action=DecisionAction.EXECUTE,
                        reason=f"WHEN text={entity.value!r} → when",
                        confidence=command.confidence,
                    )
                return self._fallback(command, "WHEN без text")

            # ── REMINDER_LIST ──
            if command.intent == Intent.REMINDER_LIST:
                if state == EntityState.FOUND and entity.type == "date":
                    command.action = "list"
                    return Decision(
                        command=command,
                        action=DecisionAction.EXECUTE,
                        reason=f"LIST date={entity.value!r} → list",
                        confidence=command.confidence,
                    )
                # ABSENT — весь список
                command.action = "list"
                return Decision(
                    command=command,
                    action=DecisionAction.EXECUTE,
                    reason="LIST без даты → list (всё)",
                    confidence=command.confidence,
                )

            # ── REMINDER_DONE ──
            if command.intent == Intent.REMINDER_DONE:
                if state == EntityState.FOUND and entity.type == "text" and entity.value:
                    # «готово» без хвоста — extractor вернёт «готово»,
                    # это не текст для mark_done_by_text → clarify.
                    val = entity.value.strip().lower()
                    if val in ("готово", "сделано", "выполнено"):
                        return self._clarify(
                            command,
                            "REMINDER_DONE_CLARIFY",
                            reason="DONE без текста",
                        )
                    command.action = "done"
                    return Decision(
                        command=command,
                        action=DecisionAction.EXECUTE,
                        reason=f"DONE text={entity.value!r} → done",
                        confidence=command.confidence,
                    )
                return self._clarify(
                    command,
                    "REMINDER_DONE_CLARIFY",
                    reason="DONE без text",
                )

            return self._fallback(command, f"unhandled {command.intent.value}")

        except Exception as e:
            log(f"decision.reminders: {e}", tag="BRAIN", level="WARN")
            return self._fallback(command, f"exception: {e}")

    # ── helpers ─────────────────────────────────────────────
    def _clarify(self, command: Command, phrase_set: str, reason: str) -> Decision:
        return Decision(
            command=command,
            action=DecisionAction.CLARIFY,
            reason=reason,
            confidence=command.confidence,
            clarify=ClarifyRequest(phrase_set=phrase_set, kwargs={}, reason=reason),
        )

    def _fallback(self, command: Command | None, reason: str) -> Decision:
        if command is None:
            command = Command(
                intent=Intent.UNKNOWN, entity=None, skill_name=None,
                action=None, confidence=0.0,
                normalized_text="", original_text="",
            )
        return Decision(
            command=command,
            action=DecisionAction.FALLBACK,
            reason=reason,
            confidence=0.0,
        )


# ── Синглтон ────────────────────────────────────────────────
_engine: RemindersDecisionEngine | None = None


def get() -> RemindersDecisionEngine:
    global _engine
    if _engine is None:
        _engine = RemindersDecisionEngine()
    return _engine