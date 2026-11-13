# brain/nlu/decision/music.py
# DecisionEngine для музыки: Command → Decision.
#
# По EntityState:
#   FOUND      → EXECUTE(play_artist / play_genre / play_track / play_radio / play_mood)
#   ABSENT     → EXECUTE(play_wave)        «включи музыку» / «вруби чего-нибудь»
#   ABSENT+radio → EXECUTE(play_radio)     «включи радио» (без станции)
#   UNRESOLVED → EXECUTE(play_search)      «включи Орион» (локально → Яндекс)
#   AMBIGUOUS  → EXECUTE(play_search)      «включи планка» (5 кандидатов → search)
#
# ── MUSIC_LIKE / MUSIC_DISLIKE / MUSIC_LOCAL_LIKED (v6.9) ──────────
#   Добавлены в supported_intents.
#   MUSIC_LIKE    → EXECUTE(like_current)    — add_like в liked_tracks.json
#   MUSIC_DISLIKE → EXECUTE(dislike_current) — add_dislike + music_next
#   MUSIC_LOCAL_LIKED → EXECUTE(play_local_liked) — локальный плейлист
#
# ── FIX (v6.6): UNRESOLVED/AMBIGUOUS → play_search, не CLARIFY ─────
#   Раньше: UNRESOLVED/AMBIGUOUS → CLARIFY(«нашёл 2: Ария, Кипелия»).
#   Проблема: если фразы CLARIFY нет в responses/music.json — тишина.
#   Теперь: сразу EXECUTE(play_search(query)) — music worker сам:
#     1. search_local (точный → фонетика → fuzzy)
#     2. если нет — Яндекс (search_track)
#
# ── FIX (v6.7): query — сырой текст, очищенный от служебных слов ──
#   Раньше: query = entity.value (кандидат — «блатняка», не «планка»).
#   Теперь: query = _clean_search_query(command.normalized_text).
#   «включи группу планка» → «планка».
#   Служебные слова — в config/search_noise.json (не в коде).

import json
from pathlib import Path

from brain.nlu.types import (
    Command, Decision, DecisionAction, ClarifyRequest,
    Entity, EntityState, Intent,
)
from shared import config as cfg
from shared.log import log


# ── phrase_set для CLARIFY (оставлены для совместимости, не используются) ──
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
    "search": "play_search",
    "local_liked": "play_local_liked",
}


# ── Служебные слова из config/search_noise.json ──────────────
_NOISE_FILE = Path(cfg.BASE_DIR) / "config" / "search_noise.json"
_SEARCH_NOISE: set[str] | None = None


