# brain/nlu/pipeline.py
# NLU Pipeline: текст → Decision.
#
# Поток:
#   normalize(text)
#     ↓
#   context.get_pending()?  ← если активен, пробуем заполнить entity, НЕ зовём intent
#     ↓ (нет pending)
#   intent.resolve(normalized) → Intent + confidence
#     ↓
#   skill_resolver.resolve(command) → Skill
#     ↓
#   entity_extractor.extract_candidate + match → Entity
#     ↓
#   decision_engine.resolve(command) → Decision
#     ↓
#   log_diagnostic(pipeline, ...)
#     ↓
#   return Decision
#
# ПОЧЕМУ Context ДО IntentResolver:
#   architecture.md: «Context применяется до IntentResolver. Если pending
#   активен и TTL не истёк — заполнение entity, не новый intent.
#   IntentResolver не вызывается».
#   Это ключевое: «арию» после «не нашёл Орион» — это ответ, не новая
#   команда. Если звать IntentResolver — получим MUSIC_PLAY с ABSENT
#   → play_wave. Неправильно. С контекстом — заполняем entity в pending.
#
# ЧЕГО PIPELINE НЕ ДЕЛАЕТ:
#   - не говорит (это router)
#   - не выполняет скилы (это router)
#   - не знает про responses/*.json (это router/clarify)
#   Он только: текст → Decision.
#
# РАСШИРЯЕМОСТЬ:
#   Регистрация экстракторов/движков — через _EXTRACTORS / _DECISIONS.
#   MusicEntityExtractor + MusicDecisionEngine — единственные сейчас.
#   Weather — адаптер отдельно (этап 10). Reminders — потом.
#   Добавить новый тип: дописать в словарь, не трогая pipeline.

import time
from typing import Optional

from brain.nlu import intent as intent_resolver
from brain.nlu import normalizer
from brain.nlu import skill_resolver
from brain.nlu import logging as nlu_log
from brain.nlu.context import (
    NLUContext, PendingCommand, get as get_context,
)
from brain.nlu.types import (
    Command, Decision, DecisionAction, Entity, EntityState, Intent,
)
from shared.log import log


# ── Регистрация экстракторов / движков ─────────────────────
# Music — единственный на этом этапе. Weather — адаптер отдельно.
# Reminders — потом (у них нет entity в NLU-смысле, только date/time).
def _load_extractors() -> dict[Intent, object]:
    """Intent → EntityExtractor. Ленивая загрузка (импорт тяжёлый)."""
    from brain.nlu.entity.music import get as get_music_entity
    return {
        Intent.MUSIC_PLAY: get_music_entity(),
        Intent.MUSIC_WAVE: get_music_entity(),
    }


def _load_decisions() -> dict[Intent, object]:
    """Intent → DecisionEngine. Ленивая загрузка."""
    from brain.nlu.decision.music import get as get_music_decision
    from brain.nlu.decision.controls import get as get_controls_decision
    from brain.nlu.decision.weather import get as get_weather_decision
    return {
        Intent.MUSIC_PLAY: get_music_decision(),
        Intent.MUSIC_WAVE: get_music_decision(),
        Intent.VOLUME_UP: get_controls_decision(),
        Intent.VOLUME_DOWN: get_controls_decision(),
        Intent.VOLUME_SET: get_controls_decision(),
        Intent.MUSIC_PAUSE: get_controls_decision(),
        Intent.MUSIC_STOP: get_controls_decision(),
        Intent.MUSIC_RESUME: get_controls_decision(),
        Intent.MUSIC_NEXT: get_controls_decision(),
        Intent.MUSIC_PREV: get_controls_decision(),
        Intent.WEATHER_QUERY: get_weather_decision(),
    }


# ── Кэш (ленивый, один раз) ─────────────────────────────────
_extractors: Optional[dict[Intent, object]] = None
_decisions: Optional[dict[Intent, object]] = None


def _get_extractors() -> dict[Intent, object]:
    global _extractors
    if _extractors is None:
        _extractors = _load_extractors()
    return _extractors


def _get_decisions() -> dict[Intent, object]:
    global _decisions
    if _decisions is None:
        _decisions = _load_decisions()
    return _decisions


