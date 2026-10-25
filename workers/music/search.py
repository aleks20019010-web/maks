# workers/music/search.py
# Поиск артистов, треков, классификация запроса.

from workers.music import state


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