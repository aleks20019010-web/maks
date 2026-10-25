# memory/preferences.py
# Модель предпочтений — высокоуровневые запросы к памяти.
#
# v2 (2026-10-10):
#   + Обёртки над reader для Context (plays_last_*, skips_*, ...)
#   + Долгосрочные тренды (artist_streak_days, genre_diversity_days, ...)
#   + Blacklist (is_blacklisted / blacklist / unblacklist)
#   + last_request, played_on_date


class Preferences:
    def __init__(self, storage):
        self.storage = storage
        # Ленивая ссылка на reader (устанавливается в engine)
        self._reader = None

    def set_reader(self, reader):
        """Подключить reader (вызывается из MemoryEngine)."""
        self._reader = reader

    # ========================================================
    # ВЕСА
    # ========================================================
    def get_track_weight(self, track_id):
        row = self.storage.query_one(
            "SELECT weight FROM track_stats WHERE track_id = ?",
            (str(track_id),)
        )
        return row['weight'] if row else 0.0

    def get_artist_weight(self, artist):
        row = self.storage.query_one(
            "SELECT weight FROM artist_stats WHERE artist = ?",
            (artist,)
        )
        return row['weight'] if row else 0.0

    # ========================================================
    # ФИЛЬТРЫ
    # ========================================================
    def is_hated_track(self, track_id, threshold=-5.0):
        return self.get_track_weight(track_id) < threshold

    def is_favorite_track(self, track_id, threshold=5.0):
        return self.get_track_weight(track_id) > threshold

    def get_hated_track_ids(self, threshold=-5.0):
        rows = self.storage.query("""
            SELECT track_id FROM track_stats WHERE weight < ?
        """, (threshold,))
        return {r['track_id'] for r in rows}

    def get_favorite_artist_names(self, threshold=5.0, limit=20):
        rows = self.storage.query("""
            SELECT artist FROM artist_stats
            WHERE weight > ?
            ORDER BY weight DESC
            LIMIT ?
        """, (threshold, limit))
        return [r['artist'] for r in rows]

    # ========================================================
    # ЛОКАЛЬНЫЕ ФАЙЛЫ
    # ========================================================
    def get_local_weight_by_path(self, path, normalize_func):
        """Для локального файла — вес вычисляется по названию трека."""
        import os
        name = os.path.splitext(os.path.basename(path))[0]
        name = normalize_func(name)
        row = self.storage.query_one("""
            SELECT weight FROM track_stats
            WHERE LOWER(title) = ?
            ORDER BY weight DESC LIMIT 1
        """, (name.lower(),))
        if row:
            return row['weight']
        return 0.0

    # ========================================================
    # "ДАВНО НЕ СЛУШАЛ"
    # ========================================================
    def get_long_unplayed_tracks(self, days=30, limit=50):
        return self.storage.query("""
            SELECT track_id, artist, title, last_played, weight
            FROM track_stats
            WHERE last_played IS NULL
               OR last_played < datetime('now', ?)
            ORDER BY weight DESC
            LIMIT ?
        """, (f'-{days} days', limit))

    # ========================================================
    # ОБЁРТКИ НАД READER — для Context
    # ========================================================
    def plays_last_10min(self):
        if not self._reader: return {}
        return self._reader.plays_since(600)

    def plays_last_hour(self):
        if not self._reader: return {}
        return self._reader.plays_since(3600)

    def plays_last_day(self):
        if not self._reader: return {}
        return self._reader.plays_since(86400)

    def plays_last_week(self):
        if not self._reader: return {}
        return self._reader.plays_since(604800)

    def skips_last_hour(self):
        if not self._reader: return 0
        return self._reader.skips_since(3600)

    def finishes_last_hour(self):
        if not self._reader: return 0
        return self._reader.finishes_since(3600)

    def diversity_last_day(self):
        if not self._reader: return 0
        return self._reader.diversity_since(86400)

    def top_artist_last_hour(self):
        if not self._reader: return None
        top = self._reader.top_since(3600, limit=1)
        return top[0]['artist'] if top else None

    def top_artist_last_day(self):
        if not self._reader: return None
        top = self._reader.top_since(86400, limit=1)
        return top[0]['artist'] if top else None

    # ========================================================
    # ДОЛГОСРОЧНЫЕ ТРЕНДЫ
    # ========================================================
    def plays_artist_today(self, artist):
        """Сколько раз артист играл с начала дня (с 8:00)."""
        if not self._reader:
            return 0
        return self._reader.plays_today(artist=artist)

    def plays_today(self):
        """Сводка с начала дня: {artist: count}."""
        if not self._reader:
            return {}
        return self._reader.plays_today()

    def artist_streak_days(self, artist):
        """Сколько дней подряд артист играл."""
        if not self._reader: return 0
        return self._reader.streak_days(artist)

    def days_since_artist(self, artist):
        """Сколько дней с последнего play_start артиста."""
        if not self._reader: return None
        return self._reader.days_since(artist, by='artist')

    def days_since_track(self, track_id):
        """Сколько дней с последнего play_start трека."""
        if not self._reader: return None
        return self._reader.days_since(str(track_id), by='track_id')

    def genre_diversity_days(self, days=7):
        """Сколько уникальных артистов за N дней."""
        if not self._reader: return 0
        return self._reader.diversity_since(days * 86400)

    def top_artist_by_hour(self, hour):
        """Топ-1 артист в этот час (за всё время)."""
        if not self._reader: return None
        rows = self._reader.plays_by_hour(hour)
        return rows[0]['artist'] if rows else None

    def top_artist_by_weekday(self, weekday):
        """Топ-1 артист в этот день недели (0=Пн)."""
        if not self._reader: return None
        rows = self._reader.plays_by_weekday(weekday)
        return rows[0]['artist'] if rows else None

    def played_on_date(self, artist, dt):
        """True, если артист играл в эту дату."""
        if not self._reader: return False
        events = self._reader.plays_on_date(dt)
        return any(e['artist'] == artist for e in events)

    # ========================================================
    # КОМАНДЫ (из events)
    # ========================================================
    def commands_last_2min(self):
        """Сколько команд за последние 2 минуты.

        Считаем play_request + repeat_command + search.
        """
        rows = self.storage.query("""
            SELECT COUNT(*) AS c FROM events
            WHERE event_type IN ('play_request', 'repeat_command', 'search')
              AND timestamp >= datetime('now', '-120 seconds')
        """)
        return rows[0]['c'] if rows else 0

    def last_request(self):
        """Последний play_request (query, ts)."""
        row = self.storage.query_one("""
            SELECT query, timestamp FROM events
            WHERE event_type = 'play_request'
            ORDER BY timestamp DESC LIMIT 1
        """)
        return dict(row) if row else None

    def last_request_time_ago(self):
        """Сколько секунд с последнего play_request."""
        import time
        from datetime import datetime
        row = self.storage.query_one("""
            SELECT timestamp FROM events
            WHERE event_type = 'play_request'
            ORDER BY timestamp DESC LIMIT 1
        """)
        if not row or not row['timestamp']:
            return None
        try:
            dt = datetime.strptime(row['timestamp'], '%Y-%m-%d %H:%M:%S')
            return time.time() - dt.timestamp()
        except Exception:
            return None

    # ========================================================
    # BLACKLIST (заготовка, поле в track_stats)
    # ========================================================
    def is_blacklisted(self, track_id):
        """Проверяет флаг blacklisted в track_stats."""
        row = self.storage.query_one(
            "SELECT blacklisted FROM track_stats WHERE track_id = ?",
            (str(track_id),)
        )
        return bool(row and row.get('blacklisted'))

    def blacklist(self, track_id, reason='3_skips'):
        """Помечает трек как blacklisted.

        Применяется фильтром автовыбора, но не блокирует явную просьбу.
        """
        self.storage.execute("""
            UPDATE track_stats SET blacklisted = 1 WHERE track_id = ?
        """, (str(track_id),))
        # Логируем в events
        self.storage.execute("""
            INSERT INTO events (event_type, track_id, extra_json)
            VALUES ('blacklist_add', ?, ?)
        """, (str(track_id), '{"reason": "' + reason + '"}'))

    def unblacklist(self, track_id, reason='listened'):
        """Снимает флаг blacklisted (например, после явного дослушивания)."""
        self.storage.execute("""
            UPDATE track_stats SET blacklisted = 0 WHERE track_id = ?
        """, (str(track_id),))
        self.storage.execute("""
            INSERT INTO events (event_type, track_id, extra_json)
            VALUES ('blacklist_remove', ?, ?)
        """, (str(track_id), '{"reason": "' + reason + '"}'))

    # ========================================================
    # СВОДКА
    # ========================================================
    def get_summary(self):
        row_artists = self.storage.query("""
            SELECT COUNT(*) AS c FROM artist_stats WHERE weight > 5
        """)[0]
        row_tracks = self.storage.query("""
            SELECT COUNT(*) AS c FROM track_stats WHERE weight > 5
        """)[0]
        row_hated = self.storage.query("""
            SELECT COUNT(*) AS c FROM track_stats WHERE weight < -5
        """)[0]

        top_artist = self.storage.query_one("""
            SELECT artist, weight FROM artist_stats
            ORDER BY weight DESC LIMIT 1
        """)
        top_track = self.storage.query_one("""
            SELECT title, artist, weight FROM track_stats
            ORDER BY weight DESC LIMIT 1
        """)

        return {
            'favorite_artists': row_artists['c'],
            'favorite_tracks': row_tracks['c'],
            'hated_tracks': row_hated['c'],
            'top_artist': dict(top_artist) if top_artist else None,
            'top_track': dict(top_track) if top_track else None,
        }