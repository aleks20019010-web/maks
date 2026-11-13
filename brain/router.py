# brain/router.py
# Роутер команд: выбирает навык по тексту и вызывает его.
#
# ВНЕ ДИАЛОГА (CommandSource.VOICE, VOICE_HOT, SYSTEM):
#   pending → fast/skills → NLU pipeline → LLM → NOT_UNDERSTOOD
#   Router решает первым.
#
# В ДИАЛОГЕ (CommandSource.VOICE_DIALOG):
#   smalltalk → LLM → (LLM вернёт command → process_command → router)
#   Router обрабатывает только smalltalk. Всё остальное — LLM.
#
# FIX (NLU v2): NLU-блок заменён на brain/nlu/pipeline.process().
#   router зовёт pipeline.process(), исполняет decision.action.
#
# FIX (этап 9): скилы получают Command.
#
# FIX: CommandSource enum вместо magic strings.
#
# ── FIX (этап 1.1): YES/NO для pending от скилов (cleanup) ───────
#   Раньше: router знал только PENDING_ASK / PENDING_OFFER (music).
#   «Да» на «Очистить безопасное?» (cleanup ставит pending с ключом
#   "cleanup" в brain.pending) → router видел «YES/NO без pending →
#   игнор» → cleanup pending висел 120 сек, потом timeout.
#   Теперь: перед игнором — _handle_pending_confirm(cmd_lower),
#   который проверяет cleanup / cleanup_recycle и вызывает
#   cleanup_confirm / cleanup_recycle_confirm. Скил сам делает pop.
#
# ── REMINDERS → NLU + скил (2026-11-13) ─────────────────────────
#   Раньше: REMINDER_ADD / «что на завтра» / «сделал» обрабатывались
#   здесь (_handle_reminders, fast-path по регуляркам и спискам фраз).
#   Это нарушало архитектуру: router не должен знать про напоминания.
#   Теперь:
#     - intent_routes: REMINDER_ADD / REMINDER_WHEN / REMINDER_LIST /
#       REMINDER_DONE → NLU → skill_resolver → "reminders".
#     - brain/nlu/entity/reminders.py — datetime/text экстракторы.
#     - brain/nlu/decision/reminders.py — action add/when/list/done.
#     - brain/skills/reminders/ — handle(Command).
#   Router больше не знает про напоминания: ни _handle_reminders,
#   ни _is_explicit_reminder, ни _REMINDER_*_PHRASES.
#   Импорты datetime_parser и datetime из router удалены (были только
#   для _handle_reminders). re оставлен — используется в других местах?
#   Нет: re больше не используется. Удалён.

import threading
import time

from brain.skills import base as skills_base
from brain.skills._helpers import respond_and_say as _respond_and_say
from brain import pending
from brain import relationship
from brain.nlu import context as nlu_context_mod
from brain.nlu import pipeline as nlu_pipeline
from brain.nlu.types import DecisionAction
from shared import ipc
from shared import protocol
from shared import metrics
from shared.log import log
from shared.sources import CommandSource


PENDING_ASK = "music_ask"
PENDING_OFFER = "music_offer"

MOOD_TO_GENRE = {
    "calm": ("спокойное", "genre:classical"),
    "upbeat": ("бодрое", "genre:pop"),
    "rock": ("рок", "genre:rusrock"),
}


_repeat_count = 0
_repeat_lock = threading.Lock()
_last_command = None
_last_command_time = 0.0


NO_REPEAT_COMMANDS = {
    "дальше", "next", "следующий", "пропусти", "пропустить",
    "назад", "предыдущий", "предыдущая", "вернись", "верни",
    "пауза", "продолжить", "продолжи", "играть", "играй", "плей",
    "стоп", "останови", "остановись", "стоп музыка",
    "громче", "тише", "погромче", "потише",
    "что играет", "что за песня", "что за трек", "что сейчас",
    "спасибо", "благодарю",
}

YES_WORDS = {"да", "давай", "конечно", "ага", "угу", "хорошо", "ок", "окей"}
NO_WORDS = {"нет", "не", "не надо", "неважно", "не важно", "не хочу", "отмена"}


