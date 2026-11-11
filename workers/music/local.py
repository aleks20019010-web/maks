# workers/music/local.py
# Локальные треки: класс LocalTrack, поиск случайного трека, поиск по жанру.
# Blacklist: локальные треки проверяются по названию (track_stats.title).
#
# FIX (2026-11-08): LocalTrack читает теги (mutagen) — artist, title, album.
# FIX (2026-11-08, #2): JSON-кэш индекса (data/local_index.json).
#
# ── FIX (этап 0.5): get_local_random_track — O(N), не O(N×normalize×SQL) ──
#   _played_key_cache (dict[path, key]) — первый вызов 3–5 сек,
#   дальше 0.05. _get_blacklisted_titles — ОДИН SQL → set.
#   get_local_random_track / get_local_by_genre — через два set.
#
# ── FIX (этап 0.5): регистр в blacklist ────────────────────────
#   SQLite LOWER() не работает для кириллицы. SELECT title БЕЗ LOWER(),
#   .lower() в Python.
#
# ── FIX (этап 0.4): invalidate_index + add_path_to_index ───────
#   download_track после subprocess → invalidate_index() +
#   add_path_to_index(path). Инкрементально, 0.001 сек, вместо
#   _build_index (8 сек) или all_audio_files(force_refresh=True),
#   который читает СТАРЫЙ JSON (новый файл не виден).
#
# ── FIX (этап 0.5): print → log(tag="MUSIC") ────────────────────
#   Раньше все сообщения local.py шли в stderr (print), в jarvis.log
#   их не было.
#
# ── LIKED_TRACK (v6.5) ──────────────────────────────────────────
#   LikedTrack — лёгкий дескриптор лайка (artist, title, track_id).
#   Не грузит Яндекс. Скачивается по требованию в playback.play_index
#   (search_local → download_by_id(track.id)).

import os
import json
import glob
import random
import threading
from datetime import datetime

from shared import config as cfg
from shared.log import log
from shared.audio_cache import normalize
from workers.music import state
from workers.music._shared import GENRE_TAGS_MAP, _clean_filename


try:
    from mutagen import File as MutagenFile
except ImportError:
    MutagenFile = None


# ── JSON-кэш индекса (2026-11-08, #2) ──────────────────────
_index_cache = None
_index_lock = threading.Lock()
_refresh_thread = None
_INDEX_FILE = cfg.DATA_DIR / "local_index.json"


# ── FIX (этап 0.5): кэш played_key ─────────────────────────
_played_key_cache: dict[str, str] = {}
_played_key_lock = threading.Lock()


def _read_tags(path: str) -> dict:
    """Прочитать теги (artist, title, album, genre). {} — если нет mutagen/тегов."""
    if MutagenFile is None:
        return {}
    try:
        f = MutagenFile(path, easy=True)
        if f is None or not f.tags:
            return {}
        def _first(key):
            v = f.get(key)
            return v[0] if v else None
        artist = _first("artist")
        if artist:
            artist = artist.lstrip("@").strip()
        return {
            "artist": artist,
            "title": _first("title"),
            "album": _first("album"),
            "genre": _first("genre"),
        }
    except Exception:
        return {}


def _build_index() -> dict:
    """Скан MUSIC_DIR + mutagen → {version, mtime, tracks: [...]}."""
    files = []
    for ext in ('*.mp3', '*.flac', '*.m4a', '*.aac', '*.ogg'):
        files += glob.glob(os.path.join(cfg.MUSIC_DIR, '**', ext), recursive=True)
    files = [f for f in files if '_tts' not in f and 'Jarvis' not in f]

    tracks = []
    for path in files:
        tags = _read_tags(path)
        try:
            st = os.stat(path)
            mtime, size = st.st_mtime, st.st_size
        except Exception:
            mtime, size = 0, 0
        tracks.append({
            "path": path,
            "artist": tags.get("artist") or "",
            "title": tags.get("title") or _clean_filename(path),
            "album": tags.get("album") or "",
            "genre": (tags.get("genre") or "").lower(),
            "mtime": mtime,
            "size": size,
        })

    try:
        music_mtime = os.stat(cfg.MUSIC_DIR).st_mtime
    except Exception:
        music_mtime = 0.0

    return {
        "version": 1,
        "music_dir_mtime": music_mtime,
        "built_at": datetime.now().isoformat(),
        "tracks": tracks,
    }


