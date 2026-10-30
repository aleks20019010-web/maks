# brain/router.py
# Роутер команд: выбирает навык по тексту и вызывает его.
#
# ВНЕ ДИАЛОГА (CommandSource.VOICE, VOICE_HOT, SYSTEM):
#   reminders → pending → fast/skills → NLU pipeline → LLM → NOT_UNDERSTOOD
#   Router решает первым.
#
# В ДИАЛОГЕ (CommandSource.VOICE_DIALOG):
#   smalltalk → LLM → (LLM вернёт command → process_command → router)
#   Router обрабатывает только smalltalk. Всё остальное — LLM.
#
# FIX (NLU v2): NLU-блок заменён на brain/nlu/pipeline.process().
#   Раньше router звал nlu_intent.resolve() и _INTENT_TO_SKILL.get() →
#   skills_base.find_by_name(). Это была «половинчатая» интеграция:
#   intent был, entity/decision — нет. Теперь:
#     decision = pipeline.process(command, context=nlu_context)
#   router исполняет decision.action:
#     EXECUTE  → skills_base.find_by_name(command.skill_name).handle(...)
#     CLARIFY  → respond_and_say(decision.clarify.phrase_set, **kwargs)
#     FALLBACK → LLM (как раньше) → NOT_UNDERSTOOD
#
#   _INTENT_TO_SKILL переехал в brain/nlu/skill_resolver.py (этап 4).
#   router больше не знает соответствие intent → skill_name.
#
# FIX (этап 9, часть 1): music получает Command, остальные — текст.
#   Раньше: skill.handle(command) — ВСЕГДА текст. MusicSkill.handle ждёт
#   Command (isinstance(Command) → диспатч по action). Получал текст →
#   isinstance False → classify(text) → None (для «вруби что-нибудь
#   послушать» classify вычитает «вруби»/«послушать» → пусто → None) →
#   музыка НЕ играла. Теперь:
#     if skill.name == "music":
#         skill.handle(decision.command)   # Command
#     else:
#         skill.handle(command)            # текст (controls/weather/...)
#   Переходный период: controls/weather/cleanup/smalltalk — пока на str.
#   Когда все скилы будут на Command — убрать if и оставить skill.handle(Command).
#
# FIX: CommandSource enum вместо magic strings "voice"/"voice_dialog".
# FIX (день 28, 5.15): _fmt_due/_strip_reminder_trigger/_REMINDER_TRIGGERS
# переехали в brain/reminders.py.

import re
import threading
import time
from datetime import datetime

from brain.skills import base as skills_base
from brain.skills._helpers import respond_and_say as _respond_and_say
from brain import pending
from brain import relationship
from brain import datetime_parser
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

_REMINDER_LIST_PHRASES = (
    "ничего не забыл", "что я забыл", "что забыл",
    "что у меня запланировано", "что у меня в планах",
    "что на сегодня", "что на завтра",
    "мои напоминания", "список напоминаний",
)

_REMINDER_DONE_PHRASES = (
    "готово", "сделано", "я сделал", "я сделала",
    "я выполнил", "я выполнила",
)


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
            log(f"router: YES/NO без pending → игнор «{command}»", tag="BRAIN")
            return None

    # ── ДИАЛОГ: smalltalk → LLM ──
    if source == CommandSource.VOICE_DIALOG:
        if _is_explicit_reminder(cmd_lower):
            if _handle_reminders(cmd_lower):
                return None
        else:
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
            if _handle_reminders(cmd_lower):
                return None
        _respond_and_say("NOT_UNDERSTOOD")
        return None

    # ── ВНЕ ДИАЛОГА: router решает первым ──
    if _handle_reminders(cmd_lower):
        return None

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

    # ── FAST: find_handler (skills) ──
    handler = skills_base.find_handler(command)
    if handler:
        log(f"router: → {handler.name} (fast)", tag="BRAIN")
        try:
            handler.handle(command)
        except Exception as e:
            from brain.errors import handle_error
            handle_error(e, context=f"skill:{handler.name}", speak=True)
        return None

    # ── NLU PIPELINE ──
    # find_handler вернул None. Зовём pipeline.process.
    # pipeline: normalizer → context? → intent → skill_resolver →
    #           entity → decision → Decision
    # router исполняет decision.action.
    #
    # FIX (NLU v2): раньше здесь был «половинчатый» NLU: intent.resolve +
    # _INTENT_TO_SKILL + find_by_name. Теперь — pipeline.process, который
    # делает всё: intent, entity, decision. router не знает про _INTENT_TO_SKILL.
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

    EXECUTE  → music: skill.handle(decision.command)  (Command)
               остальные: skill.handle(command)       (текст, переходный период)
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
            # ЭТАП 9, часть 1: music получает Command, остальные — текст.
            # MusicSkill.handle ждёт Command (isinstance → диспатч по action).
            # controls/weather/cleanup/smalltalk — пока на str (переходный период).
            if skill.name == "music":
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

    # FALLBACK — router сам решит (LLM / NOT_UNDERSTOOD).
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