# ── Главная функция ────────────────────────────────────────
def process(text: str, context: Optional[NLUContext] = None) -> Decision:
    """Обработать текст → Decision. Не бросает.

    context — NLUContext. Если None — берём синглтон (get_context()).
    Возвращает Decision. При ошибке — FALLBACK с пустой Command.

    Побочные эффекты:
      - context.add_history(intent, text) — для диагностики
      - context.set_pending(...) / clear_pending() — TTL 60с
      - nlu_log.log_diagnostic(...) — logs/nlu.log
    """
    t0 = time.time()

    # 0. Контекст: явный или синглтон.
    if context is None:
        context = get_context()

    # 1. Normalize
    try:
        normalized = normalizer.normalize(text)
    except Exception as e:
        nlu_log.error("normalizer", f"exception: {e}", text=text)
        return _fallback_empty(text, f"normalizer: {e}")

    if not normalized:
        nlu_log.warn("normalizer", "empty after normalize", text=text)
        return _fallback_empty(text, "empty after normalize")

    nlu_log.debug("normalizer", normalized=normalized, original=text)

    # 2. Context: pending активен?
    #    Если да — пробуем заполнить entity в pending, НЕ зовём intent.
    pending = context.get_pending()
    if pending is not None:
        decision = _try_fill_pending(pending, normalized, text, context)
        if decision is not None:
            nlu_log.info(
                "pipeline",
                decision=decision.action.value,
                reason=decision.reason,
                elapsed=f"{time.time() - t0:.3f}",
            )
            return decision
        # Не смогли заполнить — pending сбрасываем, идём обычным путём.
        nlu_log.warn(
            "context",
            "pending fill failed, clearing",
            pending_intent=pending.intent.value,
        )
        context.clear_pending()

    # 3. Intent
    try:
        intent, confidence = intent_resolver.resolve(normalized)
    except Exception as e:
        nlu_log.error("intent", f"exception: {e}", text=normalized)
        return _fallback_empty(text, f"intent: {e}")

    nlu_log.debug("intent", intent=intent.value, conf=f"{confidence:.2f}")

    # 4. Command (пока без entity — заполним ниже)
    command = Command(
        intent=intent,
        entity=None,
        skill_name=None,
        action=None,
        confidence=confidence,
        normalized_text=normalized,
        original_text=text,
    )

    # 5. SkillResolver
    try:
        skill = skill_resolver.resolve(command)
        if skill is not None:
            command.skill_name = skill.name
    except Exception as e:
        nlu_log.warn("skill_resolver", f"exception: {e}")

    nlu_log.debug("skill_resolver", skill=command.skill_name)

    # 6. Entity (если есть экстрактор для intent)
    extractor = _get_extractors().get(intent)
    if extractor is not None and _supports(extractor, intent):
        try:
            candidate = extractor.extract_candidate(normalized, intent)
            if candidate is None:
                command.entity = Entity.absent()
                nlu_log.debug("entity", state="ABSENT", candidate=None)
            else:
                entity = extractor.match(candidate, intent)
                command.entity = entity
                nlu_log.debug(
                    "entity",
                    state=entity.state.value,
                    type=entity.type,
                    value=entity.value,
                    match_conf=f"{entity.match_confidence:.2f}",
                    source=entity.source,
                )
        except Exception as e:
            nlu_log.warn("entity", f"exception: {e}", intent=intent.value)
            command.entity = Entity.absent()
    else:
        # Нет экстрактора — entity не извлекаем. DecisionEngine решит.
        nlu_log.debug("entity", skipped=f"no extractor for {intent.value}")

    # 7. Decision
    decision_engine = _get_decisions().get(intent)
    if decision_engine is not None and _supports(decision_engine, intent):
        try:
            decision = decision_engine.resolve(command)
        except Exception as e:
            nlu_log.error("decision", f"exception: {e}", intent=intent.value)
            return _fallback_command(command, f"decision: {e}")
    else:
        # Нет decision engine для intent — FALLBACK (router отдаст в LLM).
        decision = _fallback_command(
            command, f"no decision engine for {intent.value}",
        )

    # 8. Context: сброс pending после EXECUTE, или set при CLARIFY.
    if decision.action == DecisionAction.EXECUTE:
        context.clear_pending()
    elif decision.action == DecisionAction.CLARIFY:
        # Пользователь должен ответить — ставим pending на 60с.
        pending_new = PendingCommand(
            intent=command.intent,
            missing=[command.entity.type] if command.entity else [],
            partial_entity=command.entity,
            original_text=text,
        )
        context.set_pending(pending_new)

    # 9. History (для диагностики)
    context.add_history(command.intent, text)

    # 10. Итоговый лог
    elapsed = time.time() - t0
    nlu_log.info(
        "pipeline",
        intent=command.intent.value,
        decision=decision.action.value,
        conf=f"{decision.confidence:.2f}",
        skill=command.skill_name,
        action=command.action,
        reason=decision.reason,
        elapsed=f"{elapsed:.3f}",
    )

    return decision


