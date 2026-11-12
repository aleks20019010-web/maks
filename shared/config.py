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
#
# ── FIX (этап 0.2) ─────────────────────────────────────────────
# MUSIC_DIR.mkdir на импорте падал, если внешний диск (E:\Music) отключён:
# FileNotFoundError → import shared.config → падают ВСЕ воркеры, включая
# brain/TTS/ASR. Теперь MUSIC_DIR.mkdir в try/except + флаг MUSIC_AVAILABLE.
# music-воркер при старте проверяет cfg.MUSIC_AVAILABLE и, если False,
# логирует и выходит — не пытается лезть в недоступную папку.
#
# ── FIX (этап 0.1) ─────────────────────────────────────────────
# RUNTIME_DIR = DATA_DIR / "runtime" — для shared/runtime_state.py
# (межпроцессные флаги с TTL). Создаётся при импорте.
#
# ── FIX (этап 0.3) ─────────────────────────────────────────────
# LOG_FILE переехал в DATA_LOGS_DIR (data/logs), а не в LOGS_DIR
# (BASE_DIR/logs). Причина: data/ — это данные, BASE_DIR/logs — это
# тоже данные, но исторически в корне. Держим всё в data/.
# LOGS_DIR оставлен (создаётся, пустой) — вдруг кто-то ещё читает.
# Если grep покажет, что никто — удалим.
#
# ── P1.5: Яндекс — не долбить мёртвый API ──────────────────────
# Если инет лёг или API поменялся — это надолго. 5 попыток раз в
# минуту, потом блок на час. Если хоть одна вышла — сброс.
# Brain перезапустился → state новый → снова 5 попыток.

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
LOGS_DIR = BASE_DIR / "logs"   # исторический; для логов теперь DATA_LOGS_DIR

TOKEN_FILE = CONFIG_DIR / "token.txt"
ALIASES_FILE = CONFIG_DIR / "aliases.json"
VOSK_MODEL_PATH = str(MODELS_DIR / "vosk-model-small-ru-0.22")  # str — Vosk требует

# Данные — отдельно от кода
DATA_DIR = BASE_DIR / "data"
CACHE_DIR = DATA_DIR / "tts_cache"
DATA_LOGS_DIR = DATA_DIR / "logs"
RUNTIME_DIR = DATA_DIR / "runtime"   # FIX 0.1: флаги с TTL
MEMORY_DB = DATA_DIR / "memory.db"

# Логи: пишем в data/logs, а не в BASE_DIR/logs (см. шапку).
LOG_FILE = DATA_LOGS_DIR / "jarvis.log"

# Временные mp3 TTS — та же папка, что и кэш TTS.
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
DUCK_LEVEL = 30

# --- MUSIC IPC -------------------------------------------------
MUSIC_INBOX_TIMEOUT = 0.05

# --- P1.5: Яндекс — не долбить мёртвый API --------------------
# Если инет лёг или API поменялся — это надолго. 5 попыток раз в
# минуту, потом блок на час. Если хоть одна вышла — сброс.
YANDEX_FAIL_MAX = 5                 # 5 фейлов подряд
YANDEX_RETRY_INTERVAL_SEC = 60.0    # раз в минуту
YANDEX_BLOCK_SEC = 3600.0           # после 5 фейлов — час


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

# ── ViolaWake ───────────────────────────────────────────────
WAKE_MODEL_PATH = str(MODELS_DIR / "wake_models" / "max_viola_v3.onnx")
WAKE_MODEL_THRESHOLD = 0.85   # 0.70 sensitive, 0.80 balanced, 0.85 conservative [citation:11]
WAKE_COOLDOWN_S = 2.0         # секунд между детекциями [citation:2]

# ── Vosk грамматика ─────────────────────────────────────────
# Vosk будет искать ТОЛЬКО эти слова. [unk] обязателен [citation:13].
VOSK_HOT_WORDS_GRAMMAR = '["дальше", "стоп", "тише", "громче", "пауза", "продолжи", "назад", "[unk]"]'

# ============================================================
# A2 (день 29): окна контекста для ASR
# ============================================================
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
TTS_START_TIMEOUT_SEC = 3.0
TTS_PLAY_TIMEOUT_SEC = 30.0
TTS_DUCK_TAIL_SEC = 0.1


# ============================================================
# СОЗДАНИЕ ПАПОК
# ============================================================
# FIX 0.2: MUSIC_DIR — отдельно, в try/except. Если внешний диск
# отключён — brain/TTS/ASR должны работать (музыка отключена).
# Остальные папки — внутренние, всегда доступны, mkdir безопасен.
for _p in (CONFIG_DIR, MODELS_DIR, RESPONSES_DIR, LOGS_DIR,
           DATA_DIR, CACHE_DIR, DATA_LOGS_DIR, RUNTIME_DIR,
           TTS_TEMP_DIR):
    _p.mkdir(parents=True, exist_ok=True)

MUSIC_AVAILABLE = True
try:
    MUSIC_DIR.mkdir(parents=True, exist_ok=True)
except OSError as e:
    MUSIC_AVAILABLE = False
    # print, а не log: log импортирует config, был бы цикл.
    # Это одна строка на старте — не страшно.
    print(f"⚠ config: MUSIC_DIR недоступен ({MUSIC_DIR}): {e}. "
          f"Музыка отключена, остальные воркеры работают.")


# ============================================================
# AEC (Acoustic Echo Cancellation) — через SpeexDSP
# ============================================================
AEC_ENABLED = True
CABLE_DEVICE_INDEX = 2   # CABLE Output (MME) — far-поток для AEC

# Старые параметры echoff (WebRTC) — оставлены на случай отката
ECHOFF_SAMPLE_RATE_HZ = 48000
ECHOFF_STREAM_DELAY_MS = 100
AEC_LOOPBACK_DEVICE_INDEX = 10