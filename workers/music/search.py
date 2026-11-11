# workers/music/search.py
# Поиск артистов, треков, классификация запроса.
# LOCAL (v6.5): search_local(query) — точный → phon → fuzzy по
#   data/local_index.json. Возвращает dict или None.

import json
from pathlib import Path

from rapidfuzz import process, fuzz

from shared import config as cfg
from workers.music import state
from workers.music.phon import phon


_LOCAL_INDEX = None


def _load_local_index():
    """Загрузить local_index.json один раз, добавить _hay (phon artist+title)."""
    global _LOCAL_INDEX
    if _LOCAL_INDEX is None:
        path = Path(cfg.DATA_DIR) / "local_index.json"
        if path.exists():
            try:
                with open(path, encoding="utf-8") as f:
                    _LOCAL_INDEX = json.load(f)["tracks"]
            except Exception as e:
                print(f"   local_index load error: {e}")
                _LOCAL_INDEX = []
            for t in _LOCAL_INDEX:
                t["_hay"] = (
                    f"{t.get('phon_artist', '')} "
                    f"{t.get('phon_title', '')}"
                ).strip()
        else:
            _LOCAL_INDEX = []
    return _LOCAL_INDEX


def search_local(query: str):
    """Ищет локальный трек: точный → phon → fuzzy. Возвращает dict или None."""
    index = _load_local_index()
    if not query or not index:
        return None

    words = query.lower().split()
    phon_words = [phon(w) for w in words]

    # 1. Точное (все слова в artist+title)
    for t in index:
        hay = f"{t.get('artist', '')} {t.get('title', '')}".lower()
        if all(w in hay for w in words):
            return t

    # 2. Phonetic
    for t in index:
        hay = f"{t.get('phon_artist', '')} {t.get('phon_title', '')}"
        if all(pw in hay for pw in phon_words):
            return t

    # 3. Fuzzy
    target = " ".join(phon_words)
    best = process.extractOne(
        target,
        [t["_hay"] for t in index],
        scorer=fuzz.token_set_ratio,
    )
    if best and best[1] >= 75:
        return index[best[2]]
    return None


def search_artist(query):
    """Нечёткий поиск артиста: падежи + доверие Яндексу."""
    if state.client is None:
        return None

    variants = [query]
    q = query.strip()
    if q.endswith('у') or q.endswith('ю'):
        variants += [q[:-1], q[:-1] + 'я']
    if q.endswith('ой') or q.endswith('ей'):
        variants += [q[:-2] + 'а']
    if q.endswith('а'):
        variants.append(q[:-1])
    if q.endswith('и'):
        variants.append(q[:-1] + 'я')
    seen = set()
    for v in variants:
        if v in seen:
            continue
        seen.add(v)
        try:
            result = state.client.search(v)
            if result.artists and result.artists.results:
                artist = result.artists.results[0]
                print(f"   search_artist(«{v}») → {artist.name}")
                return artist
        except Exception:
            continue
    return None


def search_track(query):
    if state.client is None:
        return None
    print(f"   search_track(«{query}»)")
    try:
        result = state.client.search(query)
        if result.tracks and result.tracks.results:
            track = result.tracks.results[0]
            artists = ', '.join(a.name for a in track.artists)
            print(f"   → трек: {track.title} — {artists}")
            return track
    except Exception as e:
        print(f"   ошибка поиска трека: {e}")
    return None


def classify_query(query):
    words = query.split()
    for i in range(len(words), 0, -1):
        artist_part = ' '.join(words[:i])
        rest = ' '.join(words[i:]).strip()
        artist = search_artist(artist_part)
        if artist:
            return artist, artist_part, (rest if rest else None)
    return None, None, None


def find_artist_track(artist, track_query):
    if state.client is None:
        return None
    query = f"{artist.name} {track_query}"
    try:
        result = state.client.search(query)
        if result.tracks and result.tracks.results:
            for t in result.tracks.results:
                for a in t.artists:
                    if a.id == artist.id:
                        return t
    except Exception as e:
        print(f"   Ошибка find_artist_track: {e}")
    return None