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
#   "date"  — конкретная дата/время — два слота:
#             до 12:00 → 21:00 и 22:00 предыдущего дня;
#             после 12:00 → 10:00 и 11:00 этого же дня.
#
# Просроченные: если событие прошло больше MAX_SLOT_AGE_MINUTES минут —
# напоминание удаляется, не озвучивается. Иначе — озвучивается.
#
# check_and_remind() — фоновый поток: раз в 30 сек проверяет слоты
# и говорит вслух. Не зависит от wake / команды.

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
MAX_SLOT_AGE_MINUTES = 30  # если событие прошло > 30 мин — удалить, не говорить


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
      Если событие до 12:00 — 21:00 и 22:00 предыдущего дня.
      Если после 12:00 — 10:00 и 11:00 этого же дня.
    """
    if kind == "delay":
        return [due_at]
    if due_at.hour < 12:
        prev = due_at - timedelta(days=1)
        return [
            prev.replace(hour=21, minute=0, second=0, microsecond=0),
            prev.replace(hour=22, minute=0, second=0, microsecond=0),
        ]
    return [
        due_at.replace(hour=10, minute=0, second=0, microsecond=0),
        due_at.replace(hour=11, minute=0, second=0, microsecond=0),
    ]


# ────────────────────────────────────────────────────────────
# ДОБАВЛЕНИЕ
# ────────────────────────────────────────────────────────────
def _add_entry(text: str, due_at: datetime, kind: str) -> dict | None:
    text = (text or "").strip()
    if not text or due_at is None:
        return None

    data = _load()
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

    Просроченные напоминания (событие прошло > MAX_SLOT_AGE_MINUTES) удаляются.
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

        # Событие прошло больше чем на MAX_SLOT_AGE_MINUTES — удалить
        if now - due > timedelta(minutes=MAX_SLOT_AGE_MINUTES):
            to_remove.append(p["id"])
            continue

        kind = p.get("kind", "date")
        slots = get_reminder_times(due, kind)
        for idx, slot in enumerate(slots):
            key = (p["id"], idx)
            if key in _reminded_slots:
                continue
            if slot <= now:
                result.append((p, idx, slot))

    # Удалить устаревшие
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