# brain/reminders.py
# Напоминания с временем: «напомни через 10 минут X», «запомни 29 октября в 10:00 УЗИ».
#
# Хранится в data/reminders.json:
# [
#   {"id": 1, "text": "УЗИ", "due_at": "2026-10-29T10:00:00", "kind": "date", "done": false}
# ]
#
# kind:
#   "delay" — «через N минут» — один слот ровно в due_at.
#   "date"  — конкретная дата/время — несколько слотов:
#             - вечером накануне: 22:00 и 23:00 (если событие завтра или позже);
#             - за час до события (due - 1h), если ещё не прошёл;
#             - за 5 минут до события (due - 5m), если «за час» уже прошёл,
#               но до события ещё есть время.
#
# FIX (2026-11-13): get_reminder_times — новая логика слотов.
#   Раньше: до полудня → 21:00/22:00 накануне; после полудня → 10:00/11:00
#   того же дня. Не совпадало с ожиданием: «вечером 22–23, и за час до».
#   Теперь: 22:00/23:00 накануне + за час до + за 5 минут (fallback).
#
# FIX (2026-11-13): get_due_slots — проверяем СЛОТ, а не due.
#   Раньше: if now - due > MAX_SLOT_AGE_MINUTES — удаляли. Для kind="date"
#   слоты (22:00 накануне) могли быть в прошлом, а due — в будущем.
#   now - due < 0 → не удаляли. А slot <= now → озвучивали «вчерашний»
#   слот сразу после добавления (было: «напомнил через 4 секунды»).
#   Теперь: если слот просрочен > MAX_SLOT_AGE_MINUTES — скип. Если все
#   слоты просрочены и due в прошлом — удалить напоминание.
#
# FIX (2026-11-13, #2): дедуп в _add_entry.
#   Если уже есть невыполненное напоминание с тем же text и тем же due_at
#   (до минуты) — не добавляем второе, возвращаем существующее с флагом
#   "_duplicate": True. Скил по флагу скажет REMINDER_ALREADY,
#   а не REMINDER_ADDED / REMINDER_ADDED_AT.

import json
import os
import threading
from datetime import datetime, timedelta

from shared import config as cfg
from shared.log import log


REMINDERS_FILE = os.path.join(cfg.DATA_DIR, 'reminders.json')

_cache = None
_reminded_slots = set()  # (reminder_id, slot_index)
_worker = None
_stop = threading.Event()
_lock = threading.Lock()

CHECK_INTERVAL_SEC = 30
MAX_SLOT_AGE_MINUTES = 5  # если слот прошёл > 5 мин — не озвучивать, удалить


# ── Триггеры (используются в reminder-скиле: _strip_trigger) ──
REMINDER_TRIGGERS = ("напомни", "напомнить", "запомни", "запомнить")


# ────────────────────────────────────────────────────────────
# ФОРМАТИРОВАНИЕ И ПАРСИНГ
# ────────────────────────────────────────────────────────────
_MONTHS_RU = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля",
    5: "мая", 6: "июня", 7: "июля", 8: "августа",
    9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
}


def fmt_due(due: datetime) -> str:
    """«29 октября в 10:00». Для фраз REMINDER_WHEN, REMINDER_ADDED_AT."""
    month_ru = _MONTHS_RU.get(due.month, "")
    return f"{due.day} {month_ru} в {due.strftime('%H:%M')}"


def strip_reminder_trigger(cmd_lower: str) -> str | None:
    """Убрать триггер «напомни/запомни» из начала. None — если триггера нет."""
    for trigger in REMINDER_TRIGGERS:
        prefix = trigger + " "
        if cmd_lower.startswith(prefix):
            return cmd_lower[len(prefix):].strip()
    return None


def _load() -> list:
    global _cache
    if _cache is not None:
        return _cache
    if not os.path.exists(REMINDERS_FILE):
        _cache = []
        return _cache
    try:
        with open(REMINDERS_FILE, 'r', encoding='utf-8') as f:
            _cache = json.load(f)
    except Exception:
        _cache = []
    return _cache


