# brain/skills/weather/sources.py
# OpenWeatherMap API. Ключ — config/weather_key.txt
# Кэш 15 минут — чтобы не дёргать на каждый запрос.
#
# A5 (день 29): два эндпоинта — current (/weather) и forecast (/forecast).
#   fetch_weather(when="now")      → текущая (как было).
#   fetch_weather(when="tomorrow") → прогноз на завтра, слот 12:00.
# Два кэша: _cache (current) и _forecast_cache (tomorrow).
#   Нельзя переиспользовать один — разные форматы, разное время жизни.
#
# OWM /forecast — 5 дней / 3 часа. Список list[]. Ищем запись с dt,
#   где дата = завтра, час = 12:00 (ближайшая). Если нет — первая завтрашняя.

import json
import os
import time
import urllib.request
import urllib.parse
from datetime import datetime, timedelta

from shared import config as cfg


WEATHER_CONFIG_FILE = os.path.join(cfg.BASE_DIR, 'config', 'weather.json')
WEATHER_KEY_FILE = os.path.join(cfg.BASE_DIR, 'config', 'weather_key.txt')

_cache = {
    "data": None,
    "fetched_at": 0.0,
}

_forecast_cache = {
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


def _parse_current(data: dict) -> dict:
    """Разобрать /weather (текущая)."""
    main = data.get("main") or {}
    wind = data.get("wind") or {}
    weather_list = data.get("weather") or [{}]
    w0 = weather_list[0] if weather_list else {}

    rain_block = data.get("rain") or {}
    snow_block = data.get("snow") or {}
    rain_mm = rain_block.get("1h", rain_block.get("3h", 0.0)) or 0.0
    snow_mm = snow_block.get("1h", snow_block.get("3h", 0.0)) or 0.0

    return {
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


def _parse_forecast_slot(item: dict) -> dict:
    """Разобрать один слот /forecast (3 часа). Формат как /weather, но в item."""
    main = item.get("main") or {}
    wind = item.get("wind") or {}
    weather_list = item.get("weather") or [{}]
    w0 = weather_list[0] if weather_list else {}

    rain_block = item.get("rain") or {}
    snow_block = item.get("snow") or {}
    rain_mm = rain_block.get("3h", rain_block.get("1h", 0.0)) or 0.0
    snow_mm = snow_block.get("3h", snow_block.get("1h", 0.0)) or 0.0

    return {
        "temp": int(round(main.get("temp", 0))),
        "feels_like": int(round(main.get("feels_like", 0))),
        "windspeed": int(round(wind.get("speed", 0) * 3.6)),
        "weather_id": int(w0.get("id", 800)),
        "weather_main": w0.get("main", "Clear"),
        "description": w0.get("description", ""),
        "clouds": int((item.get("clouds") or {}).get("all", 0)),
        "rain_mm": float(rain_mm),
        "snow_mm": float(snow_mm),
    }


def _pick_tomorrow_noon(items: list) -> dict | None:
    """Из списка /forecast выбрать слот: завтра 12:00 (ближайший)."""
    if not items:
        return None
    tomorrow = (datetime.now() + timedelta(days=1)).date()

    tomorrow_items = []
    for item in items:
        dt_txt = item.get("dt_txt", "")
        try:
            dt = datetime.strptime(dt_txt, "%Y-%m-%d %H:%M:%S")
        except Exception:
            continue
        if dt.date() == tomorrow:
            tomorrow_items.append((dt, item))

    if not tomorrow_items:
        return None

    # Ближайший к 12:00
    noon = datetime.combine(tomorrow, datetime.min.time()).replace(hour=12)
    best = min(tomorrow_items, key=lambda x: abs((x[0] - noon).total_seconds()))
    return best[1]


def _fetch_current() -> dict | None:
    """Текущая погода. Кэш 15 минут."""
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

    result = _parse_current(data)
    _cache["data"] = result
    _cache["fetched_at"] = now
    print(f"   🌤 weather: temp={result['temp']} wind={result['windspeed']} "
          f"id={result['weather_id']} ({result['weather_main']}) "
          f"rain={result['rain_mm']}mm snow={result['snow_mm']}mm")
    return result


def _fetch_forecast_tomorrow() -> dict | None:
    """Прогноз на завтра (слот 12:00). Кэш 15 минут."""
    cfgw = _load_config()
    key = _load_key()
    if not key:
        print("   ⚠ weather: нет API-ключа")
        return None

    now = time.time()
    interval = cfgw.get("update_interval_sec", 900)

    if _forecast_cache["data"] is not None and (now - _forecast_cache["fetched_at"]) < interval:
        return _forecast_cache["data"]

    params = {
        "lat": cfgw["lat"],
        "lon": cfgw["lon"],
        "appid": key,
        "units": "metric",
        "lang": "ru",
    }
    url = "https://api.openweathermap.org/data/2.5/forecast?" + urllib.parse.urlencode(params)

    data = None
    for attempt in range(3):
        try:
            print(f"   🌤 weather: forecast запрос #{attempt+1}")
            t0 = time.time()
            with urllib.request.urlopen(url, timeout=20) as resp:
                raw = resp.read().decode('utf-8')
            dt = time.time() - t0
            data = json.loads(raw)
            print(f"   🌤 weather: forecast ответ за {dt:.2f}с")
            break
        except Exception as e:
            print(f"   ⚠ weather: forecast попытка {attempt+1}/3 упала: {type(e).__name__}: {e}")
            if attempt < 2:
                time.sleep(1.0)

    if data is None:
        print("   ⚠ weather: forecast все попытки провалились")
        return _forecast_cache["data"]

    items = data.get("list") or []
    slot = _pick_tomorrow_noon(items)
    if slot is None:
        print("   ⚠ weather: forecast — нет слота на завтра")
        return _forecast_cache["data"]

    result = _parse_forecast_slot(slot)
    _forecast_cache["data"] = result
    _forecast_cache["fetched_at"] = now
    print(f"   🌤 weather tomorrow: temp={result['temp']} wind={result['windspeed']} "
          f"id={result['weather_id']} ({result['weather_main']})")
    return result


def fetch_weather(when: str = "now") -> dict | None:
    """Вернуть dict с погодой. when: "now" | "tomorrow". Кэш 15 минут."""
    if when == "tomorrow":
        return _fetch_forecast_tomorrow()
    return _fetch_current()