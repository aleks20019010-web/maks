# brain/nlu/decision/music.py
# DecisionEngine для музыки: Command → Decision.
#
# По EntityState:
#   FOUND      → EXECUTE(play_artist / play_genre / play_track)
#   ABSENT     → EXECUTE(play_wave)        «включи музыку» / «вруби чего-нибудь»
#   UNRESOLVED → CLARIFY(«не нашёл X, может, Y?»)  «включи Орион»
#   AMBIGUOUS  → CLARIFY(«нашёл 2: Ария, Кипелия. Какой?»)
#
# ПОЧЕМУ DecisionEngine, А НЕ ХАРДКОД В PIPELINE:
#   У музыки своя логика (FOUND artist → play_artist, FOUND genre → play_genre).
#   У погоды будет другая. У напоминаний — третья. Если всё в pipeline —
#   pipeline превратится в свалку if/elif. DecisionEngine — свой на каждый
#   тип. pipeline зовёт decision.resolve(command) и не знает деталей.
#
# ЧЕГО DecisionEngine НЕ ДЕЛАЕТ:
#   - не извлекает entity (это entity/*)
#   - не выбирает скил (это skill_resolver)
#   - не говорит (это router)
#   Он только отвечает: «что делать с этой Command?».

from brain.nlu.types import (
    Command, Decision, DecisionAction, ClarifyRequest,
    Entity, EntityState, Intent,
)
from shared.log import log


# ── phrase_set для CLARIFY ──────────────────────────────────
# Ключи в responses/music.json. Если ключа нет — router скажет
# NOT_UNDERSTOOD (fallback). Добавим фразы позже, если понадобятся.
CLARIFY_UNRESOLVED_ARTIST = "MUSIC_CLARIFY_UNRESOLVED_ARTIST"
CLARIFY_AMBIGUOUS_ARTIST = "MUSIC_CLARIFY_AMBIGUOUS_ARTIST"
CLARIFY_AMBIGUOUS_GENERIC = "MUSIC_CLARIFY_AMBIGUOUS"


# ── Entity.type → action ────────────────────────────────────
_TYPE_TO_ACTION: dict[str, str] = {
    "artist": "play_artist",
    "genre": "play_genre",
    "track": "play_track",
    "radio": "play_radio",
    "mood": "play_mood",
}


