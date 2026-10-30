"""
brain/relationship.py — модель привычек пользователя.

Погода → музыка, вчерашнее. Не «профиль», а динамика.

Хранится в data/relationship.json:
{
  "weather_music": {...},
  "asked_recent": [...],
  "declined_recent": [...],
  "asked_today": {...},
  "last_prompt_type": ...,
  "last_volume": 100,
  "volume_changed_at": ...
}

A2-фикс (день 30): get_weather_music больше не сбрасывает mood через 7 дней.
  Раньше: asked_at старше 7 дней → None → снова спрашивал. Теперь:
  mood помнится, пока не перезапишется. Спрашивать раз в день — через
  was_asked_today("weather_<precip>") в music/skill.py.
"""

import json
import os
import random
from datetime import datetime, date

from shared import config as cfg


RELATIONSHIP_FILE = os.path.join(cfg.DATA_DIR, 'relationship.json')

_cache = None


def _load() -> dict:
    global _cache
    if _cache is not None:
        return _cache
    if not os.path.exists(RELATIONSHIP_FILE):
        _cache = {}
        return _cache
    try:
        with open(RELATIONSHIP_FILE, 'r', encoding='utf-8') as f:
            _cache = json.load(f)
    except Exception:
        _cache = {}
    return _cache


def _save():
    if _cache is None:
        return
    try:
        os.makedirs(os.path.dirname(RELATIONSHIP_FILE), exist_ok=True)
        with open(RELATIONSHIP_FILE, 'w', encoding='utf-8') as f:
            json.dump(_cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠ relationship: ошибка сохранения: {e}")


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _today_iso() -> str:
    return date.today().isoformat()


# ────────────────────────────────────────────────────────────
# ПОГОДА → МУЗЫКА
# ────────────────────────────────────────────────────────────
def get_weather_music(condition: str) -> str | None:
    if not condition:
        return None
    data = _load()
    wm = data.get("weather_music", {})
    entry = wm.get(condition)
    if not entry:
        return None
    # A2-фикс: TTL убран. mood помнится, пока не перезапишется.
    return entry.get("mood")


def set_weather_music(condition: str, mood: str | None):
    if not condition:
        return
    data = _load()
    wm = data.setdefault("weather_music", {})
    prev = wm.get(condition, {})
    wm[condition] = {
        "mood": mood,
        "asked_at": _now_iso(),
        "times_asked": prev.get("times_asked", 0) + 1,
    }
    _save()


def weather_known(condition: str) -> bool:
    return get_weather_music(condition) is not None


# ────────────────────────────────────────────────────────────
# ВЧЕРАШНЕЕ
# ────────────────────────────────────────────────────────────
def get_recent_prompt(exclude_asked: bool = True) -> dict | None:
    try:
        from memory import memory
        top = memory.top_artists(limit=20, days=1)
    except Exception as e:
        print(f"⚠ relationship: memory.top_artists: {e}")
        return None

    if not top:
        return None

    data = _load()
    asked = set(data.get("asked_recent", []))
    declined = set(data.get("declined_recent", []))

    candidates = []
    for item in top:
        if isinstance(item, dict):
            name = item.get("artist") or item.get("name")
            plays = item.get("plays") or item.get("count") or 1
        elif isinstance(item, (tuple, list)) and len(item) >= 2:
            name = item[0]
            plays = item[1]
        else:
            continue

        if not name:
            continue
        if name in declined:
            continue
        if exclude_asked and name in asked:
            continue
        candidates.append({"name": name, "plays": int(plays) if plays else 1})

    if not candidates:
        return None

    weights = [c["plays"] for c in candidates]
    total = sum(weights)
    if total <= 0:
        return random.choice(candidates)
    r = random.uniform(0, total)
    upto = 0.0
    for c, w in zip(candidates, weights):
        upto += w
        if r <= upto:
            return c
    return candidates[-1]


def mark_recent_asked(artist: str):
    if not artist:
        return
    data = _load()
    asked = data.setdefault("asked_recent", [])
    if artist not in asked:
        asked.append(artist)
        if len(asked) > 20:
            asked[:] = asked[-20:]
    data["last_prompt_type"] = "recent"
    _save()


def mark_recent_declined(artist: str):
    if not artist:
        return
    data = _load()
    declined = data.setdefault("declined_recent", [])
    if artist not in declined:
        declined.append(artist)
        if len(declined) > 20:
            declined[:] = declined[-20:]
    data["last_prompt_type"] = "recent"
    _save()


# ────────────────────────────────────────────────────────────
# ЧЕРЕДОВАНИЕ
# ────────────────────────────────────────────────────────────
def get_last_prompt_type() -> str | None:
    data = _load()
    return data.get("last_prompt_type")


def set_last_prompt_type(kind: str):
    data = _load()
    data["last_prompt_type"] = kind
    _save()


# ────────────────────────────────────────────────────────────
# УТРО / НОЧЬ
# ────────────────────────────────────────────────────────────
def was_asked_today(kind: str) -> bool:
    data = _load()
    asked_today = data.get("asked_today", {})
    return asked_today.get(kind) == _today_iso()


def mark_asked_today(kind: str):
    data = _load()
    asked_today = data.setdefault("asked_today", {})
    asked_today[kind] = _today_iso()
    data["last_prompt_type"] = "time"
    _save()


# ────────────────────────────────────────────────────────────
# ГРОМКОСТЬ
# ────────────────────────────────────────────────────────────
def get_last_volume() -> int | None:
    data = _load()
    return data.get("last_volume")


def volume_changed_recently(within_days: int = 1) -> bool:
    data = _load()
    changed_at = data.get("volume_changed_at")
    if not changed_at:
        return False
    try:
        dt = datetime.fromisoformat(changed_at)
        return (datetime.now() - dt).days <= within_days
    except Exception:
        return False


def set_volume(volume: int):
    data = _load()
    data["last_volume"] = int(volume)
    data["volume_changed_at"] = _now_iso()
    _save()


# ────────────────────────────────────────────────────────────
# I: ПРИВЫЧКИ И ПАТТЕРНЫ (через memory)
# ────────────────────────────────────────────────────────────
def get_habit(time_bucket: str) -> dict | None:
    try:
        from memory import memory
        habits = memory.get_habit(time_bucket)
        if not habits:
            return None
        top = max(habits.items(), key=lambda x: x[1])
        if top[1] >= 3:
            return {"artist": top[0], "count": top[1]}
    except Exception as e:
        print(f"⚠ relationship.get_habit: {e}")
    return None


def get_pattern(condition: str) -> dict | None:
    try:
        from memory import memory
        patterns = memory.get_pattern(condition)
        if not patterns:
            return None
        top = max(patterns.items(), key=lambda x: x[1])
        if top[1] >= 3:
            return {"artist": top[0], "count": top[1]}
    except Exception as e:
        print(f"⚠ relationship.get_pattern: {e}")
    return None


def suggest_for_context(weather: str = "", time_bucket: str = "") -> dict | None:
    if time_bucket:
        habit = get_habit(time_bucket)
        if habit:
            return {"kind": "habit", **habit}
    if weather:
        pattern = get_pattern(f"weather:{weather}")
        if pattern:
            return {"kind": "pattern", **pattern}
    return None


def record_play_habit(time_bucket: str, artist: str):
    try:
        from memory import memory
        memory.set_habit(time_bucket, artist, count=1)
    except Exception as e:
        print(f"⚠ relationship.record_play_habit: {e}")


def record_play_pattern(condition: str, artist: str):
    try:
        from memory import memory
        memory.set_pattern(condition, artist, count=1)
    except Exception as e:
        print(f"⚠ relationship.record_play_pattern: {e}")