def _save_index(data: dict):
    try:
        _INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_INDEX_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        log(f"save index: {e}", tag="MUSIC", level="WARN")


def _load_index_sync() -> dict:
    global _index_cache
    if _index_cache is not None:
        return _index_cache

    if _INDEX_FILE.exists():
        try:
            with open(_INDEX_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            _index_cache = data
            return data
        except Exception as e:
            log(f"индекс битый: {e} — строю заново", tag="MUSIC", level="WARN")

    log("первый запуск — строю индекс (8 сек)...", tag="MUSIC")
    data = _build_index()
    _save_index(data)
    _index_cache = data
    log(f"индекс построен ({len(data['tracks'])} треков)", tag="MUSIC")
    return data


def _refresh_index_async():
    global _refresh_thread, _index_cache
    if _refresh_thread is not None and _refresh_thread.is_alive():
        return

    def runner():
        try:
            try:
                music_mtime = os.stat(cfg.MUSIC_DIR).st_mtime
            except Exception:
                music_mtime = 0.0

            if _INDEX_FILE.exists():
                try:
                    with open(_INDEX_FILE, "r", encoding="utf-8") as f:
                        old = json.load(f)
                    if old.get("music_dir_mtime") == music_mtime:
                        return
                except Exception:
                    pass

            log("обновляю индекс в фоне...", tag="MUSIC")
            data = _build_index()
            _save_index(data)
            with _index_lock:
                _index_cache = data
            log(f"индекс обновлён ({len(data['tracks'])} треков)", tag="MUSIC")
        except Exception as e:
            log(f"refresh index: {e}", tag="MUSIC", level="WARN")

    _refresh_thread = threading.Thread(target=runner, daemon=True)
    _refresh_thread.start()


def _load_index(force_refresh=False) -> dict:
    data = _load_index_sync()
    if not force_refresh:
        _refresh_index_async()
    return data


# ── FIX (этап 0.4): invalidate + add_path ──────────────────
def invalidate_index():
    """Сбросить _index_cache. После download_track — чтобы новый файл
    был виден (add_path_to_index допишет, _load_index_sync перечитает)."""
    global _index_cache
    with _index_lock:
        _index_cache = None


def add_path_to_index(path: str):
    """Дописать одну запись в JSON, не пересобирая.

    FIX (этап 0.4): download_track после subprocess → add_path_to_index.
    Раньше: all_audio_files(force_refresh=True) читает СТАРЫЙ JSON,
    new_files пусто → download_track возвращает None (Яндекс-треки
    не играют). Теперь: инкрементально, 0.001 сек.
    """
    try:
        idx = _load_index_sync()
        tracks = idx.get("tracks") or []
        if any(t.get("path") == path for t in tracks):
            return
        tags = _read_tags(path)
        try:
            st = os.stat(path)
            mtime, size = st.st_mtime, st.st_size
        except Exception:
            mtime, size = 0, 0
        tracks.append({
            "path": path,
            "artist": tags.get("artist") or "",
            "title": tags.get("title") or _clean_filename(path),
            "album": tags.get("album") or "",
            "genre": (tags.get("genre") or "").lower(),
            "mtime": mtime,
            "size": size,
        })
        idx["tracks"] = tracks
        _save_index(idx)
        with _index_lock:
            _index_cache = idx
    except Exception as e:
        log(f"add_path_to_index: {e}", tag="MUSIC", level="WARN")


class LocalTrack:
    def __init__(self, path):
        self.path = path
        self.id = hash(path)

        tags = _read_tags(path)
        if tags.get("artist") and tags.get("title"):
            self._artist = tags["artist"]
            self.title = tags["title"]
        elif tags.get("title"):
            self._artist = "Локальная"
            self.title = tags["title"]
        else:
            name = _clean_filename(path)
            if ' - ' in name:
                parts = name.split(' - ', 1)
                self._artist = parts[0].strip()
                self.title = parts[1].strip()
            else:
                self._artist = "Локальная"
                self.title = name

        class _A:
            def __init__(self, n): self.name = n
        self.artists = [_A(self._artist)]
        self.albums = []


def _local_played_key(path: str) -> str:
    """normalize(имя файла без расширения), с кэшем.

    Первый вызов для path — normalize (0.001 сек). Дальше — словарь.
    На 3000 треков: первый раз 3–5 сек, дальше 0.05.
    """
    key = _played_key_cache.get(path)
    if key is not None:
        return key
    with _played_key_lock:
        key = _played_key_cache.get(path)
        if key is None:
            name = os.path.splitext(os.path.basename(path))[0]
            key = normalize(name)
            _played_key_cache[path] = key
    return key


def _get_blacklisted_titles() -> set[str]:
    """Все заблокированные title (lower) одним запросом.

    FIX (этап 0.5): SELECT title БЕЗ LOWER(). SQLite LOWER() работает
    только для ASCII, кириллицу не трогает. .lower() в Python.
    Раньше: _is_local_blacklisted(path) → query_one на КАЖДЫЙ трек
    (3000 SQL = 5–10 сек). Теперь: один SELECT → set[str], O(1).
    """
    try:
        from memory import memory
        rows = memory.storage.query(
            "SELECT title FROM track_stats WHERE blacklisted = 1"
        )
        return {(r.get("title") or "").lower() for r in rows if r.get("title")}
    except Exception:
        return set()


def _is_local_blacklisted(path: str) -> bool:
    """[DEPRECATED, не использовать в цикле]

    FIX (этап 0.5): SELECT title БЕЗ LOWER(), .lower() в Python
    (SQLite LOWER не работает для кириллицы).
    В цикле по 3000 трекам — 5–10 сек, используйте
    _get_blacklisted_titles() + set.
    """
    try:
        from memory import memory
        name = _local_played_key(path)
        rows = memory.storage.query(
            "SELECT title FROM track_stats WHERE blacklisted = 1"
        )
        titles = {(r.get("title") or "").lower() for r in rows if r.get("title")}
        return name.lower() in titles
    except Exception:
        return False


def get_local_random_track():
    """FIX (этап 0.5): O(N) по словарю, не O(N×normalize×SQL)."""
    idx = _load_index()
    tracks = idx.get("tracks") or []
    if not tracks:
        return None

    with state.session_played_lock:
        played = set(state.session_played_ids)
    blacklisted = _get_blacklisted_titles()

    candidates = [
        t for t in tracks
        if _local_played_key(t["path"]) not in played
        and _local_played_key(t["path"]).lower() not in blacklisted
    ]
    if not candidates:
        with state.session_played_lock:
            state.session_played_ids.clear()
        candidates = [
            t for t in tracks
            if _local_played_key(t["path"]).lower() not in blacklisted
        ]
    if not candidates:
        return None

    t = random.choice(candidates)
    with state.session_played_lock:
        state.session_played_ids.add(_local_played_key(t["path"]))
    return t["path"]


def _read_genre(file_path):
    genre_file = os.path.splitext(file_path)[0] + ".genre"
    if os.path.exists(genre_file):
        try:
            with open(genre_file, "r", encoding="utf-8") as f:
                return f.read().strip().lower()
        except Exception:
            pass
    tags = _read_tags(file_path)
    genre = tags.get("genre")
    if genre:
        return genre.lower().strip()
    return None


def get_local_by_genre(genre_query):
    """FIX (этап 0.5): из индекса (0.05 сек), не glob + не SQL в цикле."""
    idx = _load_index()
    q = normalize(genre_query)
    tags = GENRE_TAGS_MAP.get(q, [q])

    with state.session_played_lock:
        played = set(state.session_played_ids)
    blacklisted = _get_blacklisted_titles()

    candidates = []
    for t in idx.get("tracks", []):
        if t.get("genre") not in tags:
            continue
        key = _local_played_key(t["path"])
        if key in played:
            continue
        if key.lower() in blacklisted:
            continue
        candidates.append(t)

    if not candidates:
        return None

    t = random.choice(candidates)
    with state.session_played_lock:
        state.session_played_ids.add(_local_played_key(t["path"]))
    log(f"локальный по жанру «{genre_query}»: {os.path.basename(t['path'])}",
        tag="MUSIC", level="DEBUG")
    return t["path"]


class LikedTrack:
    """Лёгкий дескриптор лайка. Не грузит Яндекс.
    Скачивается по требованию в playback.play_index (по track_id)."""
    def __init__(self, artist: str, title: str, track_id: str = ""):
        self.artist = artist or ""
        self.title = title or ""
        self.id = track_id or ""
        self._is_discovery = False

    def __repr__(self):
        return f"LikedTrack({self.artist!r}, {self.title!r})"