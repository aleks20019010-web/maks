# brain/liked_tracks.py
# Локальный плейлист лайков. Единая полка: голосовые лайки + импорт из Яндекса.

import json
import os
import threading
import time

from shared import config as cfg
from shared.log import log


LIKED_FILE = os.path.join(cfg.DATA_DIR, "liked_tracks.json")
_lock = threading.RLock()


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
        tmp_file = LIKED_FILE + ".tmp"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_file, LIKED_FILE)
        return True
    except Exception as e:
        log(f"liked_tracks: write: {e}", tag="BRAIN", level="WARN")
        return False


def is_liked(artist: str, title: str) -> bool:
    k = _key(artist, title)
    with _lock:
        for item in _read():
            if _key(item.get("artist", ""), item.get("title", "")) == k:
                return True
    return False


def add_like(artist: str, title: str, track_id: str = "",
             source: str = "voice") -> bool:
    if not (artist or title):
        return False
    with _lock:
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
    if not tracks:
        return 0
    with _lock:
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
    with _lock:
        return _read()


def remove_like(artist: str, title: str) -> bool:
    if not (artist or title):
        return False
    k = _key(artist, title)
    with _lock:
        data = _read()
        new_data = [i for i in data
                    if _key(i.get("artist", ""), i.get("title", "")) != k]
        if len(new_data) == len(data):
            return False
        return _write(new_data)


def clear_liked() -> int:
    with _lock:
        data = _read()
        if _write([]):
            return len(data)
        return 0