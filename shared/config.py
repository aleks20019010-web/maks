# shared/config.py
# Все пути, константы и настройки Макса.
# Ни от чего не зависит — только stdlib.
#
# FIX: пути больше не хардкодятся (D:\Maks, E:\Music).
# BASE_DIR = Path(__file__).resolve().parent.parent — работает на любом компе.
# Внешние пути (музыка, VLC) — из config/paths.json (с env fallback).
#
# FIX: paths.json читается с encoding="utf-8-sig" — PowerShell Out-File
# добавляет BOM, и json.load с "utf-8" падает с Unexpected UTF-8 BOM.
#
# FIX: VOSK_MODEL_PATH — str, а не Path. Vosk вызывает .encode() на аргументе,
# Path этого не умеет. Остальные пути — Path (работают с open/os.path).
#
# ВАЖНО: старый core/config.py пока остаётся. Этот файл — для новой архитектуры.
# Когда переедем полностью, core/config.py удалим.

import os
import json
from pathlib import Path


# ============================================================
# БАЗОВЫЙ КАТАЛОГ (относительно файла, не хардкод)
# ============================================================
BASE_DIR = Path(__file__).resolve().parent.parent


# ============================================================
# ВНЕШНИЕ ПУТИ (музыка, VLC) — из config/paths.json
# ============================================================
_PATHS_FILE = BASE_DIR / "config" / "paths.json"

_external = {}
if _PATHS_FILE.exists():
    try:
        # FIX: utf-8-sig съедает BOM (PowerShell Out-File -Encoding utf8 добавляет)
        with open(_PATHS_FILE, "r", encoding="utf-8-sig") as f:
            _external = json.load(f)
    except Exception as e:
        print(f"⚠ config: ошибка чтения paths.json: {e}")


def _ext(key: str, default: str) -> Path:
    """Внешний путь: paths.json → env → default."""
    env_key = f"MAKS_{key.upper()}"
    value = os.environ.get(env_key) or _external.get(key) or default
    return Path(value)


MUSIC_DIR = _ext("music_dir", str(BASE_DIR / "music"))
VLC_PATH = _ext("vlc_path", r"C:\Program Files\VideoLAN\VLC\vlc.exe")


# ============================================================
# ПУТИ
# ============================================================
CONFIG_DIR = BASE_DIR / "config"
MODELS_DIR = BASE_DIR / "models"
RESPONSES_DIR = BASE_DIR / "responses"
LOGS_DIR = BASE_DIR / "logs"

TOKEN_FILE = CONFIG_DIR / "token.txt"
ALIASES_FILE = CONFIG_DIR / "aliases.json"
VOSK_MODEL_PATH = str(MODELS_DIR / "vosk-model-small-ru-0.22")  # str — Vosk требует
LOG_FILE = LOGS_DIR / "jarvis.log"

# Данные — отдельно от кода
DATA_DIR = BASE_DIR / "data"
CACHE_DIR = DATA_DIR / "tts_cache"
DATA_LOGS_DIR = DATA_DIR / "logs"
MEMORY_DB = DATA_DIR / "memory.db"

# Старые пути (для обратной совместимости с core/)
TTS_TEMP_DIR = BASE_DIR / "cache"
TTS_TEMP_FILE = TTS_TEMP_DIR / "_tts_temp.mp3"


# ============================================================
# МУЗЫКА
# ============================================================
TARGET_AHEAD = 6
PREFETCH_COUNT = 5
QUALITY = '1'
PATH_PATTERN = '#album-artist\\#album\\#number-padded - #title'
MIN_FILE_SIZE = 100 * 1024

MAX_SIMILAR_ARTISTS = 50
ARTIST_PAGE_SIZE = 20
LIKED_BATCH = 50

GENRE_IDS = [
    "genre:rusrock",
    "genre:metal",
    "genre:rusestrada",
    "genre:pop",
    "genre:alternative",
    "genre:rap",
    "genre:indie",
    "genre:rock",
    "genre:jazz",
    "genre:classical",
]

MY_WAVE_ID = "user:onyourwave"

SOURCE_LIST = [
    'feed_tracks',
    'chart',
    'feed_albums',
    MY_WAVE_ID,
] + GENRE_IDS

PORTIONS_PER_SOURCE = 3
WAVE_RESET_SEC = 300

# ============================================================
# МИКРОФОН / ASR
# ============================================================
MIC_DEVICE_INDEX = 1
MIC_CAPTURE_RATE = 48000
VOSK_RATE = 16000

# Wake word — «Макс». Vosk слышит его 12/12.
WAKE_WORDS = [
    "макс",
    "мак",
    "макса",
    "максу",
]

WAKE_FUZZY_THRESHOLD = 0.35
COMMAND_TIMEOUT_SEC = 10.0
WAKE_COOLDOWN_SEC = 3.0

# ============================================================
# ПРЕДОХРАНИТЕЛИ
# ============================================================
NEXT_DEBOUNCE_SEC = 3.0
NEXT_SPAM_WINDOW_SEC = 30.0
NEXT_SPAM_THRESHOLD = 5

# ============================================================
# TTS
# ============================================================
TTS_ENGINE = "yandex"

TTS_VOICE = "ru-RU-DmitryNeural"
TTS_RATE = "+5%"
TTS_PITCH = "-10Hz"

YANDEX_VOICE = "ermil"
YANDEX_EMOTION = "neutral"
YANDEX_SPEED = "1.05"
YANDEX_KEY_FILE = CONFIG_DIR / "yandex_key.txt"
YANDEX_FOLDER_FILE = CONFIG_DIR / "yandex_folder.txt"

TTS_ENABLED = True
TTS_ECHO_GUARD_SEC = 0.8
TTS_MUTE_DEFAULT_SEC = 300

# ============================================================
# СОЗДАНИЕ ПАПОК
# ============================================================
for _p in (CONFIG_DIR, MODELS_DIR, RESPONSES_DIR, LOGS_DIR,
           DATA_DIR, CACHE_DIR, DATA_LOGS_DIR,
           TTS_TEMP_DIR, MUSIC_DIR):
    _p.mkdir(parents=True, exist_ok=True)

# ============================================================
# AEC (Acoustic Echo Cancellation) — через echoff (WebRTC AEC3)
# ============================================================
AEC_ENABLED = False

ECHOFF_SAMPLE_RATE_HZ = 48000
ECHOFF_STREAM_DELAY_MS = 100

AEC_LOOPBACK_DEVICE_INDEX = 10