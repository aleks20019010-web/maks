# workers/music/local.py
# Локальные треки: класс LocalTrack, поиск случайного трека, поиск по жанру.
# Blacklist: локальные треки проверяются по названию (track_stats.title).
#
# FIX (2026-11-08): LocalTrack читает теги (mutagen) — artist, title, album.
#   Раньше: artist из имени файла ("1 - School Guide" → artist="1"),
#   или "Локальная" (если нет " - "). Теги (@ilymeedy, School Guide) НЕ
#   читались — LLM говорила "от Локальная", TTS "Молодость Ведьмы — Локальная".
#   Теперь: mutagen.File(path, easy=True) → artist="ilymeedy" (без @),
#   title="School Guide". Fallback на имя файла — если тегов нет.
#   mutagen — уже установлен (1.48.1). .m4a, .mp3, .flac — все читаются.
#
# FIX (2026-11-08, #2): JSON-кэш индекса (data/local_index.json).
#   Раньше: all_audio_files() → glob (8 сек на 3000 файлов) при первом
#   вызове. Первый «включи музыку» ждал 8 сек (glob) до локального трека.
#   Теперь:
#     - _load_index_sync() — JSON (0.05 сек) или _build_index (8 сек,
#       первый раз). Синхронно, но быстро (JSON).
#     - _refresh_index_async() — фон: glob (8 сек) → обновить JSON,
#       если mtime MUSIC_DIR изменился. НЕ блокирует музыку.
#     - Библиотека пополняется — JSON старый, музыка играет (старый
#       индекс). Фон обновляет (8 сек). Следующий запуск — актуальный JSON.
#   Ключевое: glob НИКОГДА не блокирует первый трек. Всегда JSON (0.05 сек).
#   Обновление — в фоне (после старта музыки).

import os
import re
import json
import glob
import random
import threading
from datetime import datetime

from shared import config as cfg
from shared.audio_cache import all_audio_files, normalize
from workers.music import state
from workers.music._shared import GENRE_TAGS_MAP, _clean_filename


# FIX (2026-11-08): mutagen для чтения тегов. Если нет — fallback на имя файла.
try:
    from mutagen import File as MutagenFile
except ImportError:
    MutagenFile = None


# ── JSON-кэш индекса (2026-11-08, #2) ──────────────────────
_index_cache = None
_index_lock = threading.Lock()
_refresh_thread = None
_INDEX_FILE = cfg.DATA_DIR / "local_index.json"


def _read_tags(path: str) -> dict:
    """Прочитать теги (artist, title, album, genre). {} — если нет mutagen/тегов.

    FIX (2026-11-08): .m4a (MP4) — ©ART, ©nam, ©alb, ©gen.
    .mp3 (ID3) — TPE1, TIT2, TALB, TCON. easy=True — универсально.
    """
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
        # FIX (2026-11-08): убрать "@" (Яндекс-метка в теге: "@ilymeedy" → "ilymeedy")
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
    """Скан E:\\Music + mutagen → {version, mtime, tracks: [...]}.

    FIX (2026-11-08, #2): вызывается только при первом запуске (нет JSON)
    или в фоне (_refresh_index_async) при изменении библиотеки.
    """
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
    """Сохранить индекс в data/local_index.json."""
    try:
        _INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_INDEX_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        print(f"⚠ local: save index: {e}")


def _load_index_sync() -> dict:
    """Синхронно: JSON (0.05 сек) или _build_index (8 сек, первый раз).

    НЕ проверяет mtime MUSIC_DIR — обновление в фоне (_refresh_index_async).
    """
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
            print(f"⚠ local: индекс битый: {e} — строю заново")

    # Первый запуск (нет JSON) — строим синхронно (8 сек)
    print("🔨 local: первый запуск — строю индекс (8 сек)...")
    data = _build_index()
    _save_index(data)
    _index_cache = data
    print(f"✅ local: индекс построен ({len(data['tracks'])} треков)")
    return data


