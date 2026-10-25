# shared/config.py
# Все пути, константы и настройки Макса.
# Ни от чего не зависит — только stdlib.
#
# ВАЖНО: старый core/config.py пока остаётся. Этот файл — для новой архитектуры.
# Когда переедем полностью, core/config.py удалим.

import os

# ============================================================
# ПУТИ
# ============================================================
BASE_DIR = r'D:\Maks'

TOKEN_FILE = os.path.join(BASE_DIR, 'config', 'token.txt')
ALIASES_FILE = os.path.join(BASE_DIR, 'config', 'aliases.json')
VOSK_MODEL_PATH = os.path.join(BASE_DIR, 'models', 'vosk-model-small-ru-0.22')
LOG_FILE = os.path.join(BASE_DIR, 'logs', 'jarvis.log')

# Данные — отдельно от кода
DATA_DIR = os.path.join(BASE_DIR, 'data')
CACHE_DIR = os.path.join(DATA_DIR, 'tts_cache')
LOGS_DIR = os.path.join(DATA_DIR, 'logs')
MEMORY_DB = os.path.join(DATA_DIR, 'memory.db')

# Старые пути (для обратной совместимости с core/)
TTS_TEMP_DIR = os.path.join(BASE_DIR, 'cache')      # legacy
TTS_TEMP_FILE = os.path.join(TTS_TEMP_DIR, '_tts_temp.mp3')

MUSIC_DIR = r'E:\Music'

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
# «Джарвис» убран полностью — не путаемся.
WAKE_WORDS = [
    "макс",
    # Vosk-коверканье «Макс» (на случай, если услышит не так)
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
YANDEX_KEY_FILE = os.path.join(BASE_DIR, 'config', 'yandex_key.txt')
YANDEX_FOLDER_FILE = os.path.join(BASE_DIR, 'config', 'yandex_folder.txt')

TTS_ENABLED = True
TTS_ECHO_GUARD_SEC = 0.8
TTS_MUTE_DEFAULT_SEC = 300

# ============================================================
# СОЗДАНИЕ ПАПОК
# ============================================================
os.makedirs(os.path.join(BASE_DIR, 'config'), exist_ok=True)
os.makedirs(os.path.join(BASE_DIR, 'models'), exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)
os.makedirs(os.path.join(BASE_DIR, 'logs'), exist_ok=True)  # legacy
os.makedirs(TTS_TEMP_DIR, exist_ok=True)                     # legacy
os.makedirs(MUSIC_DIR, exist_ok=True)

# ============================================================
# AEC (Acoustic Echo Cancellation) — через echoff (WebRTC AEC3)
# ============================================================
# AEC_ENABLED = True → EchoffWorker захватывает микрофон и loopback сам,
#                     sd.RawInputStream в main.py НЕ открывается.
# AEC_ENABLED = False → сырой микрофон (near_q), как раньше.
AEC_ENABLED = False

# echoff работает на 48 кГц (WASAPI native), ресемплим в VOSK_RATE (16 кГц)
ECHOFF_SAMPLE_RATE_HZ = 48000
ECHOFF_STREAM_DELAY_MS = 100     # задержка loopback vs mic (Realtek ~100-200)

# Индексы устройств (из `python -m echoff devices`)
AEC_LOOPBACK_DEVICE_INDEX = 10   # Динамики (Realtek) [Loopback]
# MIC_DEVICE_INDEX = 1 уже задан выше — используется и для echoff