# workers/music/similar.py
# Поиск похожих артистов в фоне.

from shared import config as cfg
from workers.music import state


def fetch_similar_background(artist_id):
    if state.client is None:
        return
    try:
        similar = state.client.artists_similar(artist_id)
        if similar and hasattr(similar, 'similar_artists'):
            state.radio_similar_list = [
                sa for sa in similar.similar_artists
                if sa.id != artist_id
            ][:cfg.MAX_SIMILAR_ARTISTS]
    except Exception:
        pass