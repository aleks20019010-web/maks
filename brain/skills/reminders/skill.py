# brain/skills/reminders/skill.py
# RemindersSkill: напоминания через NLU.
#
# До этого скила напоминания жили в brain/router.py (_handle_reminders) и
# вызывались ДО NLU. Это была дыра в миграции: NLU не проверялся для
# REMINDER_*, модель не знала эти интенты, а «что я забыл» работало только
# потому, что router ловил его раньше.
#
# Теперь:
#   router → pipeline.process() → Decision(EXECUTE, command=Command)
#   → _execute_decision → skill.handle(command)
#
# Скил получает Command, НЕ парсит original_text. Entity уже извлечён
# DateTimeExtractor / ReminderTextExtractor в pipeline.
#
# Интенты:
#   REMINDER_ADD    entity: delay (json {"minutes","text"})
#                          | datetime (json {"due","text"})
#                          | ABSENT (clarify)
#   REMINDER_WHEN   entity: text (что искать)
#   REMINDER_LIST   entity: date (ISO) | ABSENT (весь список)
#   REMINDER_DONE   entity: text (что закрыть) | ABSENT (clarify)
#
# Хранилище — brain/reminders.py (add, add_at, get_pending, ...).
#
# FIX (2026-11-13): entity.value для ADD — json-строка
#   ({"minutes": 10, "text": "позвонить"}), а не "10".
#   Так clean_text от парсера доходит до скила.
#
# FIX (2026-11-13): fallback_text берётся из command.context["pending_original"],
#   если он есть (pending-сценарий: «напомни хлеб» → «через десять минут»).
#   Иначе — из command.original_text (прямой сценарий).
#
# FIX (2026-11-13, #2): дубликат напоминания.
#   reminders.add / add_at возвращают dict с флагом "_duplicate": True,
#   если такое напоминание (text + due_at ±1 мин) уже есть.
#   _handle_add: если "_duplicate" → REMINDER_ALREADY (не REMINDER_ADDED).
#   См. brain/reminders.py — _find_duplicate().

import json
from datetime import datetime

from brain import reminders
from brain.skills import base as skills_base
from brain.skills._helpers import respond_and_say
from brain.nlu.types import Command, Intent, EntityState
from shared.log import log


