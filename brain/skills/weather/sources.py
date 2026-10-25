# brain/skills/weather/sources.py
# OpenWeatherMap API. Ключ — config/weather_key.txt
# Кэш 15 минут — чтобы не дёргать на каждый запрос.

import json
import os
import time
import urllib.request
import urllib.parse

from shared import config as cfg


WEATHER_CONFIG_FILE = os.path.join(cfg.BASE_DIR, 'config', 'weather.json')
WEATHER_KEY_FILE = os.path.join(cfg.BASE_DIR, 'config', 'weather_key.txt')

_cache = {
    "data": None,
    "fetched_at": 0.0,
}


def _load_config():
    if not os.path.exists(WEATHER_CONFIG_FILE):
        print(f"⚠ weather: не найден {WEATHER_CONFIG_FILE}")
        return {"lat": 58.3875, "lon": 33.9119, "city": "Боровичи"}
    try:
        with open(WEATHER_CONFIG_FILE, 'r', encoding='utf-8-sig') as f:
            return json.load(f)
    except Exception as e:
        print(f"⚠ weather: ошибка чтения {WEATHER_CONFIG_FILE}: {e}")
        return {"lat": 58.3875, "lon": 33.9119, "city": "Боровичи"}


def _load_key():
    if not os.path.exists(WEATHER_KEY_FILE):
        print(f"⚠ weather: не найден {WEATHER_KEY_FILE}")
        return ""
    try:
        with open(WEATHER_KEY_FILE, 'r', encoding='utf-8-sig') as f:
            return f.read().strip()
    except Exception as e:
        print(f"⚠ weather: ошибка чтения {WEATHER_KEY_FILE}: {e}")
        return ""


def fetch_weather():
    """Вернуть dict с текущей погодой. Кэш 15 минут."""
    cfgw = _load_config()
    key = _load_key()
    if not key:
        print("   ⚠ weather: нет API-ключа")
        return None

    now = time.time()
    interval = cfgw.get("update_interval_sec", 900)

    if _cache["data"] is not None and (now - _cache["fetched_at"]) < interval:
        return _cache["data"]

    params = {
        "lat": cfgw["lat"],
        "lon": cfgw["lon"],
        "appid": key,
        "units": "metric",
        "lang": "ru",
    }
    url = "https://api.openweathermap.org/data/2.5/weather?" + urllib.parse.urlencode(params)

    data = None
    for attempt in range(3):
        try:
            print(f"   🌤 weather: запрос #{attempt+1}")
            t0 = time.time()
            with urllib.request.urlopen(url, timeout=20) as resp:
                raw = resp.read().decode('utf-8')
            dt = time.time() - t0
            data = json.loads(raw)
            print(f"   🌤 weather: ответ за {dt:.2f}с")
            break
        except Exception as e:
            print(f"   ⚠ weather: попытка {attempt+1}/3 упала: {type(e).__name__}: {e}")
            if attempt < 2:
                time.sleep(1.0)

    if data is None:
        print("   ⚠ weather: все попытки провалились")
        return _cache["data"]

    main = data.get("main") or {}
    wind = data.get("wind") or {}
    weather_list = data.get("weather") or [{}]
    w0 = weather_list[0] if weather_list else {}

    rain_block = data.get("rain") or {}
    snow_block = data.get("snow") or {}
    rain_mm = rain_block.get("1h", rain_block.get("3h", 0.0)) or 0.0
    snow_mm = snow_block.get("1h", snow_block.get("3h", 0.0)) or 0.0

    result = {
        "temp": int(round(main.get("temp", 0))),
        "feels_like": int(round(main.get("feels_like", 0))),
        "windspeed": int(round(wind.get("speed", 0) * 3.6)),
        "weather_id": int(w0.get("id", 800)),
        "weather_main": w0.get("main", "Clear"),
        "description": w0.get("description", ""),
        "clouds": int((data.get("clouds") or {}).get("all", 0)),
        "rain_mm": float(rain_mm),
        "snow_mm": float(snow_mm),
    }
    _cache["data"] = result
    _cache["fetched_at"] = now
    print(f"   🌤 weather: temp={result['temp']} wind={result['windspeed']} "
          f"id={result['weather_id']} ({result['weather_main']}) "
          f"rain={result['rain_mm']}mm snow={result['snow_mm']}mm")
    return result