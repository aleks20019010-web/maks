# memory/engine.py
# Главный класс памяти. remember_* мгновенно кладут событие в очередь,
# фоновый writer пишет в SQLite.
#
# v2 (2026-10-10):
#   + reader — чтение с горизонтом (plays_since, days_since, streak_days, ...)
#   + remember_repeat / idle / wake / mode_change / volume_change / silence
#   + context во всех remember_* (опциональный)
#   + start() — для совместимости (no-op, writer стартует в __init__)
#
# v3 (2026-10-20):
#   + is_blacklisted / blacklist_track / unblacklist_track
#   + plays_artist_today — сколько раз артист играл сегодня
#
# v4 (2026-10-22):
#   + I: penalize_artist / reward_artist — точечное изменение веса
#   + I: get_habit / set_habit — привычки (time_bucket → artist)
#   + I: get_pattern / set_pattern — паттерны (condition → artist)
#   + путь к БД из cfg.MEMORY_DB (D:\Maks\data\memory.db), а не memory/data/

import os
import json

from shared import config as cfg

from memory.storage import Storage
from memory.events import Events
from memory.aggregates import Aggregates
from memory.preferences import Preferences
from memory.reader import Reader
from memory.writer import Writer


class MemoryEngine:
    def __init__(self, base_dir=None, start_writer=True):
        # Путь к БД — из cfg.MEMORY_DB (D:\Maks\data\memory.db)
        db_path = cfg.MEMORY_DB

        self.storage = Storage(db_path)
        self.events = Events(self.storage)
        self.aggregates = Aggregates(self.storage)
        self.preferences = Preferences(self.storage)
        self.reader = Reader(self.storage)
        self.preferences.set_reader(self.reader)

        # Фоновый writer
        self.writer = Writer(self.events, self.aggregates)
        if start_writer:
            self.writer.start()

    # ========================================================
    # ЗАПИСЬ — МГНОВЕННАЯ (кладёт в очередь)
    # ========================================================
    def remember_request(self, query, context=None):
        self.writer.put('play_request', {'query': query, 'context': context})

    def remember_play(self, track_id, artist, title, source, context=None):
        self.writer.put('play_start', {
            'track_id': track_id,
            'artist': artist,
            'title': title,
            'source': source,
            'context': context,
        })

    def remember_skip(self, track_id, seconds_played=0, context=None):
        self.writer.put('skip', {
            'track_id': track_id,
            'seconds_played': seconds_played,
            'context': context,
        })

    def remember_finish(self, track_id, duration=0, context=None):
        self.writer.put('finish', {
            'track_id': track_id,
            'duration': duration,
            'context': context,
        })

    def remember_search(self, query, found_artist=None, found_track=None,
                        success=False, context=None):
        self.writer.put('search', {
            'query': query,
            'found_artist': found_artist,
            'found_track': found_track,
            'success': success,
            'context': context,
        })

    # --- Новые типы (v2) ---
    def remember_repeat(self, command, count, context=None):
        self.writer.put('repeat_command', {
            'command': command, 'count': count, 'context': context,
        })

    def remember_idle(self, duration_sec, was_playing, context=None):
        self.writer.put('idle_period', {
            'duration_sec': duration_sec, 'was_playing': was_playing,
            'context': context,
        })

    def remember_wake(self, source='voice', context=None):
        self.writer.put('wake_word', {'source': source, 'context': context})

    def remember_mode_change(self, from_mode, to_mode, trigger='', context=None):
        self.writer.put('mode_change', {
            'from_mode': from_mode, 'to_mode': to_mode, 'trigger': trigger,
            'context': context,
        })

    def remember_volume_change(self, old, new, source='', context=None):
        self.writer.put('volume_change', {
            'old': old, 'new': new, 'source': source, 'context': context,
        })

    def remember_silence(self, duration_sec, context=None):
        self.writer.put('silence_command', {
            'duration_sec': duration_sec, 'context': context,
        })

    # ========================================================
    # ЧТЕНИЕ — ПРЕДПОЧТЕНИЯ (Preferences)
    # ========================================================
    def get_track_weight(self, track_id):
        return self.preferences.get_track_weight(track_id)

    def get_artist_weight(self, artist):
        return self.preferences.get_artist_weight(artist)

    def is_hated_track(self, track_id, threshold=-5.0):
        return self.preferences.is_hated_track(track_id, threshold)

    def is_favorite_track(self, track_id, threshold=5.0):
        return self.preferences.is_favorite_track(track_id, threshold)

    def get_hated_track_ids(self, threshold=-5.0):
        return self.preferences.get_hated_track_ids(threshold)

    def get_local_weight_by_path(self, path, normalize_func):
        return self.preferences.get_local_weight_by_path(path, normalize_func)

    def get_summary(self):
        return self.preferences.get_summary()

    def plays_artist_today(self, artist):
        """Сколько раз артист играл сегодня (с 00:00)."""
        try:
            return self.preferences.plays_artist_today(artist)
        except Exception:
            return 0

    # ========================================================
    # I: RELATIONSHIP — ТОЧЕЧНЫЕ ОПЕРАЦИИ
    # ========================================================
    def penalize_artist(self, artist, delta=-5.0):
        """Понизить вес артиста на delta (мгновенно, без recalc)."""
        if not artist:
            return
        try:
            self.storage.execute("""
                UPDATE artist_stats SET weight = COALESCE(weight, 0) + ?
                WHERE artist = ?
            """, (float(delta), artist))
        except Exception as e:
            print(f"⚠ penalize_artist: {e}")

    def reward_artist(self, artist, delta=2.0):
        """Повысить вес артиста на delta."""
        if not artist:
            return
        try:
            self.storage.execute("""
                UPDATE artist_stats SET weight = COALESCE(weight, 0) + ?
                WHERE artist = ?
            """, (float(delta), artist))
        except Exception as e:
            print(f"⚠ reward_artist: {e}")

    def get_habit(self, time_bucket):
        """Привычка для time_bucket ('morning' / 'day' / 'evening' / 'night')."""
        try:
            row = self.storage.query_one("""
                SELECT value_json FROM aggregates_cache WHERE key = ?
            """, (f"habit:{time_bucket}",))
            if row and row.get('value_json'):
                return json.loads(row['value_json'])
        except Exception:
            pass
        return None

    def set_habit(self, time_bucket, artist, count=1):
        """Записать привычку: time_bucket → artist."""
        try:
            existing = self.get_habit(time_bucket) or {}
            existing[artist] = existing.get(artist, 0) + count
            self.storage.execute("""
                INSERT OR REPLACE INTO aggregates_cache(key, value_json, computed_at)
                VALUES (?, ?, datetime('now'))
            """, (f"habit:{time_bucket}", json.dumps(existing, ensure_ascii=False)))
        except Exception as e:
            print(f"⚠ set_habit: {e}")

    def get_pattern(self, condition):
        """Паттерн для condition ('weather:rain' / 'weather:snow' / ...)."""
        try:
            row = self.storage.query_one("""
                SELECT value_json FROM aggregates_cache WHERE key = ?
            """, (f"pattern:{condition}",))
            if row and row.get('value_json'):
                return json.loads(row['value_json'])
        except Exception:
            pass
        return None

    def set_pattern(self, condition, artist, count=1):
        """Записать паттерн: condition → artist."""
        try:
            existing = self.get_pattern(condition) or {}
            existing[artist] = existing.get(artist, 0) + count
            self.storage.execute("""
                INSERT OR REPLACE INTO aggregates_cache(key, value_json, computed_at)
                VALUES (?, ?, datetime('now'))
            """, (f"pattern:{condition}", json.dumps(existing, ensure_ascii=False)))
        except Exception as e:
            print(f"⚠ set_pattern: {e}")

    # ========================================================
    # BLACKLIST (v3)
    # ========================================================
    def is_blacklisted(self, track_id):
        return self.preferences.is_blacklisted(track_id)

    def blacklist_track(self, track_id, reason='user_dislike'):
        self.preferences.blacklist(track_id, reason)

    def unblacklist_track(self, track_id, reason='user_like'):
        self.preferences.unblacklist(track_id, reason)

    # ========================================================
    # ЧТЕНИЕ — АГРЕГАТЫ (Aggregates)
    # ========================================================
    def top_artists(self, limit=10, days=None):
        return self.aggregates.top_artists(limit, days)

    def top_tracks(self, limit=20, days=None):
        return self.aggregates.top_tracks(limit, days)

    def recalc_weights(self):
        return self.aggregates.recalc_all()

    def stats(self):
        return self.aggregates.stats_summary()

    # ========================================================
    # ЧТЕНИЕ — С ГОРИЗОНТОМ (Reader)
    # ========================================================
    def plays_since(self, seconds_ago, event_type='play_start'):
        return self.reader.plays_since(seconds_ago, event_type)

    def plays_between(self, start_dt, end_dt, event_type='play_start'):
        return self.reader.plays_between(start_dt, end_dt, event_type)

    def plays_on_date(self, dt, event_type='play_start'):
        return self.reader.plays_on_date(dt, event_type)

    def plays_by_hour(self, hour, event_type='play_start'):
        return self.reader.plays_by_hour(hour, event_type)

    def plays_by_weekday(self, weekday, event_type='play_start'):
        return self.reader.plays_by_weekday(weekday, event_type)

    def plays_for_artist(self, artist, since_ts=None):
        return self.reader.plays_for_artist(artist, since_ts)

    def last_played(self, key, by='artist'):
        return self.reader.last_played(key, by)

    def days_since(self, key, by='artist'):
        return self.reader.days_since(key, by)

    def streak_days(self, artist):
        return self.reader.streak_days(artist)

    def diversity_since(self, seconds_ago, event_type='play_start'):
        return self.reader.diversity_since(seconds_ago, event_type)

    def top_since(self, seconds_ago, limit=10, event_type='play_start'):
        return self.reader.top_since(seconds_ago, limit, event_type)

    def top_tracks_since(self, seconds_ago, limit=10, event_type='play_start'):
        return self.reader.top_tracks_since(seconds_ago, limit, event_type)

    def skips_since(self, seconds_ago):
        return self.reader.skips_since(seconds_ago)

    def finishes_since(self, seconds_ago):
        return self.reader.finishes_since(seconds_ago)

    def count_plays_in_range(self, start_dt, end_dt, event_type='play_start'):
        return self.reader.count_plays_in_range(start_dt, end_dt, event_type)

    def reader_summary(self):
        return self.reader.summary()

    # ========================================================
    # СОВМЕСТИМОСТЬ
    # ========================================================
    def start(self):
        """Совместимость: writer стартует в __init__. No-op."""
        pass

    # ========================================================
    # ОСТАНОВКА
    # ========================================================
    def shutdown(self):
        """Останавливает writer. Вызывать при завершении процесса."""
        try:
            self.writer.stop()
        except Exception as e:
            print(f"⚠ memory shutdown: {e}")
        try:
            self.storage.close()
        except Exception:
            pass

    def flush(self):
        """Дождаться обработки очереди (для тестов/диагностики)."""
        import time
        for _ in range(50):
            if self.writer.queue.empty():
                time.sleep(0.1)
                break
            time.sleep(0.1)