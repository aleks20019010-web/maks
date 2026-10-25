"""
workers/music/state.py — состояние музыки.

Только музыка. Никакого TTS, wake, анти-повтора.
TTS-состояние — в workers/tts/state_file.py.
Wake — в workers/asr/.
Анти-повтор — в brain/router.py.

Яндекс-клиент — здесь (client, TOKEN). Инициализируется в workers/music/main.py.
"""

import threading

# ── Яндекс ────────────────────────────────────────────────────
client = None
TOKEN = None

# ── Плейлист и очередь ────────────────────────────────────────
playlist = []
playlist_index = 0
playlist_mode = None
playlist_lock = threading.Lock()
prefetched = {}

current_track_title = None
current_track_id = None
last_play_start_time = 0.0

# Флаг последнего skip (для монитора — не писать ложный finish)
_last_skipped_track_id = None

# ── Блокировка переключения (play_index / play_next / play_prev) ──
play_lock = threading.Lock()
switching = False

# ── Источники ─────────────────────────────────────────────────
current_source_idx = 0
current_source_portions = 0
source_buffer = []
source_buffer_lock = threading.Lock()
wave_exhausted = False
wave_exhausted_time = 0.0
_consecutive_empty = 0

# Язык «Моей волны»: None = обычная, "russian" = только русское.
wave_language = None

liked_short_tracks = []
liked_offset = 0

# ── Кэш источников Яндекса ────────────────────────────────────
_source_cache = {}
_source_cache_lock = threading.Lock()

# ── Радио-режимы ──────────────────────────────────────────────
radio_artist_id = None
radio_artist_name = None
radio_artist_page = 0
radio_artist_exhausted = False

radio_similar_list = []
radio_similar_offset = 0
radio_similar_page = 0
radio_similar_exhausted = False

single_artist_id = None

# ── Жанровый режим ────────────────────────────────────────────
genre_station_id = None
genre_name = None

artist_tracks_cache = {}
artist_tracks_exhausted = {}

# ── Каскад источников (mixed) ─────────────────────────────────
source_cycle_pos = 0
local_played_count = 0
LOCAL_MAX_STREAK = 4
LOCAL_STREAK_JITTER = 1
LOCAL_RETRY_AFTER_SEC = 30
local_wait_until = 0.0

session_played_ids = set()
session_played_lock = threading.Lock()

# ── Дедупликация ──────────────────────────────────────────────
played_track_ids = set()
played_lock = threading.Lock()

queued_track_ids = set()
queued_lock = threading.Lock()

# ── Анти-спам next ────────────────────────────────────────────
last_next_time = 0.0

# ── Рефреш ────────────────────────────────────────────────────
refill_lock = threading.Lock()

# ── Кэш аудио-файлов ──────────────────────────────────────────
_audio_cache = None
_audio_cache_lock = threading.Lock()

# ── Кэш алиасов ───────────────────────────────────────────────
_aliases_cache = None
_aliases_lock = threading.Lock()