def process_command(command: str, source: CommandSource = CommandSource.VOICE):
    command = command.strip()
    if not command:
        return None

    cmd_lower = command.lower()
    metrics.inc("router_commands")
    log(f"router: cmd='{command}' | source={source.value}", tag="BRAIN")

    if cmd_lower in ("выход", "exit", "quit"):
        return "exit"

    # ── A2: router-страховка. YES/NO без pending → игнор ──
    if cmd_lower in YES_WORDS or cmd_lower in NO_WORDS:
        if pending.get(PENDING_ASK) is None and pending.get(PENDING_OFFER) is None:
            # FIX (этап 1.1): YES/NO может быть ответом на cleanup/cleanup_recycle
            # (brain.pending, ключи "cleanup"/"cleanup_recycle"). Раньше —
            # безусловный игнор, «да» на «Очистить безопасное?» терялось.
            if _handle_pending_confirm(cmd_lower):
                return None
            log(f"router: YES/NO без pending → игнор «{command}»", tag="BRAIN")
            return None

    # ── ДИАЛОГ: smalltalk → LLM ──
    if source == CommandSource.VOICE_DIALOG:
        handler = skills_base.find_handler(command)
        if handler and handler.name == "smalltalk":
            log(f"router: → smalltalk (dialog)", tag="BRAIN")
            try:
                handler.handle(command)
            except Exception as e:
                from brain.errors import handle_error
                handle_error(e, context="skill:smalltalk", speak=True)
            return None
        if _try_llm_fallback(command):
            return None
        _respond_and_say("NOT_UNDERSTOOD")
        return None

    # ── ВНЕ ДИАЛОГА: router решает первым ──
    if _handle_pending_music(cmd_lower):
        return None

    has_davai, rest = _extract_davai(cmd_lower)
    if has_davai:
        if not rest:
            _respond_and_say("MUSIC_WHAT")
            return None
        cmd_lower = rest
        command = rest

    if _check_repeat(command):
        log("router: REPEAT_ANNOYED", tag="BRAIN")
        _respond_and_say('REPEAT_ANNOYED')
        global _repeat_count
        with _repeat_lock:
            _repeat_count = 0
        return None

    # ── NLU PIPELINE ──
    try:
        nlu_ctx = nlu_context_mod.get()
        decision = nlu_pipeline.process(command, context=nlu_ctx)
        if decision is not None:
            handled = _execute_decision(decision, command, source)
            if handled:
                return None
    except Exception as e:
        log(f"router: NLU pipeline error: {e}", tag="BRAIN", level="WARN")

    log(f"router: не понял «{command}»", tag="BRAIN")
    metrics.inc("not_understood")

    if _try_llm_fallback(command):
        return None

    words = cmd_lower.split()
    if len(words) <= 1:
        _respond_and_say('NOT_UNDERSTOOD_SHORT')
    elif len(words) >= 6:
        _respond_and_say('NOT_UNDERSTOOD_LONG')
    else:
        _respond_and_say('NOT_UNDERSTOOD')
    return None


# ── NLU: исполнение Decision ─────────────────────────────────
def _execute_decision(decision, command: str, source: CommandSource) -> bool:
    """Исполнить Decision из pipeline. True — обработали, False — нет.

    EXECUTE  → music/controls/weather/system/reminders:
               skill.handle(decision.command)
               остальные: skill.handle(command)
    CLARIFY  → respond_and_say(clarify.phrase_set, **clarify.kwargs)
    FALLBACK → False (router пойдёт в LLM / NOT_UNDERSTOOD)
    """
    action = decision.action

    if action == DecisionAction.EXECUTE:
        skill_name = decision.command.skill_name
        if not skill_name:
            log("router: EXECUTE без skill_name", tag="BRAIN", level="WARN")
            return False
        skill = skills_base.find_by_name(skill_name)
        if skill is None:
            log(f"router: EXECUTE — скил {skill_name} не найден",
                tag="BRAIN", level="WARN")
            return False
        log(f"router: NLU → {decision.command.intent.value} → "
            f"{skill_name}.{decision.command.action or 'handle'} "
            f"(conf={decision.confidence:.2f})", tag="BRAIN")
        try:
            if skill.name in ("music", "controls", "weather", "system", "reminders"):
                skill.handle(decision.command)
            else:
                skill.handle(command)
        except Exception as e:
            from brain.errors import handle_error
            handle_error(e, context=f"skill:{skill_name}", speak=True)
        return True

    if action == DecisionAction.CLARIFY:
        clarify = decision.clarify
        if clarify is None:
            log("router: CLARIFY без clarify", tag="BRAIN", level="WARN")
            return False
        log(f"router: NLU → CLARIFY {clarify.phrase_set} "
            f"(reason={clarify.reason})", tag="BRAIN")
        try:
            _respond_and_say(clarify.phrase_set, **clarify.kwargs)
        except Exception as e:
            log(f"router: CLARIFY respond_and_say: {e}", tag="BRAIN", level="WARN")
            _respond_and_say("NOT_UNDERSTOOD")
        return True

    return False


# ── LLM fallback ─────────────────────────────────────────────
def _try_llm_fallback(command: str) -> bool:
    try:
        from brain.skills.llm import skill as llm_skill
        handler = llm_skill.LLMSkill()
        return handler.handle(command)
    except Exception as e:
        log(f"router: llm fallback: {e}", tag="BRAIN", level="WARN")
        return False


