# memory/storage.py
# SQLite с постоянным соединением и WAL — быстро.
# Потокобезопасный.
#
# Schema v2 (2026-10-10):
#   + events.context_json       — JSON с контекстом события
#   + events.extra_json         — JSON с доп. полями
#   + idx_events_type_ts        — индекс по типу и времени
#   + self_events               — «память о себе»
#   + associations              — связи между событиями
#   + aggregates_cache          — кэш тяжёлых агрегатов

import os
import json
import sqlite3
import threading


SCHEMA_VERSION = 2


class Storage:
    def __init__(self, db_path):
        self.db_path = db_path
        self.lock = threading.RLock()
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._conn = sqlite3.connect(
            db_path,
            check_same_thread=False,
            isolation_level=None,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._init_db()

    # ========================================================
    # ИНИЦИАЛИЗАЦИЯ СХЕМЫ
    # ========================================================
    def _init_db(self):
        with self.lock:
            cur = self._conn.cursor()

            # --- meta ---
            cur.execute("""
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)

            # --- events (v1) ---
            cur.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    event_type TEXT NOT NULL,
                    query TEXT,
                    artist TEXT,
                    track_id TEXT,
                    track_title TEXT,
                    source TEXT,
                    duration INTEGER DEFAULT 0,
                    completed INTEGER DEFAULT 0,
                    skipped_at INTEGER DEFAULT 0
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_events_ts ON events(timestamp)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_events_artist ON events(artist)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_events_track ON events(track_id)")

            # --- events v2: миграция колонок ---
            self._ensure_column(cur, "events", "context_json", "TEXT")
            self._ensure_column(cur, "events", "extra_json", "TEXT")

            # --- events v2: индексы ---
            cur.execute("CREATE INDEX IF NOT EXISTS idx_events_type_ts ON events(event_type, timestamp)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_events_artist_ts ON events(artist, timestamp)")

            # --- artist_stats ---
            cur.execute("""
                CREATE TABLE IF NOT EXISTS artist_stats (
                    artist TEXT PRIMARY KEY,
                    request_count INTEGER DEFAULT 0,
                    play_count INTEGER DEFAULT 0,
                    skip_count INTEGER DEFAULT 0,
                    finish_count INTEGER DEFAULT 0,
                    last_request DATETIME,
                    last_played DATETIME,
                    weight REAL DEFAULT 0.0
                )
            """)

                        # --- track_stats ---
            cur.execute("""
                CREATE TABLE IF NOT EXISTS track_stats (
                    track_id TEXT PRIMARY KEY,
                    artist TEXT,
                    title TEXT,
                    play_count INTEGER DEFAULT 0,
                    skip_count INTEGER DEFAULT 0,
                    finish_count INTEGER DEFAULT 0,
                    last_played DATETIME,
                    source TEXT,
                    weight REAL DEFAULT 0.0,
                    blacklisted INTEGER DEFAULT 0
                )
            """)

            # --- track_stats v2: миграция blacklisted ---
            self._ensure_column(cur, "track_stats", "blacklisted", "INTEGER DEFAULT 0")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_track_blacklisted ON track_stats(blacklisted)")
            # --- search_history ---
            cur.execute("""
                CREATE TABLE IF NOT EXISTS search_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    query TEXT,
                    found_artist TEXT,
                    found_track TEXT,
                    success INTEGER DEFAULT 0
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_search_ts ON search_history(timestamp)")

            # --- self_events (v2) ---
            cur.execute("""
                CREATE TABLE IF NOT EXISTS self_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    event_type TEXT NOT NULL,
                    text TEXT,
                    rule_name TEXT,
                    extra_json TEXT
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_self_ts ON self_events(timestamp)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_self_type_ts ON self_events(event_type, timestamp)")

            # --- associations (v2) ---
            cur.execute("""
                CREATE TABLE IF NOT EXISTS associations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_a TEXT NOT NULL,
                    event_b TEXT NOT NULL,
                    strength REAL DEFAULT 0.0,
                    last_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
                    count INTEGER DEFAULT 1,
                    UNIQUE(event_a, event_b)
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_assoc_a ON associations(event_a)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_assoc_strength ON associations(strength)")

            # --- aggregates_cache (v2) ---
            cur.execute("""
                CREATE TABLE IF NOT EXISTS aggregates_cache (
                    key TEXT PRIMARY KEY,
                    value_json TEXT,
                    computed_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # --- schema_version ---
            cur.execute("INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)",
                        ('schema_version', str(SCHEMA_VERSION)))
            cur.execute("UPDATE meta SET value = ? WHERE key = 'schema_version'",
                        (str(SCHEMA_VERSION),))

    def _ensure_column(self, cur, table, column, col_type):
        """Добавить колонку, если её ещё нет (миграция v1 → v2)."""
        cur.execute(f"PRAGMA table_info({table})")
        cols = {row[1] for row in cur.fetchall()}
        if column not in cols:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")

    # ========================================================
    # EXECUTE / QUERY
    # ========================================================
    def execute(self, sql, params=()):
        with self.lock:
            cur = self._conn.cursor()
            cur.execute(sql, params)
            return cur.lastrowid

    def executemany(self, sql, params_list):
        with self.lock:
            cur = self._conn.cursor()
            cur.executemany(sql, params_list)
            return cur.rowcount

    def query(self, sql, params=()):
        with self.lock:
            cur = self._conn.cursor()
            cur.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]

    def query_one(self, sql, params=()):
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def close(self):
        with self.lock:
            try:
                self._conn.close()
            except Exception:
                pass