# ── Pending fill ───────────────────────────────────────────
def _try_fill_pending(
    pending: PendingCommand,
    normalized: str,
    original: str,
    context: NLUContext,
) -> Optional[Decision]:
    """Попробовать заполнить pending новым текстом.

    Возвращает Decision, если получилось (EXECUTE/CLARIFY).
    None — если не получилось (pipeline пойдёт обычным путём).

    Логика:
      - если текст — отмена («отмена», «не надо») → clear_pending, вернуть
        Decision(EXECUTE, action="cancel")? Нет — просто None, пусть
        pipeline пойдёт обычным путём и router скажет «отменил».
        Хотя это странно. Лучше: вернуть Decision(FALLBACK, reason="cancelled")
        и router скажет NOT_UNDERSTOOD. Но пользователь ждёт «отменил».
        Пока: возвращаем None, pending сброшен, pipeline идёт обычным путём.
        «Отмена» без intent'а → FALLBACK → router скажет NOT_UNDERSTOOD.
        Не идеально, но не выдумываем.

      - иначе: пробуем extract_candidate + match для pending.intent.
        Если FOUND — EXECUTE с pending.intent + entity.
        Если UNRESOLVED/AMBIGUOUS — новый CLARIFY (pipeline его обработает).
        Если ABSENT — не получилось, None.
    """
    # Отмена
    if context.is_cancel(normalized):
        nlu_log.info("context", "cancelled", pending_intent=pending.intent.value)
        context.clear_pending()
        return None

    # Есть ли экстрактор для pending.intent?
    extractor = _get_extractors().get(pending.intent)
    if extractor is None or not _supports(extractor, pending.intent):
        return None

    try:
        candidate = extractor.extract_candidate(normalized, pending.intent)
        if candidate is None:
            # Пользователь сказал что-то без параметра — не заполнили.
            return None

        entity = extractor.match(candidate, pending.intent)

        # Собираем Command из pending + entity.
        command = Command(
            intent=pending.intent,
            entity=entity,
            skill_name=None,
            action=None,
            confidence=0.9,  # pending fill — уверенно (пользователь ответил)
            normalized_text=normalized,
            original_text=original,
            context={"pending": True},
        )

        # SkillResolver
        try:
            skill = skill_resolver.resolve(command)
            if skill is not None:
                command.skill_name = skill.name
        except Exception as e:
            nlu_log.warn("skill_resolver", f"pending: {e}")

        # Decision
        decision_engine = _get_decisions().get(pending.intent)
        if decision_engine is None or not _supports(decision_engine, pending.intent):
            return _fallback_command(command, "no decision engine (pending)")

        decision = decision_engine.resolve(command)

        nlu_log.info(
            "context",
            "pending filled",
            pending_intent=pending.intent.value,
            entity_state=entity.state.value,
            decision=decision.action.value,
        )

        # EXECUTE — pending сбрасываем; CLARIFY — оставляем (новый pending).
        if decision.action == DecisionAction.EXECUTE:
            context.clear_pending()
        elif decision.action == DecisionAction.CLARIFY:
            new_pending = PendingCommand(
                intent=pending.intent,
                missing=[entity.type] if entity.type else [],
                partial_entity=entity,
                original_text=original,
            )
            context.set_pending(new_pending)

        context.add_history(command.intent, original)
        return decision

    except Exception as e:
        nlu_log.warn("context", f"pending fill exception: {e}")
        return None


# ── Helpers ────────────────────────────────────────────────
def _supports(obj: object, intent: Intent) -> bool:
    """Поддерживает ли объект intent. Duck typing."""
    if hasattr(obj, "supports"):
        try:
            return bool(obj.supports(intent))
        except Exception:
            return False
    supported = getattr(obj, "supported_intents", ())
    return intent in supported


def _fallback_empty(original: str, reason: str) -> Decision:
    """FALLBACK с пустой Command. Для ошибок до создания Command."""
    command = Command(
        intent=Intent.UNKNOWN,
        entity=None,
        skill_name=None,
        action=None,
        confidence=0.0,
        normalized_text="",
        original_text=original or "",
    )
    return Decision(
        command=command,
        action=DecisionAction.FALLBACK,
        reason=reason,
        confidence=0.0,
    )


def _fallback_command(command: Command, reason: str) -> Decision:
    """FALLBACK для готовой Command."""
    return Decision(
        command=command,
        action=DecisionAction.FALLBACK,
        reason=reason,
        confidence=0.0,
    )