# ── pending (ask + offer) ─────────────────────────────────────
def _handle_pending_music(cmd_lower: str) -> bool:
    p = pending.get(PENDING_ASK)
    key = PENDING_ASK
    if p is None:
        p = pending.get(PENDING_OFFER)
        key = PENDING_OFFER
    if p is None:
        return False

    has_davai, rest = _extract_davai(cmd_lower)

    if (has_davai and not rest) or cmd_lower in YES_WORDS:
        pending.pop(key)
        if key == PENDING_OFFER:
            _respond_and_say("SMALLTALK_MUSIC_ACCEPT")
            ipc.put(protocol.MUSIC_INBOX, protocol.play_wave(language=None))
        else:
            _accept_pending(p.data)
        return True

    if has_davai and rest:
        pending.pop(key)
        if key == PENDING_OFFER:
            from brain.skills.music import skill as music_skill
            music_skill.MusicSkill().handle(rest)
        else:
            _accept_with_detail(p.data, rest)
        return True

    if cmd_lower in NO_WORDS:
        pending.pop(key)
        if key == PENDING_OFFER:
            _respond_and_say("SMALLTALK_MUSIC_DECLINE")
        else:
            _decline_pending(p.data)
        return True

    pending.pop(key)
    log(f"router: pending {key} сброшен (другая команда)", tag="BRAIN")
    return False


# ── FIX (этап 1.1): pending для скилов (cleanup, cleanup_recycle) ──
_CONFIRM_PENDING_KEYS = (
    "cleanup", "cleanup_recycle",
)


def _handle_pending_confirm(cmd_lower: str) -> bool:
    """YES/NO для pending от скилов (cleanup, cleanup_recycle).

    Скил сам делает pending.pop(key) в handle().
    """
    for key in _CONFIRM_PENDING_KEYS:
        if pending.get(key) is None:
            continue
        skill = skills_base.find_by_name(f"{key}_confirm")
        if skill is None:
            log(f"router: pending {key} — нет скила {key}_confirm",
                tag="BRAIN", level="WARN")
            continue
        try:
            skill.handle(cmd_lower)
        except Exception as e:
            from brain.errors import handle_error
            handle_error(e, context=f"skill:{key}_confirm", speak=True)
        return True
    return False


def _accept_pending(data: dict):
    kind = data.get("kind")
    if kind == "weather":
        label, genre = MOOD_TO_GENRE["calm"]
        _respond_and_say("MUSIC_ACCEPT", mood=label)
        relationship.set_weather_music(data.get("condition", ""), "calm")
        ipc.put(protocol.MUSIC_INBOX, protocol.play_genre(label, station=genre))
    elif kind == "recent":
        artist = data.get("artist", "")
        _respond_and_say("PLAY_ARTIST", artist=artist)
        relationship.mark_recent_asked(artist)
        ipc.put(protocol.MUSIC_INBOX, protocol.play_artist(artist))
    elif kind == "time":
        time_of_day = data.get("time_of_day", "")
        if time_of_day == "morning":
            label, genre = MOOD_TO_GENRE["upbeat"]
        else:
            label, genre = MOOD_TO_GENRE["calm"]
        _respond_and_say("MUSIC_ACCEPT", mood=label)
        relationship.mark_asked_today(time_of_day)
        ipc.put(protocol.MUSIC_INBOX, protocol.play_genre(label, station=genre))


def _accept_with_detail(data: dict, detail: str):
    kind = data.get("kind")
    from brain.skills.music import skill as music_skill
    music_skill.MusicSkill().handle(detail)
    if kind == "weather":
        relationship.set_weather_music(data.get("condition", ""), detail)
    elif kind == "recent":
        relationship.mark_recent_asked(data.get("artist", ""))
    elif kind == "time":
        relationship.mark_asked_today(data.get("time_of_day", ""))


def _decline_pending(data: dict):
    kind = data.get("kind")
    _respond_and_say("MUSIC_DECLINE")
    ipc.put(protocol.MUSIC_INBOX, protocol.play_wave(language=None))
    if kind == "weather":
        relationship.set_weather_music(data.get("condition", ""), None)
    elif kind == "recent":
        relationship.mark_recent_declined(data.get("artist", ""))
    elif kind == "time":
        relationship.mark_asked_today(data.get("time_of_day", ""))


# ── "давай" ───────────────────────────────────────────────────
def _extract_davai(text: str):
    """Отделить «давай» от остального. (да, «давай рок» → True, «рок»)."""
    t = text.strip()
    for prefix in ("давай ", "давай, ", "давай-ка ", "ну давай "):
        if t.startswith(prefix):
            return True, t[len(prefix):].strip()
    if t in ("давай", "давай,", "ну давай"):
        return True, ""
    return False, text


# ── повтор ───────────────────────────────────────────────────
def _check_repeat(command: str) -> bool:
    """Пользователь повторяет ту же команду 3+ раз подряд?"""
    global _repeat_count, _last_command, _last_command_time

    cmd = command.lower().strip()
    if cmd in NO_REPEAT_COMMANDS:
        return False

    now = time.time()
    with _repeat_lock:
        if cmd == _last_command and (now - _last_command_time) < 30.0:
            _repeat_count += 1
        else:
            _repeat_count = 1
            _last_command = cmd
        _last_command_time = now
        return _repeat_count >= 3