def _save():
    if _cache is None:
        return
    try:
        os.makedirs(os.path.dirname(REMINDERS_FILE), exist_ok=True)
        with open(REMINDERS_FILE, 'w', encoding='utf-8') as f:
            json.dump(_cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log(f"reminders: ошибка сохранения: {e}", tag="BRAIN", level="WARN")


def _now() -> datetime:
    return datetime.now()


# ────────────────────────────────────────────────────────────
# СЛОТЫ НАПОМИНАНИЙ
# ────────────────────────────────────────────────────────────
def get_reminder_times(due_at: datetime, kind: str = "date") -> list:
    """Вернуть список datetime — когда напоминать.

    kind == "delay" → один слот ровно в due_at.

    kind == "date":
      1. Вечером накануне: 22:00 и 23:00 (если событие завтра или позже).
      2. За час до события (due - 1h), если ещё не прошёл.
      3. За 5 минут до события (due - 5m), если «за час» уже прошёл,
         но до события ещё есть время.

    Примеры (now = 13 ноя 10:00):
      14 ноя 09:00 → [13 ноя 22:00, 13 ноя 23:00, 14 ноя 08:00]
      14 ноя 15:00 → [13 ноя 22:00, 13 ноя 23:00, 14 ноя 14:00]
      13 ноя 18:00 → [13 ноя 17:00]  (22:00/23:00 после события — скип)
      13 ноя 17:30 (now=17:30) → [13 ноя 17:25? нет, 5м уже...]

    Примеры (now = 13 ноя 17:30):
      13 ноя 18:00 → [13 ноя 17:55]  («за час» прошёл, «за 5 минут»)
    """
    now = _now()

    if kind == "delay":
        return [due_at]

    slots = []

    # 1. Вечером накануне: 22:00 и 23:00 (если событие завтра или позже)
    if due_at.date() > now.date():
        prev_evening = (due_at - timedelta(days=1)).replace(
            hour=22, minute=0, second=0, microsecond=0,
        )
        slots.append(prev_evening)                      # 22:00
        slots.append(prev_evening.replace(hour=23))     # 23:00

    # 2. За час до события
    one_hour_before = due_at - timedelta(hours=1)
    if one_hour_before > now:
        slots.append(one_hour_before)
    else:
        # 3. «За час» прошёл — за 5 минут до события
        five_min_before = due_at - timedelta(minutes=5)
        if five_min_before > now:
            slots.append(five_min_before)

    # Уникализация + сортировка (вечернее 23:00 может совпасть с «за час до 00:00»)
    return sorted(set(slots))


# ────────────────────────────────────────────────────────────
# ДОБАВЛЕНИЕ
# ────────────────────────────────────────────────────────────
def _add_entry(text: str, due_at: datetime, kind: str) -> dict | None:
    text = (text or "").strip()
    if not text or due_at is None:
        return None

    # FIX (2026-11-13): дедуп. Если уже есть невыполненное напоминание
    # с тем же text и тем же due_at (до минуты) — не добавляем второе,
    # возвращаем существующее с флагом "_duplicate": True.
    # Скил по флагу скажет "Уже записано", а не "Записал".
    due_key = due_at.replace(second=0, microsecond=0)
    data = _load()
    for p in data:
        if p.get("done"):
            continue
        try:
            p_due = datetime.fromisoformat(p["due_at"]).replace(second=0, microsecond=0)
        except Exception:
            continue
        if p.get("text", "").strip().lower() == text.lower() and p_due == due_key:
            log(f"reminders: дубликат «{text}» на {p['due_at']} — не добавляю",
                tag="BRAIN")
            result = dict(p)
            result["_duplicate"] = True
            return result

    next_id = max((p.get("id", 0) for p in data), default=0) + 1
    entry = {
        "id": next_id,
        "text": text,
        "due_at": due_at.isoformat(timespec="seconds"),
        "created_at": _now().isoformat(timespec="seconds"),
        "kind": kind,
        "done": False,
    }
    data.append(entry)
    _save()
    log(f"reminders: добавлено #{next_id} «{text}» на {entry['due_at']} ({kind})", tag="BRAIN")
    return entry


def add(text: str, delay_minutes: float) -> dict | None:
    """Добавить напоминание через delay_minutes минут (kind="delay")."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        delay_minutes = float(delay_minutes)
    except (TypeError, ValueError):
        return None
    if delay_minutes <= 0:
        return None

    due = _now() + timedelta(minutes=delay_minutes)
    return _add_entry(text, due, kind="delay")


def add_at(text: str, due_at: datetime) -> dict | None:
    """Добавить напоминание на конкретную дату/время (kind="date")."""
    return _add_entry(text, due_at, kind="date")


# ────────────────────────────────────────────────────────────
# ЧТЕНИЕ
# ────────────────────────────────────────────────────────────
def get_pending() -> list:
    return [p for p in _load() if not p.get("done")]


def get_upcoming(text: str) -> list:
    """Все невыполненные напоминания, где text в тексте."""
    text = (text or "").strip().lower()
    if not text:
        return []
    return [
        p for p in _load()
        if not p.get("done") and text in p.get("text", "").lower()
    ]


def get_due_slots() -> list:
    """Список (reminder, slot_index, slot_time) — просроченных и неотмеченных.

    FIX (2026-11-13): проверяем СЛОТ, а не due.
    Если слот просрочен > MAX_SLOT_AGE_MINUTES — не озвучивать.
    Если все слоты просрочены и due в прошлом — удалить напоминание.
    """
    global _cache
    now = _now()
    result = []
    to_remove = []

    for p in _load():
        if p.get("done"):
            continue
        try:
            due = datetime.fromisoformat(p["due_at"])
        except Exception:
            continue

        kind = p.get("kind", "date")
        slots = get_reminder_times(due, kind)

        slot_fresh = False
        for idx, slot in enumerate(slots):
            key = (p["id"], idx)
            if key in _reminded_slots:
                continue
            if now - slot > timedelta(minutes=MAX_SLOT_AGE_MINUTES):
                continue          # слот просрочен — не озвучивать
            slot_fresh = True
            if slot <= now:
                result.append((p, idx, slot))

        # Если все слоты просрочены и due в прошлом — удалить
        if not slot_fresh and now > due:
            to_remove.append(p["id"])

    if to_remove:
        data = _load()
        data = [p for p in data if p["id"] not in to_remove]
        _cache = data
        _save()
        log(f"reminders: удалено устаревших #{to_remove}", tag="BRAIN")

    return result


def mark_done(reminder_id: int) -> bool:
    data = _load()
    for p in data:
        if p.get("id") == reminder_id:
            p["done"] = True
            p["done_at"] = _now().isoformat(timespec="seconds")
            _save()
            log(f"reminders: #{reminder_id} выполнено", tag="BRAIN")
            return True
    return False


def find_by_text(text: str) -> dict | None:
    text = (text or "").strip().lower()
    if not text:
        return None
    for p in _load():
        if p.get("done"):
            continue
        if text in p.get("text", "").lower():
            return p
    return None


def mark_done_by_text(text: str) -> bool:
    p = find_by_text(text)
    if p:
        return mark_done(p["id"])
    return False


# ────────────────────────────────────────────────────────────
# ФОНОВЫЙ ПОТОК
# ────────────────────────────────────────────────────────────
def check_and_remind():
    """Проверить просроченные слоты и сказать вслух."""
    try:
        from brain.skills._helpers import respond_and_say
    except Exception:
        return

    due = get_due_slots()
    if not due:
        return

    p, idx, slot = due[0]
    _reminded_slots.add((p["id"], idx))
    log(f"reminders: напоминание #{p['id']} слот {idx} «{p['text']}»", tag="BRAIN")

    try:
        respond_and_say("REMINDER_TRIGGER", text=p["text"])
    except Exception as e:
        log(f"reminders: respond_and_say: {e}", tag="BRAIN", level="WARN")


def _worker_loop():
    log("reminders: воркер запущен", tag="BRAIN")
    while not _stop.is_set():
        try:
            check_and_remind()
        except Exception as e:
            log(f"reminders: worker: {e}", tag="BRAIN", level="WARN")
        _stop.wait(CHECK_INTERVAL_SEC)
    log("reminders: воркер остановлен", tag="BRAIN")


def start_worker():
    global _worker
    with _lock:
        if _worker is not None and _worker.is_alive():
            return
        _stop.clear()
        _worker = threading.Thread(target=_worker_loop, name="reminders-worker", daemon=True)
        _worker.start()


def stop_worker():
    _stop.set()
    if _worker:
        _worker.join(timeout=2.0)