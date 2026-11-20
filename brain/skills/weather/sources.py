# brain/skills/weather/sources.py
# OpenWeatherMap API. Ключ — config/weather_key.txt. Кэш 15 минут.

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from shared import config as cfg
from shared.log import log


WEATHER_CONFIG_FILE = os.path.join(cfg.BASE_DIR, 'config', 'weather.json')
WEATHER_KEY_FILE = os.path.join(cfg.BASE_DIR, 'config', 'weather_key.txt')

_HTTP_TIMEOUT_SEC = 4.0
_MAX_ATTEMPTS = 2
_NET_COOLDOWN_SEC = 30.0
_net_blocked_until = 0.0

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
        log(f"weather: не найден {WEATHER_CONFIG_FILE}", tag="BRAIN", level="WARN")
        return {"lat": 58.3875, "lon": 33.9119, "city": "Боровичи"}
    try:
        with open(WEATHER_CONFIG_FILE, 'r', encoding='utf-8-sig') as f:
            return json.load(f)
    except Exception as e:
        log(f"weather: ошибка чтения {WEATHER_CONFIG_FILE}: {e}", tag="BRAIN", level="WARN")
        return {"lat": 58.3875, "lon": 33.9119, "city": "Боровичи"}


def _load_key():
    if not os.path.exists(WEATHER_KEY_FILE):
        log(f"weather: не найден {WEATHER_KEY_FILE}", tag="BRAIN", level="WARN")
        return ""
    try:
        with open(WEATHER_KEY_FILE, 'r', encoding='utf-8-sig') as f:
            return f.read().strip()
    except Exception as e:
        log(f"weather: ошибка чтения {WEATHER_KEY_FILE}: {e}", tag="BRAIN", level="WARN")
        return ""


def _parse_current(data: dict) -> dict:
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

    noon = datetime.combine(tomorrow, datetime.min.time()).replace(hour=12)
    best = min(tomorrow_items, key=lambda x: abs((x[0] - noon).total_seconds()))
    return best[1]


def _http_get_json(url: str, label: str) -> dict | None:
    global _net_blocked_until
    if time.time() < _net_blocked_until:
        return None

    for attempt in range(_MAX_ATTEMPTS):
        try:
            t0 = time.time()
            with urllib.request.urlopen(url, timeout=_HTTP_TIMEOUT_SEC) as resp:
                raw = resp.read().decode('utf-8')
            dt = time.time() - t0
            data = json.loads(raw)
            _net_blocked_until = 0.0
            log(f"weather: {label} ответ за {dt:.2f}с", tag="BRAIN", level="DEBUG")
            return data
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            _net_blocked_until = time.time() + _NET_COOLDOWN_SEC
            log(f"weather: {label} нет связи ({type(e).__name__}) — пауза {_NET_COOLDOWN_SEC:.0f}с",
                tag="BRAIN", level="WARN")
            return None
        except Exception as e:
            log(f"weather: {label} попытка {attempt + 1}/{_MAX_ATTEMPTS} упала: {type(e).__name__}: {e}",
                tag="BRAIN", level="WARN")
            if attempt + 1 < _MAX_ATTEMPTS:
                time.sleep(0.3)

    return None


def _fetch_current() -> dict | None:
    cfgw = _load_config()
    key = _load_key()
    if not key:
        log("weather: нет API-ключа", tag="BRAIN", level="WARN")
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

    data = _http_get_json(url, "current")
    if data is None:
        return _cache["data"]

    result = _parse_current(data)
    _cache["data"] = result
    _cache["fetched_at"] = now
    log(f"weather: temp={result['temp']} wind={result['windspeed']} "
        f"id={result['weather_id']} ({result['weather_main']}) "
        f"rain={result['rain_mm']}mm snow={result['snow_mm']}mm",
        tag="BRAIN", level="DEBUG")
    return result


def _fetch_forecast_tomorrow() -> dict | None:
    cfgw = _load_config()
    key = _load_key()
    if not key:
        log("weather: нет API-ключа", tag="BRAIN", level="WARN")
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

    data = _http_get_json(url, "forecast")
    if data is None:
        return _forecast_cache["data"]

    items = data.get("list") or []
    slot = _pick_tomorrow_noon(items)
    if slot is None:
        log("weather: forecast — нет слота на завтра", tag="BRAIN", level="WARN")
        return _forecast_cache["data"]

    result = _parse_forecast_slot(slot)
    _forecast_cache["data"] = result
    _forecast_cache["fetched_at"] = now
    log(f"weather tomorrow: temp={result['temp']} wind={result['windspeed']} "
        f"id={result['weather_id']} ({result['weather_main']})",
        tag="BRAIN", level="DEBUG")
    return result


def fetch_weather(when: str = "now") -> dict | None:
    if when == "tomorrow":
        return _fetch_forecast_tomorrow()
    return _fetch_current()