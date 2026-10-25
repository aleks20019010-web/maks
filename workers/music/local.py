# workers/music/local.py
# Локальные треки: класс LocalTrack, поиск случайного трека, поиск по жанру.
# Blacklist: локальные треки проверяются по названию (track_stats.title).

import os
import random

from shared.audio_cache import all_audio_files, normalize
from workers.music import state
from workers.music._shared import GENRE_TAGS_MAP, _clean_filename


class LocalTrack:
    def __init__(self, path):
        self.path = path
        self.id = hash(path)
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
    files = all_audio_files()
    if not files:
        return None

    with state.session_played_lock:
        played = set(state.session_played_ids)

    candidates = [f for f in files if _local_played_key(f) not in played]
    if not candidates:
        with state.session_played_lock:
            state.session_played_ids.clear()
        candidates = files

    # Пропускаем blacklisted
    candidates = [f for f in candidates if not _is_local_blacklisted(f)]
    if not candidates:
        return None

    path = random.choice(candidates)
    with state.session_played_lock:
        state.session_played_ids.add(_local_played_key(path))
    return path


def _read_genre(file_path):
    genre_file = os.path.splitext(file_path)[0] + ".genre"
    if not os.path.exists(genre_file):
        return None
    try:
        with open(genre_file, "r", encoding="utf-8") as f:
            return f.read().strip().lower()
    except Exception:
        return None


def get_local_by_genre(genre_query):
    q = normalize(genre_query)
    tags = GENRE_TAGS_MAP.get(q)
    if not tags:
        tags = [q]

    files = all_audio_files()
    if not files:
        return None

    with state.session_played_lock:
        played = set(state.session_played_ids)

    candidates = []
    for file_path in files:
        tag = _read_genre(file_path)
        if not tag:
            continue
        if tag in tags:
            key = _local_played_key(file_path)
            if key not in played and not _is_local_blacklisted(file_path):
                candidates.append(file_path)

    if not candidates:
        return None

    path = random.choice(candidates)
    with state.session_played_lock:
        state.session_played_ids.add(_local_played_key(path))
    print(f"🎸 Локальный по жанру «{genre_query}»: {os.path.basename(path)}")
    return path