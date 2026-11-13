# brain/nlu/entity/reminders.py
# EntityExtractor для напоминаний.
#
# Два экстрактора:
#   DateTimeExtractor     — для REMINDER_ADD (delay | datetime)
#                           и REMINDER_LIST (date без времени).
#   ReminderTextExtractor — для REMINDER_WHEN / REMINDER_DONE (text).
#
# Контракт как у MusicEntityExtractor:
#   supported_intents, supports(intent),
#   extract_candidate(text, intent) -> str | None
#   match(candidate, intent) -> Entity
#
# Парсинг — brain/datetime_parser.py (parse_delay, parse_when,
# parse_date_only). Экстрактор только оборачивает результат в Entity.
#
# Entity.value — СТРОКА (types.py: Entity.value: Optional[str]).
# Для ADD кладём json: {"minutes": 10, "text": "позвонить"} / {"due": "...", "text": "..."}.
# Скил делает json.loads — так clean_text от парсера доходит до скила,
# а не теряется.
#
# FIX (2026-11-13):
#   - parse_delay теперь без обязательного хвоста («через час» → (60, "")).
#   - json-в-entity для ADD: clean_text доходит до скила.
#   - fallback «в восемь часов» для pending-ответа без «в».
#   - parse_date_only для REMINDER_LIST («что на завтра»).

import json
from datetime import datetime

from brain import datetime_parser
from brain.nlu.types import Entity, EntityState, Intent
from shared.log import log


class DateTimeExtractor:
    """Дата/время для REMINDER_ADD и REMINDER_LIST.

    REMINDER_ADD:
      «через 10 минут позвонить» → entity(type="delay",
          value='{"minutes": 10, "text": "позвонить"}')
      «29 октября в 10:00 УЗИ»   → entity(type="datetime",
          value='{"due": "2026-10-29T10:00:00", "text": "УЗИ"}')
    REMINDER_LIST:
      «что на завтра» → entity(type="date", value="2026-11-14T00:00:00")
    """

    supported_intents = (Intent.REMINDER_ADD, Intent.REMINDER_LIST)

    def supports(self, intent: Intent) -> bool:
        return intent in self.supported_intents

    def extract_candidate(self, text: str, intent: Intent) -> str | None:
        """Вернуть очищенный текст (без триггера/даты) как кандидат.

        Для REMINDER_LIST — только дата-часть («завтра» / «29 октября»).
        Для REMINDER_ADD — весь остаток после триггера.
        """
        if not text:
            return None
        t = text.strip()

        if intent == Intent.REMINDER_LIST:
            # «что на завтра» → «завтра»; «мои напоминания» → «мои напоминания»
            for prefix in (
                "что на ", "что у меня на ",
                "напоминания на ", "покажи на ",
            ):
                if t.lower().startswith(prefix):
                    return t[len(prefix):].strip()
            return t

        # REMINDER_ADD: снять «напомни/запомни» и т.п.
        from brain import reminders
        stripped = reminders.strip_reminder_trigger(t)
        return stripped if stripped is not None else t

    def match(self, candidate: str, intent: Intent) -> Entity:
        """Разобрать candidate в Entity.

        REMINDER_ADD:
          parse_delay → delay; parse_when → datetime; иначе ABSENT.
        REMINDER_LIST:
          parse_date_only → date; иначе ABSENT.
        """
        if not candidate:
            return Entity.absent()

        try:
            if intent == Intent.REMINDER_ADD:
                parsed = datetime_parser.parse_delay(candidate)
                if parsed:
                    minutes, clean = parsed
                    return Entity.found(
                        "delay",
                        json.dumps(
                            {"minutes": int(minutes), "text": clean},
                            ensure_ascii=False,
                        ),
                        1.0, "parse_delay",
                    )

                parsed = datetime_parser.parse_when(candidate)
                if not parsed:
                    # pending-ответ «восемь часов» без «в»
                    parsed = datetime_parser.parse_when("в " + candidate)
                if parsed:
                    due, clean = parsed
                    return Entity.found(
                        "datetime",
                        json.dumps(
                            {
                                "due": due.isoformat(timespec="seconds"),
                                "text": clean,
                            },
                            ensure_ascii=False,
                        ),
                        1.0, "parse_when",
                    )
                return Entity.absent()

            if intent == Intent.REMINDER_LIST:
                d = datetime_parser.parse_date_only(candidate)
                if d is None:
                    return Entity.absent()
                # date → ISO datetime (00:00), чтобы скил парсил одним fromisoformat
                due = datetime(d.year, d.month, d.day, 0, 0, 0)
                return Entity.found(
                    "date", due.isoformat(timespec="seconds"),
                    1.0, "parse_date_only",
                )
        except Exception as e:
            log(f"entity.reminders.DateTimeExtractor.match: {e}",
                tag="BRAIN", level="WARN")

        return Entity.absent()


class ReminderTextExtractor:
    """Текст для REMINDER_WHEN / REMINDER_DONE.

    «когда к врачу»         → entity(type="text", value="к врачу")
    «готово позвонить маме» → entity(type="text", value="позвонить маме")
    «готово»                → entity(type="text", value="готово")  — скил → clarify
    """

    supported_intents = (Intent.REMINDER_WHEN, Intent.REMINDER_DONE)

    def supports(self, intent: Intent) -> bool:
        return intent in self.supported_intents

    def extract_candidate(self, text: str, intent: Intent) -> str | None:
        if not text:
            return None
        t = text.strip()
        if intent == Intent.REMINDER_WHEN:
            # «когда к врачу» → «к врачу»
            for prefix in ("когда ", "что с ", "что ", "во сколько "):
                if t.lower().startswith(prefix):
                    t = t[len(prefix):].strip()
                    break
        elif intent == Intent.REMINDER_DONE:
            # «готово позвонить маме» → «позвонить маме»
            # «я сделал позвонить» → «позвонить»
            low = t.lower()
            for prefix in (
                "готово ", "сделано ", "выполнено ",
                "я сделал ", "я сделала ", "я выполнил ", "я выполнила ",
                "я позвонил ", "я позвонила ",
            ):
                if low.startswith(prefix):
                    t = t[len(prefix):].strip()
                    break
        return t or None

    def match(self, candidate: str, intent: Intent) -> Entity:
        if not candidate:
            return Entity.absent()
        return Entity.found("text", candidate, 1.0, "text")


# ── Синглтоны ───────────────────────────────────────────────
_dt_extractor: DateTimeExtractor | None = None
_text_extractor: ReminderTextExtractor | None = None


def get() -> DateTimeExtractor:
    global _dt_extractor
    if _dt_extractor is None:
        _dt_extractor = DateTimeExtractor()
    return _dt_extractor


def get_text() -> ReminderTextExtractor:
    global _text_extractor
    if _text_extractor is None:
        _text_extractor = ReminderTextExtractor()
    return _text_extractor