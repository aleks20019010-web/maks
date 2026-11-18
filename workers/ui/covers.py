# workers/ui/covers.py
# Фоновая загрузка обложек треков (кэш на диске, ID3-теги, URL Яндекса, Deezer/iTunes).

import hashlib
import os
import threading
import requests
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor, QPixmap

from shared import config as cfg
from workers.ui.theme import (
    extract_accent_color,
    make_default_cover,
    make_rounded_pixmap,
)

COVER_CACHE_DIR = os.path.join(cfg.CACHE_DIR, "covers")


def _extract_id3_cover(file_path: str) -> bytes | None:
    if not file_path or not os.path.exists(file_path):
        return None
    try:
        from mutagen import File as MutagenFile
        audio = MutagenFile(file_path)
        if audio is None or not getattr(audio, "tags", None):
            return None
        for tag in audio.tags.values():
            if hasattr(tag, "data") and isinstance(tag.data, (bytes, bytearray)):
                return bytes(tag.data)
    except Exception:
        pass
    return None


def _fetch_fallback_cover(artist: str, title: str) -> bytes | None:
    query = f"{artist} {title}".strip()
    if not query:
        return None

    try:
        r = requests.get(
            "https://api.deezer.com/search",
            params={"q": query, "limit": 1},
            timeout=3.5,
        )
        if r.status_code == 200:
            items = r.json().get("data") or []
            if items:
                album = items[0].get("album") or {}
                img_url = album.get("cover_big") or album.get("cover_medium")
                if img_url:
                    img_r = requests.get(img_url, timeout=3.5)
                    if img_r.status_code == 200 and len(img_r.content) > 256:
                        return img_r.content
    except Exception:
        pass

    try:
        r = requests.get(
            "https://itunes.apple.com/search",
            params={"term": query, "media": "music", "entity": "song", "limit": 1},
            timeout=3.5,
        )
        if r.status_code == 200:
            results = r.json().get("results") or []
            if results:
                art = results[0].get("artworkUrl100", "")
                if art:
                    img_url = art.replace("100x100bb", "400x400bb")
                    img_r = requests.get(img_url, timeout=3.5)
                    if img_r.status_code == 200 and len(img_r.content) > 256:
                        return img_r.content
    except Exception:
        pass

    return None


class CoverLoader(QObject):
    loaded = Signal(str, QPixmap, QColor)

    def __init__(self, cover_size: int):
        super().__init__()
        self._size = cover_size
        self._current_key = ""
        self.default_cover = make_default_cover(cover_size)

    @property
    def current_key(self) -> str:
        return self._current_key

    def request(self, cover_url: str, file_path: str, artist: str = "", title: str = ""):
        key = cover_url or file_path or f"{artist}|{title}".strip("|") or "default"
        if key == self._current_key:
            return
        self._current_key = key

        if key == "default":
            self.loaded.emit(key, self.default_cover, QColor("#6366f1"))
            return

        def worker():
            os.makedirs(COVER_CACHE_DIR, exist_ok=True)
            h = hashlib.md5(key.encode("utf-8")).hexdigest()
            cached_path = os.path.join(COVER_CACHE_DIR, f"{h}.jpg")

            raw_bytes = None
            if os.path.exists(cached_path):
                try:
                    with open(cached_path, "rb") as f:
                        raw_bytes = f.read()
                except Exception:
                    pass

            if raw_bytes is None and file_path:
                raw_bytes = _extract_id3_cover(file_path)

            if raw_bytes is None and cover_url:
                try:
                    r = requests.get(cover_url, timeout=4.0)
                    if r.status_code == 200 and len(r.content) > 256:
                        raw_bytes = r.content
                except Exception:
                    pass

            if raw_bytes is None and (artist or title):
                raw_bytes = _fetch_fallback_cover(artist, title)

            if raw_bytes:
                try:
                    with open(cached_path, "wb") as f:
                        f.write(raw_bytes)
                except Exception:
                    pass

            pix = QPixmap()
            if raw_bytes and pix.loadFromData(raw_bytes):
                rounded = make_rounded_pixmap(pix, self._size, 22)
                accent = extract_accent_color(pix)
            else:
                rounded = self.default_cover
                accent = QColor("#6366f1")

            self.loaded.emit(key, rounded, accent)

        threading.Thread(target=worker, daemon=True, name="ui-cover").start()