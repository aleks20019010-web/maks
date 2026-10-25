# memory/events.py
# SQL-вставки событий и чтение.
# Публичные методы record_* больше НЕ пишут напрямую — они в writer.
# Здесь только _insert_* (для writer) и чтение (для check_memory.py).
#
# v2 (2026-10-10):
#   + Универсальный _insert_event
#   + context (dict → JSON) во всех _insert_*
#   + extra (dict → JSON) для доп. полей

import json


class Events:
    def __init__(self, storage):
        self.storage = storage

    # ========================================================
    # УНИВЕРСАЛЬНАЯ ЗАПИСЬ
    # ========================================================
    def _insert_event(self, event_type, *,
                      query=None, artist=None, track_id=None, title=None,
                      source=None, duration=0, completed=0, skipped_at=0,
                      context=None, extra=None):
        """Универсальная запись события в events.

        context и extra — dict, будут сериализованы в JSON.
        """
        ctx_json = json.dumps(context, ensure_ascii=False) if context else None
        ext_json = json.dumps(extra, ensure_ascii=False) if extra else None

        self.storage.execute("""
            INSERT INTO events (
                event_type, query, artist, track_id, track_title,
                source, duration, completed, skipped_at,
                context_json, extra_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            event_type,
            query,
            artist,
            str(track_id) if track_id is not None else None,
            title,
            source,
            int(duration) if duration else 0,
            int(completed) if completed else 0,
            int(skipped_at) if skipped_at else 0,
            ctx_json,
            ext_json,
        ))

    # ========================================================
    # _INSERT (вызываются из Writer) — обёртки
    # ========================================================
    def _insert_play_request(self, query, context=None):
        self._insert_event('play_request', query=query, context=context)

    def _insert_play_start(self, track_id, artist, title, source, context=None):
        self._insert_event(
            'play_start',
            track_id=track_id, artist=artist, title=title, source=source,
            context=context,
        )

        # Обновить агрегаты
        self.storage.execute("""
            INSERT INTO artist_stats (artist, play_count, last_played)
            VALUES (?, 1, CURRENT_TIMESTAMP)
            ON CONFLICT(artist) DO UPDATE SET
                play_count = play_count + 1,
                last_played = CURRENT_TIMESTAMP
        """, (artist,))

        self.storage.execute("""
            INSERT INTO track_stats (track_id, artist, title, play_count, last_played, source)
            VALUES (?, ?, ?, 1, CURRENT_TIMESTAMP, ?)
            ON CONFLICT(track_id) DO UPDATE SET
                play_count = play_count + 1,
                last_played = CURRENT_TIMESTAMP,
                source = excluded.source
        """, (str(track_id), artist, title, source))

    def _insert_skip(self, track_id, seconds_played=0, context=None):
        self._insert_event(
            'skip',
            track_id=track_id, skipped_at=seconds_played,
            context=context,
        )

        self.storage.execute("""
            UPDATE track_stats SET skip_count = skip_count + 1 WHERE track_id = ?
        """, (str(track_id),))

        row = self.storage.query_one(
            "SELECT artist FROM track_stats WHERE track_id = ?", (str(track_id),))
        if row and row.get('artist'):
            self.storage.execute("""
                UPDATE artist_stats SET skip_count = skip_count + 1 WHERE artist = ?
            """, (row['artist'],))

    def _insert_finish(self, track_id, duration=0, context=None):
        self._insert_event(
            'finish',
            track_id=track_id, duration=duration, completed=1,
            context=context,
        )

        self.storage.execute("""
            UPDATE track_stats SET finish_count = finish_count + 1 WHERE track_id = ?
        """, (str(track_id),))

        row = self.storage.query_one(
            "SELECT artist FROM track_stats WHERE track_id = ?", (str(track_id),))
        if row and row.get('artist'):
            self.storage.execute("""
                UPDATE artist_stats SET finish_count = finish_count + 1 WHERE artist = ?
            """, (row['artist'],))

    def _insert_search(self, query, found_artist=None, found_track=None,
                       success=False, context=None):
        # В search_history — как раньше
        self.storage.execute("""
            INSERT INTO search_history (query, found_artist, found_track, success)
            VALUES (?, ?, ?, ?)
        """, (query, found_artist, found_track, 1 if success else 0))

        # И в events — для единого журнала
        self._insert_event(
            'search',
            query=query,
            artist=found_artist,
            title=found_track,
            completed=1 if success else 0,
            context=context,
        )

    # ========================================================
    # НОВЫЕ ТИПЫ (v2)
    # ========================================================
    def _insert_repeat(self, command, count, context=None):
        self._insert_event(
            'repeat_command',
            query=command,
            extra={'count': count},
            context=context,
        )

    def _insert_idle(self, duration_sec, was_playing, context=None):
        self._insert_event(
            'idle_period',
            duration=duration_sec,
            extra={'was_playing': was_playing},
            context=context,
        )

    def _insert_wake(self, source='voice', context=None):
        self._insert_event('wake_word', source=source, context=context)

    def _insert_mode_change(self, from_mode, to_mode, trigger='', context=None):
        self._insert_event(
            'mode_change',
            source=trigger,
            extra={'from': from_mode, 'to': to_mode},
            context=context,
        )

    def _insert_volume_change(self, old, new, source='', context=None):
        self._insert_event(
            'volume_change',
            source=source,
            extra={'old': old, 'new': new},
            context=context,
        )

    def _insert_silence(self, duration_sec, context=None):
        self._insert_event(
            'silence_command',
            duration=duration_sec,
            context=context,
        )

    # ========================================================
    # ЧТЕНИЕ (для check_memory.py и reader.py)
    # ========================================================
    def recent_events(self, limit=50):
        return self.storage.query("""
            SELECT * FROM events ORDER BY timestamp DESC LIMIT ?
        """, (limit,))

    def event_count(self):
        row = self.storage.query_one("SELECT COUNT(*) AS c FROM events")
        return row['c'] if row else 0