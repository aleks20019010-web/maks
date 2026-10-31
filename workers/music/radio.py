# workers/music/radio.py
# Радио: живые интернет-потоки.
# Music worker НЕ говорит. Brain скажет сам.
#
# DUCKING (день 29, вечер): ducking привязан к микрофону ASR.
#   Убран music.release_duck() после mark_ready. Ducking от TTS
#   (если TTS ещё говорит, пока радио стартует) снимает ASR при mic_on.
#   Раньше: release_duck() в start_radio() снимал ducking от TTS
#   раньше времени — та же гонка, что в playback.play_index
#   (баг из лога 15:15:20: volume 100 → 20 → 100, TTS ещё говорит).
#   Если TTS говорит во время старта радио, music.set_player() создаст
#   плеер приглушённым (ducked=True), mark_ready() применит _duck_level.
#   ASR пришлёт duck_off при mic_on — восстановит громкость.

import json
import os

import vlc

from shared import config as cfg
from workers.music import state
from workers.music.player import music
from memory import memory


STATIONS_FILE = os.path.join(cfg.BASE_DIR, 'config', 'radio_stations.json')
ALIASES_FILE = os.path.join(cfg.BASE_DIR, 'config', 'radio_aliases.json')


def _load_json(path):
    if not os.path.exists(path):
        print(f"⚠ radio: не найден {path}")
        return {}
    try:
        with open(path, 'r', encoding='utf-8-sig') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"⚠ radio: ошибка чтения {path}: {e}")
        return {}


RADIO_STATIONS = _load_json(STATIONS_FILE)
RADIO_ALIASES = _load_json(ALIASES_FILE)


def resolve_radio(query: str) -> str | None:
    q = query.lower().strip()
    if not q:
        return None

    if q in RADIO_STATIONS:
        return q

    key = RADIO_ALIASES.get(q)
    if key and key in RADIO_STATIONS:
        return key

    for station_key in sorted(RADIO_STATIONS.keys(), key=len, reverse=True):
        if station_key in q:
            return station_key

    for alias in sorted(RADIO_ALIASES.keys(), key=len, reverse=True):
        if alias in q:
            return RADIO_ALIASES[alias]

    return None


def is_radio_query(query: str) -> bool:
    return resolve_radio(query) is not None


def start_radio(station_key: str) -> bool:
    """Запустить радиостанцию по ключу."""
    station = RADIO_STATIONS.get(station_key)
    if not station:
        return False

    name = station.get('name', station_key)
    url = station.get('url', '')
    if not url:
        print(f"  ✗ Радио «{name}»: URL пустой")
        return False

    print(f"📻 Радио: {name}")
    print(f"   URL: {url}")

    try:
        music.stop()
    except Exception:
        pass

    with state.playlist_lock:
        state.playlist = []
        state.prefetched = {}
    state.playlist_index = 0
    state.playlist_mode = "radio"
    state.current_track_title = f"{name} — Радио"

    try:
        player = vlc.MediaPlayer(url)
        music.set_player(player)
        player.play()
        music.mark_ready()
        # Ducking от TTS (если активен) снимает ASR при mic_on.
        # Здесь music.release_duck() снимал ducking раньше времени (баг 15:15:20).
    except Exception as e:
        print(f"  ✗ Радио «{name}»: ошибка VLC: {e}")
        return False

    memory.remember_search(
        query=f"радио {station_key}",
        found_artist=name,
        success=True,
    )

    return True