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
# C10 (день 29): ViolaWake — wake-детектор.
# A2 (день 29): WAKE_WINDOW_SEC — окно после wake, Vosk слушает команду.
#   music_playing.flag (music worker) — hot words при музыке.
#   expecting_input.flag (brain pending) — hot words при ожидании ответа.
#   session_open.flag (TTS worker) — окно после ответа LLM.
#
# FIX (день 29, вечер):
#   - DUCK_LEVEL — целевая громкость музыки при ducking (%). music/player.py
#     читает через getattr(cfg, "DUCK_LEVEL", 20), но теперь есть явный дефолт
#     в конфиге, чтобы не расходиться при рефакторингах.
#   - MUSIC_INBOX_TIMEOUT — таймаут ipc.get для music-воркера. 0.05 нужен,
#     чтобы duck_on/duck_off применялись быстро (см. music/main.py).
#   - TTS_START_TIMEOUT_SEC / TTS_PLAY_TIMEOUT_SEC — таймауты ожидания
#     State.Playing / Ended в tts/player.py (были хардкодом 2.0/30.0).
#   - TTS_DUCK_TAIL_SEC — хвост после конца фразы перед duck_off (был в
#     коде как getattr-дефолт 0.1; теперь явный).
#   - AEC_LOOPBACK_DEVICE_INDEX — был объявлен, но не используется; оставлен
#     как есть (совместимость), помечен комментарием.
#   - Убрано дублирование путей: TTS_TEMP_DIR = CACHE_DIR (одна папка для
#     временных mp3), чтобы не плодить два каталога с одинаковым смыслом.

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

# Временные mp3 TTS — та же папка, что и кэш TTS.
# Старый TTS_TEMP_DIR (BASE_DIR / "cache") удалён: смысл тот же, а два
# каталога с одинаковым назначением только путают.
TTS_TEMP_DIR = CACHE_DIR
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

# --- DUCKING ---------------------------------------------------
# Целевая громкость музыки при ducking, %. music/player.py берёт это
# через getattr(cfg, "DUCK_LEVEL", 20). Явно здесь — чтобы не расходиться.
DUCK_LEVEL = 20

# --- MUSIC IPC -------------------------------------------------
# Таймаут ipc.get для music-воркера. 0.05 сек нужен, чтобы duck_on/duck_off
# применялись быстро (пока TTS говорит, задержка слышна).
MUSIC_INBOX_TIMEOUT = 0.05


# ============================================================
# МИКРОФОН / ASR
# ============================================================
MIC_DEVICE_INDEX = 1
MIC_CAPTURE_RATE = 48000
VOSK_RATE = 16000

WAKE_WORDS = [
    "макс",
    "макса",
    "максу",
]

WAKE_FUZZY_THRESHOLD = 0.35
COMMAND_TIMEOUT_SEC = 10.0
WAKE_COOLDOWN_SEC = 3.0

# ============================================================
# WAKE WORD (ViolaWake) — C10, день 29
# ============================================================
WAKE_MODEL_PATH = str(MODELS_DIR / "wake_models" / "max_viola_v2.onnx")
WAKE_MODEL_THRESHOLD = 0.80
WAKE_CONFIRM_COUNT = 3
WAKE_COOLDOWN_S = 2.0

# ============================================================
# A2 (день 29): окна контекста для ASR
# ============================================================
# WAKE_WINDOW_SEC — сколько Vosk слушает команду после wake.
# 5 сек — как у Алисы. Vosk просыпается на 5 сек, ловит команду.
WAKE_WINDOW_SEC = 5.0

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

# --- TTS playback timeouts (tts/player.py) ---------------------
# Ожидание реального старта (State.Playing). 2.0 сек — как было хардкодом.
TTS_START_TIMEOUT_SEC = 2.0
# Ожидание реального конца (Ended/Stopped). 30 сек — как было хардкодом.
TTS_PLAY_TIMEOUT_SEC = 30.0
# Хвост после конца фразы перед отправкой duck_off. Чтобы не обрывать
# последние сэмплы из-за буферизации VLC.
TTS_DUCK_TAIL_SEC = 0.1


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

# AEC_LOOPBACK_DEVICE_INDEX сейчас не используется в коде (только
# объявлен). Оставлен для обратной совместимости с конфигами,
# которые на него ссылаются. При включении AEC — читать отсюда.
AEC_LOOPBACK_DEVICE_INDEX = 10