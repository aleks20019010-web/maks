# brain/liked_tracks.py
# Локальный плейлист лайков. Единая полка: голосовые лайки + импорт из Яндекса.
#
# Формат data/liked_tracks.json:
#   [{"artist": "...", "title": "...", "track_id": "...", "ts": 1234567890, "source": "voice|yandex"}]
#
# Правила:
#   - Только дополняется. Никогда не удаляет (кроме явного remove_like).
#   - Дубликаты не плодим: ключ (artist.lower().strip(), title.lower().strip()).
#   - Импорт из Яндекса добавляет новые, не трогает старые.
#
# API:
#   add_like(artist, title, track_id="", source="voice") → True если добавлен
#   get_liked() → list[dict]
#   merge_from_yandex(tracks) → int (сколько добавлено)
#   is_liked(artist, title) → bool
#   remove_like(artist, title) → bool
#   clear_liked() → int
#
# v6.9: remove_like — «мне не нравится» убирает из лайков, не добавляет
#   в отдельный disliked_tracks.json. Дизлайк = удаление из liked_tracks.

import json
import os
import time

from shared import config as cfg
from shared.log import log


LIKED_FILE = os.path.join(cfg.DATA_DIR, "liked_tracks.json")


def _key(artist: str, title: str) -> tuple:
    return ((artist or "").lower().strip(), (title or "").lower().strip())


def _read() -> list:
    if not os.path.exists(LIKED_FILE):
        return []
    try:
        with open(LIKED_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as e:
        log(f"liked_tracks: read: {e}", tag="BRAIN", level="WARN")
        return []


def _write(data: list) -> bool:
    try:
        os.makedirs(os.path.dirname(LIKED_FILE), exist_ok=True)
        with open(LIKED_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        log(f"liked_tracks: write: {e}", tag="BRAIN", level="WARN")
        return False


def is_liked(artist: str, title: str) -> bool:
    k = _key(artist, title)
    for item in _read():
        if _key(item.get("artist", ""), item.get("title", "")) == k:
            return True
    return False


def add_like(artist: str, title: str, track_id: str = "",
             source: str = "voice") -> bool:
    """Добавить в лайки. True — добавлен, False — уже был / пустые поля."""
    if not (artist or title):
        return False
    if is_liked(artist, title):
        return False
    data = _read()
    data.append({
        "artist": artist or "",
        "title": title or "",
        "track_id": str(track_id) if track_id else "",
        "ts": time.time(),
        "source": source,
    })
    return _write(data)


def merge_from_yandex(tracks: list) -> int:
    """Дополнить лайки из Яндекса. tracks — [{"artist","title","track_id"}].
    Возвращает сколько добавлено. Дубликаты не плодит.
    """
    if not tracks:
        return 0
    data = _read()
    existing = {_key(i.get("artist", ""), i.get("title", "")) for i in data}
    added = 0
    for t in tracks:
        artist = t.get("artist", "") or ""
        title = t.get("title", "") or ""
        if not (artist or title):
            continue
        k = _key(artist, title)
        if k in existing:
            continue
        data.append({
            "artist": artist,
            "title": title,
            "track_id": str(t.get("track_id", "") or ""),
            "ts": time.time(),
            "source": "yandex",
        })
        existing.add(k)
        added += 1
    if added:
        _write(data)
    return added


def get_liked() -> list:
    return _read()


def remove_like(artist: str, title: str) -> bool:
    """Убрать из лайков. True — убрано, False — не было / пустые поля."""
    if not (artist or title):
        return False
    k = _key(artist, title)
    data = _read()
    new_data = [i for i in data
                if _key(i.get("artist", ""), i.get("title", "")) != k]
    if len(new_data) == len(data):
        return False
    return _write(new_data)


def clear_liked() -> int:
    """Очистить все лайки. Возвращает сколько было."""
    data = _read()
    if _write([]):
        return len(data)
    return 0