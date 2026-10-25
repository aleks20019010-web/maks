# memory/reader.py
# Чтение событий из SQLite с любым горизонтом.
# Храним ВСЁ, срезы — на этапе чтения.
#
# API:
#   plays_since(seconds_ago)         — что играло за N секунд
#   plays_between(start_ts, end_ts)  — что играло в диапазоне
#   plays_on_date(dt)                — что играло в конкретную дату
#   plays_by_hour(hour)              — что играло в этот час (за всё время)
#   plays_by_weekday(weekday)        — что играло в этот день недели
#   plays_for_artist(artist, since)  — треки артиста
#   last_played(key)                 — когда последний раз
#   days_since(key)                  — сколько дней назад
#   streak_days(artist)              — сколько дней подряд слушали
#   diversity_since(seconds)         — сколько уникальных артистов
#   top_since(seconds, limit)        — топ-N за период
#
# Все методы возвращают простые типы: list, dict, int, float.

import time
from datetime import datetime, timedelta


class Reader:
    def __init__(self, storage):
        self.storage = storage

    # ========================================================
    # БАЗОВЫЕ ХЕЛПЕРЫ
    # ========================================================
    def _since_cutoff(self, seconds_ago):
        """Timestamp 'N секунд назад' в формате SQLite."""
        dt = datetime.now() - timedelta(seconds=seconds_ago)
        return dt.strftime('%Y-%m-%d %H:%M:%S')

    def _dt_to_sql(self, dt):
        if isinstance(dt, str):
            return dt
        return dt.strftime('%Y-%m-%d %H:%M:%S')

    # ========================================================
    # ПЛЕЙ-СОБЫТИЯ ПО ПЕРИОДУ
    # ========================================================
    def plays_since(self, seconds_ago, event_type='play_start'):
        """Что играло за последние N секунд.

        Возвращает: {"КиШ": 3, "Ария": 2, ...}
        """
        cutoff = self._since_cutoff(seconds_ago)
        rows = self.storage.query("""
            SELECT artist, COUNT(*) AS cnt
            FROM events
            WHERE event_type = ? AND timestamp >= ? AND artist IS NOT NULL
            GROUP BY artist
            ORDER BY cnt DESC
        """, (event_type, cutoff))
        return {r['artist']: r['cnt'] for r in rows}

    def plays_between(self, start_dt, end_dt, event_type='play_start'):
        """Что играло в диапазоне. Возвращает список событий."""
        s = self._dt_to_sql(start_dt)
        e = self._dt_to_sql(end_dt)
        return self.storage.query("""
            SELECT timestamp, artist, track_title, track_id, source
            FROM events
            WHERE event_type = ? AND timestamp >= ? AND timestamp <= ?
            ORDER BY timestamp
        """, (event_type, s, e))

    def plays_on_date(self, dt, event_type='play_start'):
        """Что играло в конкретную дату (день).

        dt: date или datetime или 'YYYY-MM-DD'
        """
        if isinstance(dt, str):
            date_str = dt[:10]
        else:
            date_str = dt.strftime('%Y-%m-%d')
        return self.storage.query("""
            SELECT timestamp, artist, track_title, track_id, source
            FROM events
            WHERE event_type = ? AND DATE(timestamp) = ?
            ORDER BY timestamp
        """, (event_type, date_str))

    def plays_by_hour(self, hour, event_type='play_start'):
        """Что играло в этот час (за всё время)."""
        return self.storage.query("""
            SELECT artist, track_title, COUNT(*) AS cnt
            FROM events
            WHERE event_type = ?
              AND CAST(strftime('%H', timestamp) AS INTEGER) = ?
              AND artist IS NOT NULL
            GROUP BY artist, track_title
            ORDER BY cnt DESC
        """, (event_type, int(hour)))

    def plays_by_weekday(self, weekday, event_type='play_start'):
        """Что играло в этот день недели (0=Пн, 6=Вс)."""
        # strftime('%w') → 0=Вс, 6=Сб. Приводим к 0=Пн.
        return self.storage.query("""
            SELECT artist, track_title, COUNT(*) AS cnt
            FROM events
            WHERE event_type = ?
              AND (CAST(strftime('%w', timestamp) AS INTEGER) + 6) % 7 = ?
              AND artist IS NOT NULL
            GROUP BY artist, track_title
            ORDER BY cnt DESC
        """, (event_type, int(weekday)))

    def plays_today(self, artist=None, event_type='play_start', start_hour=8):
        """Сколько раз играло с последнего START_HOUR (по умолчанию с 8:00).

        Если сейчас >= start_hour -> начало = сегодня start_hour
        Если сейчас < start_hour -> начало = вчера start_hour

        Возвращает dict {artist: count} или int если artist указан.
        """
        now = datetime.now()
        if now.hour >= start_hour:
            start_dt = now.replace(hour=start_hour, minute=0, second=0, microsecond=0)
        else:
            start_dt = (now - timedelta(days=1)).replace(
                hour=start_hour, minute=0, second=0, microsecond=0
            )
        start_str = start_dt.strftime('%Y-%m-%d %H:%M:%S')

        if artist:
            row = self.storage.query_one("""
                SELECT COUNT(*) AS c FROM events
                WHERE event_type = ? AND artist = ? AND timestamp >= ?
            """, (event_type, artist, start_str))
            return row['c'] if row else 0

        rows = self.storage.query("""
            SELECT artist, COUNT(*) AS cnt FROM events
            WHERE event_type = ? AND timestamp >= ? AND artist IS NOT NULL
            GROUP BY artist
            ORDER BY cnt DESC
        """, (event_type, start_str))
        return {r['artist']: r['cnt'] for r in rows}

    def count_plays_in_range(self, start_dt, end_dt, event_type='play_start'):
        """Сколько треков играло в диапазоне."""
        s = self._dt_to_sql(start_dt)
        e = self._dt_to_sql(end_dt)
        row = self.storage.query_one("""
            SELECT COUNT(*) AS c FROM events
            WHERE event_type = ? AND timestamp >= ? AND timestamp <= ?
        """, (event_type, s, e))
        return row['c'] if row else 0

    # ========================================================
    # ПО АРТИСТУ / ТРЕКУ
    # ========================================================
    def plays_for_artist(self, artist, since_ts=None, limit=200):
        """Треки артиста. Если since_ts — только после него."""
        if since_ts:
            return self.storage.query("""
                SELECT timestamp, artist, track_title, track_id
                FROM events
                WHERE event_type = 'play_start'
                  AND artist = ? AND timestamp >= ?
                ORDER BY timestamp DESC
                LIMIT ?
            """, (artist, since_ts, limit))
        return self.storage.query("""
            SELECT timestamp, artist, track_title, track_id
            FROM events
            WHERE event_type = 'play_start' AND artist = ?
            ORDER BY timestamp DESC
            LIMIT ?
        """, (artist, limit))

    def last_played(self, key, by='artist'):
        """Timestamp последнего play_start.

        by: 'artist' или 'track_id'
        """
        col = 'artist' if by == 'artist' else 'track_id'
        row = self.storage.query_one(f"""
            SELECT timestamp FROM events
            WHERE event_type = 'play_start' AND {col} = ?
            ORDER BY timestamp DESC LIMIT 1
        """, (key,))
        if not row:
            return None
        try:
            dt = datetime.strptime(row['timestamp'], '%Y-%m-%d %H:%M:%S')
            return dt.timestamp()
        except Exception:
            return None

    def days_since(self, key, by='artist'):
        """Сколько дней с последнего play_start. None если не играл."""
        ts = self.last_played(key, by=by)
        if ts is None:
            return None
        return int((time.time() - ts) / 86400)

    def streak_days(self, artist):
        """Сколько дней подряд артист играл (считая сегодня).

        0 — не играл сегодня
        1 — играл только сегодня
        N — N дней подряд
        """
        rows = self.storage.query("""
            SELECT DISTINCT DATE(timestamp) AS d
            FROM events
            WHERE event_type = 'play_start' AND artist = ?
            ORDER BY d DESC
        """, (artist,))
        if not rows:
            return 0

        dates = []
        for r in rows:
            try:
                dates.append(datetime.strptime(r['d'], '%Y-%m-%d').date())
            except Exception:
                continue

        if not dates:
            return 0

        today = datetime.now().date()
        if dates[0] != today:
            return 0

        streak = 1
        for i in range(1, len(dates)):
            if dates[i - 1] - dates[i] == timedelta(days=1):
                streak += 1
            else:
                break
        return streak

    # ========================================================
    # АНАЛИТИКА
    # ========================================================
    def diversity_since(self, seconds_ago, event_type='play_start'):
        """Сколько уникальных артистов за период."""
        cutoff = self._since_cutoff(seconds_ago)
        row = self.storage.query_one("""
            SELECT COUNT(DISTINCT artist) AS c
            FROM events
            WHERE event_type = ? AND timestamp >= ? AND artist IS NOT NULL
        """, (event_type, cutoff))
        return row['c'] if row else 0

    def top_since(self, seconds_ago, limit=10, event_type='play_start'):
        """Топ-N артистов за период."""
        cutoff = self._since_cutoff(seconds_ago)
        return self.storage.query("""
            SELECT artist, COUNT(*) AS cnt
            FROM events
            WHERE event_type = ? AND timestamp >= ? AND artist IS NOT NULL
            GROUP BY artist
            ORDER BY cnt DESC
            LIMIT ?
        """, (event_type, cutoff, limit))

    def top_tracks_since(self, seconds_ago, limit=10, event_type='play_start'):
        """Топ-N треков за период."""
        cutoff = self._since_cutoff(seconds_ago)
        return self.storage.query("""
            SELECT track_title, artist, COUNT(*) AS cnt
            FROM events
            WHERE event_type = ? AND timestamp >= ? AND track_title IS NOT NULL
            GROUP BY track_title, artist
            ORDER BY cnt DESC
            LIMIT ?
        """, (event_type, cutoff, limit))

    # ========================================================
    # СКИПЫ / ДОСЛУШАЛ
    # ========================================================
    def skips_since(self, seconds_ago):
        """Сколько скипов за период."""
        cutoff = self._since_cutoff(seconds_ago)
        row = self.storage.query_one("""
            SELECT COUNT(*) AS c FROM events
            WHERE event_type = 'skip' AND timestamp >= ?
        """, (cutoff,))
        return row['c'] if row else 0

    def finishes_since(self, seconds_ago):
        """Сколько дослушано до конца за период."""
        cutoff = self._since_cutoff(seconds_ago)
        row = self.storage.query_one("""
            SELECT COUNT(*) AS c FROM events
            WHERE event_type = 'finish' AND timestamp >= ?
        """, (cutoff,))
        return row['c'] if row else 0

    # ========================================================
    # СВОДКА
    # ========================================================
    def summary(self):
        """Быстрая сводка: сколько чего в базе."""
        ev = self.storage.query_one("SELECT COUNT(*) AS c FROM events")
        ar = self.storage.query_one("SELECT COUNT(*) AS c FROM artist_stats")
        tr = self.storage.query_one("SELECT COUNT(*) AS c FROM track_stats")
        return {
            'events': ev['c'] if ev else 0,
            'artists': ar['c'] if ar else 0,
            'tracks': tr['c'] if tr else 0,
        }