# ── Явное напоминание? ───────────────────────────────────────
def _is_explicit_reminder(cmd_lower: str) -> bool:
    from brain import reminders
    if any(cmd_lower.startswith(t + " ") or cmd_lower == t for t in reminders.REMINDER_TRIGGERS):
        return True
    if any(phrase in cmd_lower for phrase in _REMINDER_LIST_PHRASES):
        return True
    if cmd_lower in _REMINDER_DONE_PHRASES:
        return True
    return False


# ── F1: напоминания ──────────────────────────────────────────
def _handle_reminders(cmd_lower: str) -> bool:
    from brain import reminders

    if any(phrase in cmd_lower for phrase in _REMINDER_LIST_PHRASES):
        pending_list = reminders.get_pending()
        if not pending_list:
            _respond_and_say("NOTHING_PENDING")
            return True

        items = []
        for p in pending_list:
            try:
                dt = datetime.fromisoformat(p["due_at"])
                items.append((dt, p["text"]))
            except Exception:
                items.append((None, p["text"]))
        items.sort(key=lambda x: (x[0] is None, x[0]))

        lines = []
        for dt, text in items:
            if dt:
                lines.append(f"{text} — {reminders.fmt_due(dt)}")
            else:
                lines.append(text)

        _respond_and_say("PENDING_LIST", items="; ".join(lines), count=len(items))
        return True

    m = re.match(
        r'^когда\s+(?:я\s+)?(?:должен|должна|надо|нужно|планирую|собираюсь|мне\s+надо|мне\s+нужно)\s+(.+?)\??$',
        cmd_lower,
    )
    if m:
        text = m.group(1).strip()
        upcoming = reminders.get_upcoming(text)
        if upcoming:
            p = upcoming[0]
            try:
                dt = datetime.fromisoformat(p["due_at"])
                _respond_and_say("REMINDER_WHEN", text=p["text"], when=reminders.fmt_due(dt))
            except Exception:
                _respond_and_say("NOT_UNDERSTOOD_SHORT")
        else:
            _respond_and_say("REMINDER_NOT_FOUND", text=text)
        return True

    m2 = re.match(r'^что\s+с\s+(.+?)\??$', cmd_lower)
    if m2:
        text = m2.group(1).strip()
        upcoming = reminders.get_upcoming(text)
        if upcoming:
            p = upcoming[0]
            try:
                dt = datetime.fromisoformat(p["due_at"])
                _respond_and_say("REMINDER_WHEN", text=p["text"], when=reminders.fmt_due(dt))
            except Exception:
                _respond_and_say("NOT_UNDERSTOOD_SHORT")
        else:
            _respond_and_say("REMINDER_NOT_FOUND", text=text)
        return True

    rest = reminders.strip_reminder_trigger(cmd_lower)
    if rest is not None:
        if not rest:
            _respond_and_say("NOT_UNDERSTOOD_SHORT")
            return True

        parsed = datetime_parser.parse_delay(rest)
        if parsed:
            delay, text = parsed
            r = reminders.add(text, delay)
            if r:
                _respond_and_say("REMINDER_ADDED", text=text, minutes=delay)
                return True

        parsed = datetime_parser.parse_when(rest)
        if parsed:
            due, text = parsed
            r = reminders.add_at(text, due)
            if r:
                _respond_and_say("REMINDER_ADDED_AT", text=text, when=reminders.fmt_due(due))
                return True

        r = reminders.add(rest, 5)
        if r:
            _respond_and_say("REMINDER_ADDED", text=rest, minutes=5)
            return True
        _respond_and_say("NOT_UNDERSTOOD_SHORT")
        return True

    if cmd_lower in _REMINDER_DONE_PHRASES:
        pending_list = reminders.get_pending()
        if pending_list:
            p = pending_list[0]
            reminders.mark_done(p["id"])
            _respond_and_say("REMINDER_DONE", text=p["text"])
        else:
            _respond_and_say("NOT_UNDERSTOOD_SHORT")
        return True

    if cmd_lower.startswith(("я позвонил ", "я позвонила ",
                             "я сделал ", "я сделала ",
                             "я выполнил ", "я выполнила ")):
        parts = cmd_lower.split(" ", 2)
        if len(parts) >= 3:
            rest = parts[2].strip()
            if reminders.mark_done_by_text(rest):
                _respond_and_say("REMINDER_DONE", text=rest)
                return True
        return False

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