class MusicDecisionEngine:
    """DecisionEngine для музыкальных интентов.

    Поддерживает MUSIC_PLAY и MUSIC_WAVE.
    MUSIC_STOP/PAUSE/RESUME/NEXT/PREV — не сюда (controls, не NLU-решение).
    """

    supported_intents: tuple[Intent, ...] = (
        Intent.MUSIC_PLAY,
        Intent.MUSIC_WAVE,
    )

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
                return self._fallback(command, f"intent {command.intent.value} not supported")

            # MUSIC_WAVE — всегда EXECUTE(play_wave). Параметра нет.
            if command.intent == Intent.MUSIC_WAVE:
                return self._execute_wave(command)

            # MUSIC_PLAY — смотрим entity.
            return self._resolve_play(command)

        except Exception as e:
            log(f"decision.music: {e}", tag="BRAIN", level="WARN")
            return self._fallback(command, f"exception: {e}")

    # ========================================================
    # MUSIC_PLAY
    # ========================================================
    def _resolve_play(self, command: Command) -> Decision:
        entity = command.entity

        # Нет entity — считаем ABSENT (пользователь не назвал параметр).
        if entity is None:
            return self._execute_wave(command, reason="entity None → ABSENT")

        if entity.state == EntityState.ABSENT:
            return self._execute_wave(command, reason="entity ABSENT → play_wave")

        if entity.state == EntityState.FOUND:
            return self._execute_found(command, entity)

        if entity.state == EntityState.UNRESOLVED:
            return self._clarify_unresolved(command, entity)

        if entity.state == EntityState.AMBIGUOUS:
            return self._clarify_ambiguous(command, entity)

        # Неизвестный state — FALLBACK, не молчим.
        return self._fallback(command, f"unknown entity state {entity.state}")

    def _execute_found(self, command: Command, entity: Entity) -> Decision:
        """FOUND → EXECUTE с action по entity.type."""
        action = _TYPE_TO_ACTION.get(entity.type or "")
        if action is None:
            # FOUND с неизвестным типом (например, date для музыки) — FALLBACK.
            return self._fallback(
                command,
                f"FOUND but unknown entity.type={entity.type!r}",
            )

        # Обновим command.action, чтобы router/скил знал, что делать.
        command.action = action
        return Decision(
            command=command,
            action=DecisionAction.EXECUTE,
            reason=f"FOUND {entity.type}={entity.value!r} → {action}",
            confidence=min(command.confidence, entity.match_confidence),
        )

    def _execute_wave(self, command: Command, reason: str = "") -> Decision:
        """ABSENT → EXECUTE(play_wave). Параметр не назван — включаем волну."""
        command.action = "play_wave"
        return Decision(
            command=command,
            action=DecisionAction.EXECUTE,
            reason=reason or "ABSENT → play_wave",
            confidence=command.confidence,
        )

    # ========================================================
    # CLARIFY
    # ========================================================
    def _clarify_unresolved(self, command: Command, entity: Entity) -> Decision:
        """UNRESOLVED → CLARIFY(«не нашёл X, может, Y?»).

        entity.value — что пользователь назвал («орион»).
        Мы не знаем «Y» (альтернативу) на этом этапе — match не нашёл.
        Поэтому вопрос generic: «не нашёл X, уточните». Альтернативы —
        забота скила/LLM, если понадобится. NLU не выдумывает.
        """
        candidate = entity.value or ""
        clarify = ClarifyRequest(
            phrase_set=CLARIFY_UNRESOLVED_ARTIST,
            kwargs={"candidate": candidate},
            reason="unresolved_artist",
        )
        return Decision(
            command=command,
            action=DecisionAction.CLARIFY,
            reason=f"UNRESOLVED candidate={candidate!r}",
            confidence=entity.match_confidence,
            clarify=clarify,
        )

    def _clarify_ambiguous(self, command: Command, entity: Entity) -> Decision:
        """AMBIGUOUS → CLARIFY(«нашёл 2: Ария, Кипелия. Какой?»).

        entity.candidates — список с value и confidence.
        Собираем «Ария, Кипелов» для подстановки в фразу.
        """
        candidates = entity.candidates or []
        if not candidates:
            return self._fallback(command, "AMBIGUOUS but no candidates")

        # Уникальные значения, до 5 штук.
        seen = set()
        values: list[str] = []
        for c in candidates:
            if c.value and c.value not in seen:
                seen.add(c.value)
                values.append(c.value)
            if len(values) >= 5:
                break

        if not values:
            return self._fallback(command, "AMBIGUOUS but empty values")

        candidates_str = ", ".join(values)
        clarify = ClarifyRequest(
            phrase_set=CLARIFY_AMBIGUOUS_GENERIC,
            kwargs={
                "candidates": candidates_str,
                "count": len(values),
            },
            reason="ambiguous_artist",
        )
        return Decision(
            command=command,
            action=DecisionAction.CLARIFY,
            reason=f"AMBIGUOUS {len(values)} candidates",
            confidence=entity.match_confidence,
            clarify=clarify,
        )

    # ========================================================
    # FALLBACK
    # ========================================================
    def _fallback(self, command: Command | None, reason: str) -> Decision:
        """FALLBACK — router отдаст в LLM / NOT_UNDERSTOOD."""
        if command is None:
            # Заглушка: пустая Command, чтобы Decision не был None-command.
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
_engine: MusicDecisionEngine | None = None


def get() -> MusicDecisionEngine:
    global _engine
    if _engine is None:
        _engine = MusicDecisionEngine()
    return _engine