def _load_search_noise() -> set[str]:
    """Загрузить noise_words из config/search_noise.json. Лениво, 1 раз."""
    global _SEARCH_NOISE
    if _SEARCH_NOISE is not None:
        return _SEARCH_NOISE
    if not _NOISE_FILE.exists():
        log(f"decision.music: {_NOISE_FILE} не найден — noise пуст",
            tag="BRAIN", level="WARN")
        _SEARCH_NOISE = set()
        return _SEARCH_NOISE
    try:
        with open(_NOISE_FILE, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        words = data.get("noise_words", [])
        if isinstance(words, list):
            _SEARCH_NOISE = {w.lower().strip() for w in words if isinstance(w, str) and w.strip()}
        else:
            _SEARCH_NOISE = set()
    except Exception as e:
        log(f"decision.music: search_noise load: {e}",
            tag="BRAIN", level="WARN")
        _SEARCH_NOISE = set()
    return _SEARCH_NOISE


def _clean_search_query(text: str) -> str:
    """Убрать служебные слова из запроса. «включи группу планка» → «планка»."""
    if not text:
        return ""
    noise = _load_search_noise()
    words = text.lower().split()
    cleaned = [w for w in words if w not in noise]
    return " ".join(cleaned).strip()


class MusicDecisionEngine:
    """DecisionEngine для музыкальных интентов.

    Поддерживает MUSIC_PLAY, MUSIC_WAVE, MUSIC_LIKE, MUSIC_DISLIKE,
    MUSIC_LOCAL_LIKED.
    MUSIC_STOP/PAUSE/RESUME/NEXT/PREV — не сюда (controls, не NLU-решение).
    """

    supported_intents: tuple[Intent, ...] = (
        Intent.MUSIC_PLAY,
        Intent.MUSIC_WAVE,
        # v6.9: лайки / дизлайки / локальный плейлист.
        Intent.MUSIC_LIKE,
        Intent.MUSIC_DISLIKE,
        Intent.MUSIC_LOCAL_LIKED,
    )

    def supports(self, intent: Intent) -> bool:
        return intent in self.supported_intents

    def resolve(self, command: Command) -> Decision:
        """Command → Decision. Не бросает."""
        if command is None:
            return self._fallback(None, "command is None")

        try:
            if not self.supports(command.intent):
                return self._fallback(command, f"intent {command.intent.value} not supported")

            # v6.9: лайки / дизлайки — без entity, action фиксирован.
            if command.intent == Intent.MUSIC_LIKE:
                command.action = "like_current"
                return Decision(
                    command=command,
                    action=DecisionAction.EXECUTE,
                    reason="MUSIC_LIKE → like_current",
                    confidence=command.confidence,
                )
            if command.intent == Intent.MUSIC_DISLIKE:
                command.action = "dislike_current"
                return Decision(
                    command=command,
                    action=DecisionAction.EXECUTE,
                    reason="MUSIC_DISLIKE → dislike_current",
                    confidence=command.confidence,
                )
            if command.intent == Intent.MUSIC_LOCAL_LIKED:
                command.action = "play_local_liked"
                return Decision(
                    command=command,
                    action=DecisionAction.EXECUTE,
                    reason="MUSIC_LOCAL_LIKED → play_local_liked",
                    confidence=command.confidence,
                )

            if command.intent == Intent.MUSIC_WAVE:
                return self._execute_wave(command)

            return self._resolve_play(command)

        except Exception as e:
            log(f"decision.music: {e}", tag="BRAIN", level="WARN")
            return self._fallback(command, f"exception: {e}")

    # ========================================================
    # MUSIC_PLAY
    # ========================================================
    def _resolve_play(self, command: Command) -> Decision:
        entity = command.entity

        if entity is None:
            return self._execute_wave(command, reason="entity None → ABSENT")

        # RADIO: «включи радио» без станции → play_radio (без value).
        if entity.state == EntityState.ABSENT and entity.type == "radio":
            command.action = "play_radio"
            return Decision(
                command=command,
                action=DecisionAction.EXECUTE,
                reason="entity ABSENT type=radio → play_radio (без станции)",
                confidence=entity.match_confidence,
            )

        if entity.state == EntityState.ABSENT:
            return self._execute_wave(command, reason="entity ABSENT → play_wave")

        if entity.state == EntityState.FOUND:
            return self._execute_found(command, entity)

        # ── FIX (v6.6): UNRESOLVED / AMBIGUOUS → play_search ──
        if entity.state in (EntityState.UNRESOLVED, EntityState.AMBIGUOUS):
            return self._execute_search(command, entity)

        return self._fallback(command, f"unknown entity state {entity.state}")

    def _execute_found(self, command: Command, entity: Entity) -> Decision:
        """FOUND → EXECUTE с action по entity.type."""
        action = _TYPE_TO_ACTION.get(entity.type or "")
        if action is None:
            return self._fallback(
                command,
                f"FOUND but unknown entity.type={entity.type!r}",
            )

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
    # UNRESOLVED / AMBIGUOUS → play_search (локально → Яндекс)
    # ========================================================
    def _execute_search(self, command: Command, entity: Entity) -> Decision:
        """UNRESOLVED / AMBIGUOUS → EXECUTE(play_search).

        query — СЫРОЙ текст пользователя (normalized_text), ОЧИЩЕННЫЙ
        от «включи», «поставь», «группу», «трек» и т.д.
        НЕ entity.value (кандидат — может быть не тем: «блатняка» вместо «планка»).
        """
        candidates = entity.candidates or []

        # 1. Сырой текст пользователя (normalized_text) → чистим.
        raw = (command.normalized_text or "").strip()
        query = _clean_search_query(raw)

        # 2. Fallback: entity.value → первый кандидат.
        if not query:
            query = (entity.value or "").strip()
        if not query and candidates:
            query = (candidates[0].value or "").strip()
        if not query:
            return self._fallback(
                command, f"{entity.state.value} but empty query",
            )

        command.action = "play_search"
        command.entity = Entity(
            type="search",
            value=query,
            state=EntityState.FOUND,
            match_confidence=entity.match_confidence,
            source=f"{entity.state.value.lower()}_fallback",
        )
        return Decision(
            command=command,
            action=DecisionAction.EXECUTE,
            reason=f"{entity.state.value} {len(candidates)} candidates "
                   f"→ play_search({query!r})",
            confidence=entity.match_confidence,
        )

    # ========================================================
    # FALLBACK
    # ========================================================
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
_engine: MusicDecisionEngine | None = None


def get() -> MusicDecisionEngine:
    global _engine
    if _engine is None:
        _engine = MusicDecisionEngine()
    return _engine