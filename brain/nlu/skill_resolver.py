# brain/nlu/skill_resolver.py
# SkillResolver: выбор подходящего скила по Command.
#
# Два пути:
#   1. По intent — через _INTENT_TO_SKILL (быстрый, детерминированный).
#   2. По тексту — fallback на skills_base.find_handler (если intent неизвестен
#      или скил по intent не зарегистрирован).
#
# _INTENT_TO_SKILL переехал сюда из brain/router.py (этап 4).
# router.py больше не знает про соответствие intent → skill_name.
# Он зовёт pipeline.process(), а pipeline зовёт skill_resolver.resolve().
#
# ── MUSIC_LIKE / MUSIC_DISLIKE / MUSIC_LOCAL_LIKED (v6.9) ──────────
#   Добавлены в _INTENT_TO_SKILL → "music".
#   MUSIC_LIKE    → music.like_current    (add_like в liked_tracks.json)
#   MUSIC_DISLIKE → music.dislike_current (add_dislike + music_next)
#   MUSIC_LOCAL_LIKED → music.play_local_liked (локальный плейлист)
#   Раньше: resolver: нет скила для MUSIC_LIKE → FALLBACK → LLM (4-15 сек).

from brain.nlu.types import Command, Intent
from brain.skills import base as skills_base
from shared.log import log


# ── Intent → skill_name ─────────────────────────────────────
# Один источник правды. Перенесено из brain/router.py.
# Если intent нет в словаре — resolver попробует find_handler по тексту.
_INTENT_TO_SKILL: dict[Intent, str | None] = {
    Intent.MUSIC_PLAY: "music",
    Intent.MUSIC_WAVE: "music",
    # MUSIC_LIKE / MUSIC_DISLIKE / MUSIC_LOCAL_LIKED (v6.9) → music.
    Intent.MUSIC_LIKE: "music",
    Intent.MUSIC_DISLIKE: "music",
    Intent.MUSIC_LOCAL_LIKED: "music",
    Intent.MUSIC_STOP: "controls",
    Intent.MUSIC_PAUSE: "controls",
    Intent.MUSIC_RESUME: "controls",
    Intent.MUSIC_NEXT: "controls",
    Intent.MUSIC_PREV: "controls",
    # MUSIC_NOW_PLAYING (2026-11-08): «что играет» / «что за песня».
    # controls.now_playing → protocol.music_now_playing_request → music →
    # event_now_playing → brain → TTS. Быстро (0.5 сек), без LLM.
    Intent.MUSIC_NOW_PLAYING: "controls",
    Intent.VOLUME_UP: "controls",
    Intent.VOLUME_DOWN: "controls",
    Intent.VOLUME_SET: "controls",
    Intent.WEATHER_QUERY: "weather",
    Intent.SYSTEM_STATUS: "system",
    Intent.REMINDER_ADD: "reminders",   # пока нет скила — уйдёт в _handle_reminders раньше
    Intent.CLEANUP_DISK: "cleanup",
    Intent.CLEANUP_RECYCLE: "cleanup_recycle",
    Intent.SMALLTALK: "smalltalk",
    Intent.LLM_QUESTION: "llm",
    Intent.UNKNOWN: None,
}


def resolve(command: Command) -> skills_base.Skill | None:
    """Выбрать скил для Command.

    Приоритет:
      1. По intent — _INTENT_TO_SKILL → find_by_name.
      2. По тексту — find_handler (может вернуть скил, у которого can_handle
         сработал на original_text, даже если intent — UNKNOWN).
      3. None — никто не взялся.

    Возвращает Skill или None. Не бросает.
    """
    if command is None:
        return None

    # 1. По intent
    skill_name = _INTENT_TO_SKILL.get(command.intent)
    if skill_name:
        skill = skills_base.find_by_name(skill_name)
        if skill is not None:
            log(
                f"resolver: {command.intent.value} → {skill_name} "
                f"(по intent)",
                tag="BRAIN",
            )
            return skill
        log(
            f"resolver: {command.intent.value} → {skill_name} "
            f"(скил не зарегистрирован, пробую find_handler)",
            tag="BRAIN", level="WARN",
        )

    # 2. По тексту — fallback
    if command.original_text:
        skill = skills_base.find_handler(command.original_text)
        if skill is not None:
            log(
                f"resolver: «{command.original_text}» → {skill.name} "
                f"(по тексту, find_handler)",
                tag="BRAIN",
            )
            return skill

    # 3. Никто
    log(
        f"resolver: нет скила для {command.intent.value} "
        f"«{command.original_text}»",
        tag="BRAIN", level="WARN",
    )
    return None


def skill_name_for_intent(intent: Intent) -> str | None:
    """Вернуть имя скила для intent (без поиска в реестре).

    Полезно для диагностики и тестов: «какой скил ДОЛЖЕН обработать intent».
    Не путать с resolve() — тот ищет реально зарегистрированный скил.
    """
    return _INTENT_TO_SKILL.get(intent)


def registered_intents() -> dict[Intent, str | None]:
    """Вернуть копию _INTENT_TO_SKILL (для тестов/диагностики)."""
    return dict(_INTENT_TO_SKILL)