class RemindersSkill(skills_base.Skill):
    """Скил напоминаний. NLU-путь, can_handle → False."""

    name = "reminders"
    priority = 40  # до smalltalk/llm, но после music/controls

    def can_handle(self, text: str) -> bool:
        # NLU-путь: router зовёт по skill_name из Decision, не по can_handle.
        return False

    def handle(self, command) -> None:
        """Диспатч по command.intent. command — Command (не str)."""
        if not isinstance(command, Command):
            log(f"reminders: handle получил {type(command).__name__}, "
                f"ожидался Command", tag="BRAIN", level="WARN")
            return

        intent = command.intent

        if intent == Intent.REMINDER_ADD:
            self._handle_add(command)
        elif intent == Intent.REMINDER_WHEN:
            self._handle_when(command)
        elif intent == Intent.REMINDER_LIST:
            self._handle_list(command)
        elif intent == Intent.REMINDER_DONE:
            self._handle_done(command)
        else:
            log(f"reminders: неизвестный intent {intent}", tag="BRAIN", level="WARN")

    # ── REMINDER_ADD ────────────────────────────────────────
    def _handle_add(self, command: Command) -> None:
        """Напомни X [через N минут | 29 октября в 10:00].

        Entity:
          delay    → json {"minutes", "text"} → add(text, minutes)
          datetime → json {"due", "text"}     → add_at(text, due)
          ABSENT   → clarify (REMINDER_ADD_CLARIFY)

        FIX (2026-11-13, #2): если reminders.add / add_at вернул
        "_duplicate": True — говорим REMINDER_ALREADY, а не REMINDER_ADDED.
        """
        entity = command.entity
        # FIX (2026-11-13): pending-сценарий — исходный текст в context.
        fallback_text = self._strip_trigger(
            command.context.get("pending_original") or command.original_text
        )

        if entity is None or entity.state == EntityState.ABSENT:
            respond_and_say("REMINDER_ADD_CLARIFY")
            return

        if entity.state != EntityState.FOUND:
            respond_and_say("NOT_UNDERSTOOD_SHORT")
            return

        if entity.type == "delay":
            try:
                payload = json.loads(entity.value)
                minutes = float(payload["minutes"])
                text = (payload.get("text") or "").strip() or fallback_text
            except (TypeError, ValueError, KeyError):
                respond_and_say("NOT_UNDERSTOOD_SHORT")
                return
            if not text:
                respond_and_say("REMINDER_ADD_CLARIFY")
                return
            r = reminders.add(text, minutes)
            if r:
                if r.get("_duplicate"):
                    respond_and_say("REMINDER_ALREADY",
                                    text=text,
                                    when=f"через {int(minutes)} минут")
                else:
                    respond_and_say("REMINDER_ADDED", text=text, minutes=int(minutes))
            else:
                respond_and_say("NOT_UNDERSTOOD_SHORT")
            return

        if entity.type == "datetime":
            try:
                payload = json.loads(entity.value)
                due = datetime.fromisoformat(payload["due"])
                text = (payload.get("text") or "").strip() or fallback_text
            except (TypeError, ValueError, KeyError):
                respond_and_say("NOT_UNDERSTOOD_SHORT")
                return
            if not text:
                respond_and_say("REMINDER_ADD_CLARIFY")
                return
            r = reminders.add_at(text, due)
            if r:
                if r.get("_duplicate"):
                    respond_and_say("REMINDER_ALREADY",
                                    text=text,
                                    when=reminders.fmt_due(due))
                else:
                    respond_and_say("REMINDER_ADDED_AT", text=text,
                                    when=reminders.fmt_due(due))
            else:
                respond_and_say("NOT_UNDERSTOOD_SHORT")
            return

        respond_and_say("NOT_UNDERSTOOD_SHORT")

    # ── REMINDER_WHEN ───────────────────────────────────────
    def _handle_when(self, command: Command) -> None:
        """«когда к врачу» → найти по тексту, сказать когда."""
        entity = command.entity
        if entity is None or entity.state != EntityState.FOUND or not entity.value:
            respond_and_say("NOT_UNDERSTOOD_SHORT")
            return

        text = entity.value.strip()
        upcoming = reminders.get_upcoming(text)
        if not upcoming:
            respond_and_say("REMINDER_NOT_FOUND", text=text)
            return

        p = upcoming[0]
        try:
            dt = datetime.fromisoformat(p["due_at"])
        except (KeyError, ValueError):
            respond_and_say("NOT_UNDERSTOOD_SHORT")
            return

        respond_and_say("REMINDER_WHEN", text=p["text"], when=reminders.fmt_due(dt))

    # ── REMINDER_LIST ───────────────────────────────────────
    def _handle_list(self, command: Command) -> None:
        """«мои напоминания» / «что на завтра».

        Entity: date (ISO) → фильтр по дате; ABSENT → весь список.
        """
        pending_list = reminders.get_pending()
        if not pending_list:
            respond_and_say("NOTHING_PENDING")
            return

        entity = command.entity
        filter_date = None
        if entity is not None and entity.state == EntityState.FOUND and entity.type == "date":
            try:
                filter_date = datetime.fromisoformat(entity.value).date()
            except (TypeError, ValueError):
                filter_date = None

        items = []
        for p in pending_list:
            try:
                dt = datetime.fromisoformat(p["due_at"])
            except (KeyError, ValueError):
                continue
            if filter_date is not None and dt.date() != filter_date:
                continue
            items.append((dt, p["text"]))

        if not items:
            if filter_date is not None:
                respond_and_say("REMINDER_LIST_EMPTY_DAY",
                                day=self._fmt_day(filter_date))
            else:
                respond_and_say("NOTHING_PENDING")
            return

        items.sort(key=lambda x: x[0])
        lines = [f"{text} — {reminders.fmt_due(dt)}" for dt, text in items]
        respond_and_say("PENDING_LIST", items="; ".join(lines), count=len(items))

    # ── REMINDER_DONE ───────────────────────────────────────
    def _handle_done(self, command: Command) -> None:
        """«готово позвонить маме» / «готово» (clarify)."""
        entity = command.entity

        if entity is None or entity.state == EntityState.ABSENT:
            respond_and_say("REMINDER_DONE_CLARIFY")
            return

        if entity.state != EntityState.FOUND or not entity.value:
            respond_and_say("REMINDER_DONE_CLARIFY")
            return

        text = entity.value.strip()
        if text.lower() in ("готово", "сделано", "выполнено"):
            respond_and_say("REMINDER_DONE_CLARIFY")
            return

        if reminders.mark_done_by_text(text):
            respond_and_say("REMINDER_DONE", text=text)
        else:
            respond_and_say("REMINDER_NOT_FOUND", text=text)

    # ── helpers ─────────────────────────────────────────────
    @staticmethod
    def _strip_trigger(text: str) -> str:
        """Убрать «напомни/запомни/не забудь» из начала. Вернуть остаток."""
        if not text:
            return ""
        t = text.lower().strip()
        for trigger in reminders.REMINDER_TRIGGERS:
            if t.startswith(trigger + " "):
                return text[len(trigger) + 1:].strip()
            if t == trigger:
                return ""
        for trigger in ("не забудь ", "не забудь"):
            if t.startswith(trigger):
                return text[len(trigger):].strip()
        return text.strip()

    @staticmethod
    def _fmt_day(d) -> str:
        """date → «14 ноября». Для REMINDER_LIST_EMPTY_DAY."""
        months = {
            1: "января", 2: "февраля", 3: "марта", 4: "апреля",
            5: "мая", 6: "июня", 7: "июля", 8: "августа",
            9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
        }
        return f"{d.day} {months.get(d.month, '')}"


# ── Регистрация ─────────────────────────────────────────────
skills_base.register(RemindersSkill())