def _refresh_index_async():
    """Фон: glob (8 сек) → обновить JSON, если mtime MUSIC_DIR изменился.

    FIX (2026-11-08, #2): вызывается ПОСЛЕ старта музыки (из main.py или
    при первом play_index). Не блокирует. Если библиотека не изменилась — пропуск.
    """
    global _refresh_thread, _index_cache
    if _refresh_thread is not None and _refresh_thread.is_alive():
        return  # уже обновляется

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
                        return  # библиотека не изменилась
                except Exception:
                    pass

            print("🔄 local: обновляю индекс в фоне...")
            data = _build_index()
            _save_index(data)
            with _index_lock:
                _index_cache = data
            print(f"✅ local: индекс обновлён ({len(data['tracks'])} треков)")
        except Exception as e:
            print(f"⚠ local: refresh index: {e}")

    _refresh_thread = threading.Thread(target=runner, daemon=True)
    _refresh_thread.start()


def _load_index(force_refresh=False) -> dict:
    """Публичная: синхронно (JSON) + асинхронное обновление в фоне."""
    data = _load_index_sync()
    if not force_refresh:
        _refresh_index_async()  # фон (8 сек, не блокирует)
    return data


class LocalTrack:
    def __init__(self, path):
        self.path = path
        self.id = hash(path)

        # FIX (2026-11-08): сначала теги (mutagen), потом имя файла.
        tags = _read_tags(path)
        if tags.get("artist") and tags.get("title"):
            self._artist = tags["artist"]
            self.title = tags["title"]
        elif tags.get("title"):
            # artist нет, title есть — fallback "Локальная"
            self._artist = "Локальная"
            self.title = tags["title"]
        else:
            # Тегов нет — имя файла (как раньше)
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


def _local_played_key(path):
    name = os.path.splitext(os.path.basename(path))[0]
    return normalize(name)


def _is_local_blacklisted(path) -> bool:
    """Проверить blacklist для локального файла по названию трека."""
    try:
        from memory import memory
        name = _local_played_key(path)
        row = memory.storage.query_one(
            "SELECT blacklisted FROM track_stats WHERE LOWER(title) = ?",
            (name.lower(),)
        )
        return bool(row and row.get('blacklisted'))
    except Exception:
        return False


def get_local_random_track():
    """FIX (2026-11-08, #2): из индекса (0.01 сек), не glob."""
    idx = _load_index()
    tracks = idx.get("tracks") or []
    if not tracks:
        return None

    with state.session_played_lock:
        played = set(state.session_played_ids)

    candidates = [t for t in tracks if _local_played_key(t["path"]) not in played]
    if not candidates:
        with state.session_played_lock:
            state.session_played_ids.clear()
        candidates = tracks

    candidates = [t for t in candidates if not _is_local_blacklisted(t["path"])]
    if not candidates:
        return None

    t = random.choice(candidates)
    with state.session_played_lock:
        state.session_played_ids.add(_local_played_key(t["path"]))
    return t["path"]


def _read_genre(file_path):
    """Прочитать жанр: сначала .genre файл (кэш), потом теги (mutagen)."""
    genre_file = os.path.splitext(file_path)[0] + ".genre"
    if os.path.exists(genre_file):
        try:
            with open(genre_file, "r", encoding="utf-8") as f:
                return f.read().strip().lower()
        except Exception:
            pass
    # FIX (2026-11-08): fallback на теги
    tags = _read_tags(file_path)
    genre = tags.get("genre")
    if genre:
        return genre.lower().strip()
    return None


def get_local_by_genre(genre_query):
    """FIX (2026-11-08, #2): из индекса (0.01 сек), не glob."""
    idx = _load_index()
    q = normalize(genre_query)
    tags = GENRE_TAGS_MAP.get(q, [q])

    with state.session_played_lock:
        played = set(state.session_played_ids)

    candidates = []
    for t in idx.get("tracks", []):
        if t.get("genre") not in tags:
            continue
        if _local_played_key(t["path"]) in played:
            continue
        if _is_local_blacklisted(t["path"]):
            continue
        candidates.append(t)

    if not candidates:
        return None

    t = random.choice(candidates)
    with state.session_played_lock:
        state.session_played_ids.add(_local_played_key(t["path"]))
    print(f"🎸 Локальный по жанру «{genre_query}»: {os.path.basename(t['path'])}")
    return t["path"]