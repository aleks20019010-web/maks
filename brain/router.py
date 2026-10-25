# brain/router.py
# Роутер команд: выбирает навык по тексту и вызывает его.
#
# F1: напоминания — «напомни/напомнить/запомни/запомнить X», «когда X?»,
#     «я ничего не забыл?», «что я забыл?», «готово».
# pending_music_ask — ответ на вопрос music (погода/вчерашнее/время).

import re
import threading
import time
from datetime import datetime

from brain.skills import base as skills_base
from brain.skills._helpers import respond_and_say as _respond_and_say
from brain import pending
from brain import relationship
from brain import datetime_parser
from shared import ipc
from shared import protocol
from shared import metrics
from shared.log import log


PENDING_ASK = "music_ask"
PENDING_OFFER = "music_offer"

MOOD_TO_GENRE = {
    "calm": ("спокойное", "genre:classical"),
    "upbeat": ("бодрое", "genre:pop"),
    "rock": ("рок", "genre:rusrock"),
}

_REMINDER_TRIGGERS = ("напомни", "напомнить", "запомни", "запомнить")

_REMINDER_LIST_PHRASES = (
    "ничего не забыл", "что я забыл", "что забыл",
    "что у меня запланировано", "что у меня в планах",
    "что на сегодня", "что на завтра",
    "мои напоминания", "список напоминаний",
)


# ── Анти-повтор ───────────────────────────────────────────────
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


def process_command(command: str):
    command = command.strip()
    if not command:
        return None

    cmd_lower = command.lower()
    metrics.inc("router_commands")
    log(f"router: cmd='{command}' | NO_REPEAT={cmd_lower in NO_REPEAT_COMMANDS}", tag="BRAIN")

    if cmd_lower in ("выход", "exit", "quit"):
        return "exit"

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

    handler = skills_base.find_handler(command)
    if handler:
        log(f"router: → {handler.name}", tag="BRAIN")
        try:
            handler.handle(command)
        except Exception as e:
            from brain.errors import handle_error
            handle_error(e, context=f"skill:{handler.name}", speak=True)
        return None

    log(f"router: не понял «{command}»", tag="BRAIN")
    metrics.inc("not_understood")
    words = cmd_lower.split()
    if len(words) <= 1:
        _respond_and_say('NOT_UNDERSTOOD_SHORT')
    elif len(words) >= 6:
        _respond_and_say('NOT_UNDERSTOOD_LONG')
    else:
        _respond_and_say('NOT_UNDERSTOOD')
    return None


# ── F1: напоминания ──────────────────────────────────────────
def _fmt_due(due: datetime) -> str:
    month_ru = {1: "января", 2: "февраля", 3: "марта", 4: "апреля",
                5: "мая", 6: "июня", 7: "июля", 8: "августа",
                9: "сентября", 10: "октября", 11: "ноября",
                12: "декабря"}.get(due.month, "")
    return f"{due.day} {month_ru} в {due.strftime('%H:%M')}"


def _strip_reminder_trigger(cmd_lower: str):
    """Если команда начинается с триггера — вернуть rest, иначе None."""
    for trigger in _REMINDER_TRIGGERS:
        prefix = trigger + " "
        if cmd_lower.startswith(prefix):
            return cmd_lower[len(prefix):].strip()
    return None


def _handle_reminders(cmd_lower: str) -> bool:
    from brain import reminders

    # ── «я ничего не забыл?» / «что я забыл?» / «что у меня запланировано?» ──
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
                lines.append(f"{text} — {_fmt_due(dt)}")
            else:
                lines.append(text)

        _respond_and_say("PENDING_LIST", items="; ".join(lines), count=len(items))
        return True

    # ── «когда X?» / «что с X?» ─────────────────────────────
    m = re.match(r'^(?:когда|что\s+с)\s+(.+?)\??$', cmd_lower)
    if m:
        text = m.group(1).strip()
        upcoming = reminders.get_upcoming(text)
        if upcoming:
            p = upcoming[0]
            try:
                dt = datetime.fromisoformat(p["due_at"])
                _respond_and_say("REMINDER_WHEN", text=p["text"], when=_fmt_due(dt))
            except Exception:
                _respond_and_say("NOT_UNDERSTOOD_SHORT")
        else:
            _respond_and_say("REMINDER_NOT_FOUND", text=text)
        return True

    # ── «напомни/напомнить/запомни/запомнить ...» ──────────
    rest = _strip_reminder_trigger(cmd_lower)
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
                _respond_and_say("REMINDER_ADDED_AT", text=text, when=_fmt_due(due))
                return True

        r = reminders.add(rest, 5)
        if r:
            _respond_and_say("REMINDER_ADDED", text=rest, minutes=5)
            return True
        _respond_and_say("NOT_UNDERSTOOD_SHORT")
        return True

    # ── «готово» / «сделал» ────────────────────────────────
    if cmd_lower in ("готово", "сделано", "я сделал", "я сделала",
                     "я выполнил", "я выполнила"):
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
    t = text.strip()
    for prefix in ("давай-ка ", "давай же ", "давай "):
        if t.startswith(prefix):
            return True, t[len(prefix):].strip()
    if t == "давай" or t == "давай-ка":
        return True, ""
    return False, t


# ── Анти-повтор ───────────────────────────────────────────────
def _check_repeat(command: str) -> bool:
    global _repeat_count, _last_command, _last_command_time
    cmd_lower = command.lower().strip()
    if cmd_lower in NO_REPEAT_COMMANDS:
        with _repeat_lock:
            _repeat_count = 0
            _last_command = cmd_lower
            _last_command_time = time.time()
        return False
    with _repeat_lock:
        now = time.time()
        if command == _last_command and now - _last_command_time < 10:
            _repeat_count += 1
        else:
            _repeat_count = 0
        _last_command = command
        _last_command_time = now
        return _repeat_count >= 2