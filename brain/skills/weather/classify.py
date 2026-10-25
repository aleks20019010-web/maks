# brain/skills/weather/classify.py
# Классификация погоды OpenWeatherMap → переменные для правил и фраз.
#
# OWM weather.id:
#   2xx — гроза, 3xx — морось, 5xx — дождь, 6xx — снег,
#   7xx — туман, 800 — ясно, 801-804 — облачно.
#   511 — ледяной дождь, 611-613 — дождь со снегом, 906 — град.
#
# ВАЖНО: OWM часто отдаёт «light rain» с осадками < 1 мм.
#        Считаем дождём только если rain_mm >= RAIN_THRESHOLD_MM.
#        Аналогично снег.
#
# Подтипы дождя:
#   drizzle     — морось (rain_mm < 1)
#   rain        — обычный (1 <= rain_mm <= 5)
#   heavy_rain  — ливень (rain_mm > 5)

RAIN_THRESHOLD_MM = 1.0
SNOW_THRESHOLD_MM = 1.0
HEAVY_RAIN_MM = 5.0


def _temp_bucket(t):
    if t >= 30:
        return "hot"
    if t >= 18:
        return "warm"
    if t >= 5:
        return "mild"
    if t >= -20:
        return "cold"
    return "freeze"


def _precip_kind(wid, rain_mm=0.0, snow_mm=0.0):
    # Гроза — всегда гроза
    if 200 <= wid < 300:
        return "storm"

    # Морось / дождь (3xx, 5xx)
    if 300 <= wid < 400 or 500 <= wid < 600:
        if rain_mm < RAIN_THRESHOLD_MM:
            # Если осадков почти нет — не считаем дождём
            return "drizzle" if rain_mm > 0 else "cloudy"
        if rain_mm > HEAVY_RAIN_MM:
            return "heavy_rain"
        return "rain"

    # Ледяной дождь (511)
    if wid == 511:
        return "ice"

    # Дождь со снегом (611-613)
    if 611 <= wid <= 613:
        return "sleet"

    # Снег (6xx, кроме 611-613 и 906)
    if 600 <= wid < 700:
        if snow_mm >= SNOW_THRESHOLD_MM:
            return "snow"
        return "cloudy"

    # Туман / дымка
    if 700 <= wid < 800:
        return "fog"

    # Ясно
    if wid == 800:
        return "clear"

    # Облачно (801-804)
    if 801 <= wid <= 804:
        return "cloudy"

    # Град (906)
    if wid == 906:
        return "hail"

    return "cloudy"


def _wind_kind(ws):
    if ws < 12:
        return "calm"
    if ws < 30:
        return "light"
    if ws < 50:
        return "strong"
    return "very_strong"


def _sky_ru(sky):
    return {
        "clear": "ясно",
        "cloudy": "облачно",
        "fog": "туман",
        "drizzle": "мелкий дождь",
        "rain": "дождь",
        "heavy_rain": "ливень",
        "sleet": "дождь со снегом",
        "ice": "гололёд",
        "hail": "град",
        "snow": "снег",
        "storm": "гроза",
    }.get(sky, "облачно")


def _wind_ru(wind):
    return {
        "calm": "без ветра",
        "light": "небольшой ветер",
        "strong": "сильный ветер",
        "very_strong": "очень сильный ветер",
    }.get(wind, "ветер")


def _advice(temp, precip, wind):
    if precip == "storm":
        return ""
    if precip == "drizzle":
        return "Возьмите зонт, сэр"
    if precip == "rain":
        return "Возьмите зонт, сэр"
    if precip == "heavy_rain":
        return "Лучше переждать, сэр"
    if precip == "sleet":
        return "Осторожно, скользко, сэр"
    if precip == "ice":
        return "Осторожно, гололёд, сэр"
    if precip == "hail":
        return "Не выходите, сэр"
    if precip == "snow":
        return "Одевайтесь теплее, сэр"
    if wind in ("strong", "very_strong"):
        return "Одевайтесь плотнее, сэр"
    if temp >= 30:
        return "Пейте больше воды, сэр"
    if temp <= -15:
        return "Осторожно, сильный мороз"
    return ""


def classify(data):
    """Вернуть словарь переменных для фраз и правил."""
    if data is None:
        return {
            "temp": "?",
            "temp_num": 0,
            "temp_bucket": "unknown",
            "precip": "unknown",
            "wind": "unknown",
            "sky": "",
            "wind_ru": "",
            "advice": "",
        }

    temp = data["temp"]
    wid = data["weather_id"]
    ws = data["windspeed"]
    rain_mm = data.get("rain_mm", 0.0)
    snow_mm = data.get("snow_mm", 0.0)

    bucket = _temp_bucket(temp)
    precip = _precip_kind(wid, rain_mm, snow_mm)
    wind = _wind_kind(ws)

    return {
        "temp": str(temp),
        "temp_num": temp,
        "temp_bucket": bucket,
        "precip": precip,
        "wind": wind,
        "sky": _sky_ru(precip),
        "wind_ru": _wind_ru(wind),
        "advice": _advice(temp, precip, wind),
    }