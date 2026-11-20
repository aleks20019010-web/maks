# brain/reminders.py
# Напоминания с временем: «напомни через 10 минут X», «запомни 29 октября в 10:00 УЗИ».

import json
import os
import threading
from datetime import datetime, timedelta

from shared import config as cfg
from shared.log import log


REMINDERS_FILE = os.path.join(cfg.DATA_DIR, 'reminders.json')

_cache = None
_reminded_slots = set()  # (reminder_id, slot_iso)
_worker = None
_stop = threading.Event()
_lock = threading.RLock()

CHECK_INTERVAL_SEC = 15
MAX_SLOT_AGE_MINUTES = 5

REMINDER_TRIGGERS = ("напомни", "напомнить", "запомни", "запомнить")

_MONTHS_RU = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля",
    5: "мая", 6: "июня", 7: "июля", 8: "августа",
    9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
}


def fmt_due(due: datetime) -> str:
    month_ru = _MONTHS_RU.get(due.month, "")
    return f"{due.day} {month_ru} в {due.strftime('%H:%M')}"


def strip_reminder_trigger(cmd_lower: str) -> str | None:
    for trigger in REMINDER_TRIGGERS:
        prefix = trigger + " "
        if cmd_lower.startswith(prefix):
            return cmd_lower[len(prefix):].strip()
    return None


def _load() -> list:
    global _cache
    with _lock:
        if _cache is not None:
            return _cache
        if not os.path.exists(REMINDERS_FILE):
            _cache = []
            return _cache
        try:
            with open(REMINDERS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            _cache = data if isinstance(data, list) else []
        except Exception as e:
            log(f"reminders: ошибка чтения: {e}", tag="BRAIN", level="WARN")
            _cache = []
        return _cache


def _save():
    with _lock:
        if _cache is None:
            return
        try:
            os.makedirs(os.path.dirname(REMINDERS_FILE), exist_ok=True)
            tmp_file = REMINDERS_FILE + ".tmp"
            with open(tmp_file, 'w', encoding='utf-8') as f:
                json.dump(_cache, f, ensure_ascii=False, indent=2)
            os.replace(tmp_file, REMINDERS_FILE)
        except Exception as e:
            log(f"reminders: ошибка сохранения: {e}", tag="BRAIN", level="WARN")


def _now() -> datetime:
    return datetime.now()


def get_reminder_times(due_at: datetime, kind: str = "date", ref_now: datetime | None = None) -> list[datetime]:
    """Вычислить фиксированный список слотов относительно момента создания ref_now."""
    now = ref_now or _now()

    if kind == "delay":
        return [due_at]

    slots = []

    # 1. Вечером накануне: 22:00 и 23:00 (если событие завтра или позже)
    if due_at.date() > now.date():
        prev_evening = (due_at - timedelta(days=1)).replace(
            hour=22, minute=0, second=0, microsecond=0,
        )
        if prev_evening > now:
            slots.append(prev_evening)
        prev_23 = prev_evening.replace(hour=23)
        if prev_23 > now:
            slots.append(prev_23)

    # 2. За час до события
    one_hour_before = due_at - timedelta(hours=1)
    if one_hour_before > now:
        slots.append(one_hour_before)
    else:
        # 3. «За час» уже прошёл при создании — ставим за 5 минут до события
        five_min_before = due_at - timedelta(minutes=5)
        if five_min_before > now:
            slots.append(five_min_before)
        elif due_at > now:
            slots.append(due_at)

    return sorted(set(slots))


def _add_entry(text: str, due_at: datetime, kind: str) -> dict | None:
    text = (text or "").strip()
    if not text or due_at is None:
        return None

    now = _now()
    due_key = due_at.replace(second=0, microsecond=0)

    with _lock:
        data = _load()
        for p in data:
            if p.get("done"):
                continue
            try:
                p_due = datetime.fromisoformat(p["due_at"]).replace(second=0, microsecond=0)
            except Exception:
                continue
            if p.get("text", "").strip().lower() == text.lower() and p_due == due_key:
                log(f"reminders: дубликат «{text}» на {p['due_at']} — не добавляю", tag="BRAIN")
                result = dict(p)
                result["_duplicate"] = True
                return result

        slots_dt = get_reminder_times(due_at, kind, ref_now=now)
        next_id = max((p.get("id", 0) for p in data), default=0) + 1
        entry = {
            "id": next_id,
            "text": text,
            "due_at": due_at.isoformat(timespec="seconds"),
            "created_at": now.isoformat(timespec="seconds"),
            "kind": kind,
            "slots": [s.isoformat(timespec="seconds") for s in slots_dt],
            "done": False,
        }
        data.append(entry)
        _save()
        log(f"reminders: добавлено #{next_id} «{text}» на {entry['due_at']} ({kind}, слотов: {len(slots_dt)})", tag="BRAIN")
        return entry


def add(text: str, delay_minutes: float) -> dict | None:
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
    return _add_entry(text, due_at, kind="date")


def get_pending() -> list:
    with _lock:
        return [dict(p) for p in _load() if not p.get("done")]


def get_upcoming(text: str) -> list:
    text = (text or "").strip().lower()
    if not text:
        return []
    with _lock:
        return [
            dict(p) for p in _load()
            if not p.get("done") and text in p.get("text", "").lower()
        ]


def _entry_slots(p: dict, due: datetime) -> list[datetime]:
    raw_slots = p.get("slots")
    if isinstance(raw_slots, list) and raw_slots:
        out = []
        for s in raw_slots:
            try:
                out.append(datetime.fromisoformat(s))
            except Exception:
                continue
        if out:
            return out

    # Fallback для старых записей без сохранённых slots
    try:
        created = datetime.fromisoformat(p["created_at"])
    except Exception:
        created = due - timedelta(days=2)
    return get_reminder_times(due, p.get("kind", "date"), ref_now=created)


def get_due_slots() -> list:
    global _cache
    now = _now()
    result = []
    to_remove = []

    with _lock:
        for p in _load():
            if p.get("done"):
                continue
            try:
                due = datetime.fromisoformat(p["due_at"])
            except Exception:
                continue

            slots = _entry_slots(p, due)
            slot_fresh = False
            for idx, slot in enumerate(slots):
                slot_iso = slot.isoformat(timespec="seconds")
                key = (p["id"], slot_iso)
                if key in _reminded_slots:
                    continue
                if now - slot > timedelta(minutes=MAX_SLOT_AGE_MINUTES):
                    continue
                slot_fresh = True
                if slot <= now:
                    result.append((dict(p), slot_iso, slot))

            if not slot_fresh and now > due:
                to_remove.append(p["id"])

        if to_remove:
            data = _load()
            _cache = [p for p in data if p["id"] not in to_remove]
            _save()
            log(f"reminders: удалено устаревших #{to_remove}", tag="BRAIN")

    return result


def mark_done(reminder_id: int) -> bool:
    with _lock:
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
    with _lock:
        for p in _load():
            if p.get("done"):
                continue
            if text in p.get("text", "").lower():
                return dict(p)
    return None


def mark_done_by_text(text: str) -> bool:
    p = find_by_text(text)
    if p:
        return mark_done(p["id"])
    return False


def check_and_remind():
    try:
        from brain.skills._helpers import respond_and_say
    except Exception:
        return

    due = get_due_slots()
    if not due:
        return

    for p, slot_iso, slot in due:
        _reminded_slots.add((p["id"], slot_iso))
        log(f"reminders: напоминание #{p['id']} слот {slot_iso} «{p['text']}»", tag="BRAIN")
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