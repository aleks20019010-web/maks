# memory/aggregates.py
# Пересчёт агрегатов и весов.
# Формула веса — простая, легко тюнится.

import datetime
from datetime import datetime as dt


# ============================================================
# ВЕСА — настраиваемые коэффициенты
# ============================================================
W_FINISH = 3.0            # дослушал = сильный плюс
W_PLAY = 1.0              # играл = слабый плюс
W_SKIP = 2.0              # пропустил = минус
W_RECENT_SKIP = 5.0       # свежие пропуски весят больше
RECENT_SKIP_HOURS = 24    # окно для "свежих" пропусков
W_DAYS_SINCE = 0.1        # давность повышает шанс
DAYS_CAP = 60             # ограничение давности (дней)
W_FAVORITE_BONUS = 2.0    # бонус за высокий finish_rate
W_HATED_BONUS = -3.0      # штраф за высокий skip_rate
MIN_PLAYS_FOR_RATIO = 3   # минимум прослушиваний для оценки предпочтения
FAVORITE_RATIO = 0.7      # finish/play > 0.7 → любимый
HATED_RATIO = 0.7         # skip/play > 0.7 → нелюбимый


class Aggregates:
    def __init__(self, storage):
        self.storage = storage

    # ========================================================
    # ПЕРЕСЧЁТ ВЕСОВ
    # ========================================================
    def recalc_artist_weights(self):
        """Пересчитать вес для всех артистов."""
        rows = self.storage.query("SELECT * FROM artist_stats")
        now = dt.now()

        for row in rows:
            artist = row['artist']
            finish = row['finish_count'] or 0
            play = row['play_count'] or 0
            skip = row['skip_count'] or 0

            weight = (
                finish * W_FINISH
                + play * W_PLAY
                - skip * W_SKIP
            )

            # Свежие пропуски — отдельно
            recent_skips = self._recent_skips_for_artist(artist, hours=RECENT_SKIP_HOURS)
            weight -= recent_skips * W_RECENT_SKIP

            # Давность
            last_played = row.get('last_played')
            if last_played:
                try:
                    last_dt = dt.fromisoformat(str(last_played).replace(' ', 'T'))
                    days = min((now - last_dt).days, DAYS_CAP)
                    weight += days * W_DAYS_SINCE
                except Exception:
                    pass

            # Соотношения
            if play >= MIN_PLAYS_FOR_RATIO:
                if finish / play > FAVORITE_RATIO:
                    weight += W_FAVORITE_BONUS
                if skip / play > HATED_RATIO:
                    weight += W_HATED_BONUS

            self.storage.execute("""
                UPDATE artist_stats SET weight = ? WHERE artist = ?
            """, (round(weight, 2), artist))

        return len(rows)

    def recalc_track_weights(self):
        """Пересчитать вес для всех треков."""
        rows = self.storage.query("SELECT * FROM track_stats")
        now = dt.now()

        for row in rows:
            track_id = row['track_id']
            finish = row['finish_count'] or 0
            play = row['play_count'] or 0
            skip = row['skip_count'] or 0

            weight = (
                finish * W_FINISH
                + play * W_PLAY
                - skip * W_SKIP
            )

            recent_skips = self._recent_skips_for_track(track_id, hours=RECENT_SKIP_HOURS)
            weight -= recent_skips * W_RECENT_SKIP

            last_played = row.get('last_played')
            if last_played:
                try:
                    last_dt = dt.fromisoformat(str(last_played).replace(' ', 'T'))
                    days = min((now - last_dt).days, DAYS_CAP)
                    weight += days * W_DAYS_SINCE
                except Exception:
                    pass

            if play >= MIN_PLAYS_FOR_RATIO:
                if finish / play > FAVORITE_RATIO:
                    weight += W_FAVORITE_BONUS
                if skip / play > HATED_RATIO:
                    weight += W_HATED_BONUS

            self.storage.execute("""
                UPDATE track_stats SET weight = ? WHERE track_id = ?
            """, (round(weight, 2), track_id))

        return len(rows)

    def recalc_all(self):
        """Пересчитать все веса."""
        n_artists = self.recalc_artist_weights()
        n_tracks = self.recalc_track_weights()
        return {'artists': n_artists, 'tracks': n_tracks}

    # ========================================================
    # ВСПОМОГАТЕЛЬНЫЕ
    # ========================================================
    def _recent_skips_for_artist(self, artist, hours=24):
        row = self.storage.query_one("""
            SELECT COUNT(*) AS c FROM events
            WHERE event_type = 'skip'
              AND timestamp >= datetime('now', ?)
              AND track_id IN (SELECT track_id FROM track_stats WHERE artist = ?)
        """, (f'-{hours} hours', artist))
        return row['c'] if row else 0

    def _recent_skips_for_track(self, track_id, hours=24):
        row = self.storage.query_one("""
            SELECT COUNT(*) AS c FROM events
            WHERE event_type = 'skip'
              AND track_id = ?
              AND timestamp >= datetime('now', ?)
        """, (str(track_id), f'-{hours} hours'))
        return row['c'] if row else 0

    # ========================================================
    # ТОПЫ
    # ========================================================
    def top_artists(self, limit=10, days=None):
        if days:
            return self.storage.query("""
                SELECT artist, SUM(play_count) AS plays, SUM(finish_count) AS finishes,
                       SUM(skip_count) AS skips
                FROM artist_stats
                WHERE last_played >= datetime('now', ?)
                ORDER BY (finishes * 3 + plays - skips * 2) DESC
                LIMIT ?
            """, (f'-{days} days', limit))
        return self.storage.query("""
            SELECT * FROM artist_stats
            ORDER BY weight DESC LIMIT ?
        """, (limit,))

    def top_tracks(self, limit=20, days=None):
        if days:
            return self.storage.query("""
                SELECT track_id, artist, title, play_count, finish_count, skip_count
                FROM track_stats
                WHERE last_played >= datetime('now', ?)
                ORDER BY (finish_count * 3 + play_count - skip_count * 2) DESC
                LIMIT ?
            """, (f'-{days} days', limit))
        return self.storage.query("""
            SELECT * FROM track_stats
            ORDER BY weight DESC LIMIT ?
        """, (limit,))

    def stats_summary(self):
        """Общая сводка."""
        return {
            'events': self.storage.query_one("SELECT COUNT(*) AS c FROM events")['c'],
            'artists': self.storage.query_one("SELECT COUNT(*) AS c FROM artist_stats")['c'],
            'tracks': self.storage.query_one("SELECT COUNT(*) AS c FROM track_stats")['c'],
            'searches': self.storage.query_one("SELECT COUNT(*) AS c FROM search_history")['c'],
        }