# brain/datetime_parser.py
# Парсинг дат и времени из текста: «29 октября в 10:00», «завтра в 9:00»,
# «через 10 минут», «двадцать третьего октября в девять ноль-ноль»,
# «в 9 часов», «в девять часов».
#
# Возвращает:
#   parse_when(text) → (due_datetime, clean_text) | None
#   parse_delay(text) → (delay_minutes, clean_text) | None

import re
from datetime import datetime, timedelta


_MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}

_DAY_WORDS = {
    "первого": 1, "второго": 2, "третьего": 3, "четвёртого": 4, "пятого": 5,
    "шестого": 6, "седьмого": 7, "восьмого": 8, "девятого": 9, "десятого": 10,
    "одиннадцатого": 11, "двенадцатого": 12, "тринадцатого": 13, "четырнадцатого": 14,
    "пятнадцатого": 15, "шестнадцатого": 16, "семнадцатого": 17, "восемнадцатого": 18,
    "девятнадцатого": 19, "двадцатого": 20,
    "двадцать первого": 21, "двадцать второго": 22,
    "двадцать третьего": 23, "двадцать четвёртого": 24, "двадцать пятого": 25,
    "двадцать шестого": 26, "двадцать седьмого": 27, "двадцать восьмого": 28,
    "двадцать девятого": 29, "тридцатого": 30, "тридцать первого": 31,
}

_HOUR_WORDS = {
    "ноль": 0, "один": 1, "два": 2, "три": 3, "четыре": 4, "пять": 5,
    "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10,
    "одиннадцать": 11, "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14,
    "пятнадцать": 15, "шестнадцать": 16, "семнадцать": 17, "восемнадцать": 18,
    "девятнадцать": 19, "двадцать": 20,
    "двадцать один": 21, "двадцать два": 22, "двадцать три": 23,
}

_NUM_WORDS = {
    "одну": 1, "один": 1, "одна": 1,
    "две": 2, "два": 2, "три": 3, "четыре": 4, "пять": 5,
    "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10,
    "пятнадцать": 15, "двадцать": 20, "тридцать": 30,
}


def _parse_hour(s: str):
    """«девять» → 9. «09» → 9."""
    s = s.strip().lower()
    if s.isdigit():
        return int(s)
    return _HOUR_WORDS.get(s)


def _extract_time(text: str):
    """Найти «в HH:MM» / «в HH часов» / «в H-слово ноль-ноль» /
    «в H-слово часов» / «в HH» / «в H-слово».

    Возвращает ((hour, minute), rest_text) или (None, text).
    """
    t = text

    # 1) «в HH:MM»
    m = re.search(r'\bв\s+(\d{1,2}):(\d{2})\b', t)
    if m:
        h, mm = int(m.group(1)), int(m.group(2))
        rest = (t[:m.start()] + " " + t[m.end():]).strip()
        return (h, mm), " ".join(rest.split())

    # 2) «в HH часов»
    m = re.search(r'\bв\s+(\d{1,2})\s+час(?:ов|а|)\b', t)
    if m:
        h = int(m.group(1))
        rest = (t[:m.start()] + " " + t[m.end():]).strip()
        return (h, 0), " ".join(rest.split())

    # 3) «в H-слово часов»
    m = re.search(r'\bв\s+((?:двадцать\s+)?[а-яё]+)\s+час(?:ов|а|)\b', t)
    if m:
        h = _parse_hour(m.group(1).strip())
        if h is not None:
            rest = (t[:m.start()] + " " + t[m.end():]).strip()
            return (h, 0), " ".join(rest.split())

    # 4) «в H-слово ноль-ноль»
    m = re.search(r'\bв\s+([а-яё]+)\s+ноль[-\s]?ноль\b', t)
    if m:
        h = _parse_hour(m.group(1))
        if h is not None:
            rest = (t[:m.start()] + " " + t[m.end():]).strip()
            return (h, 0), " ".join(rest.split())

    # 5) «в HH» (число, без минут)
    m = re.search(r'\bв\s+(\d{1,2})\b', t)
    if m:
        h = int(m.group(1))
        rest = (t[:m.start()] + " " + t[m.end():]).strip()
        return (h, 0), " ".join(rest.split())

    # 6) «в H-слово»
    m = re.search(r'\bв\s+((?:двадцать\s+)?[а-яё]+)\b', t)
    if m:
        hour_str = m.group(1).strip()
        h = _parse_hour(hour_str)
        if h is not None:
            rest = (t[:m.start()] + " " + t[m.end():]).strip()
            return (h, 0), " ".join(rest.split())

    return None, t


def _extract_date(text: str):
    """Найти «D месяца» (цифры или слова).

    Возвращает ((day, month), rest_text) или (None, text).
    """
    t = text

    # 1) «29 октября» — цифры
    m = re.search(r'\b(\d{1,2})\s+([а-яё]+)\b', t)
    if m:
        day = int(m.group(1))
        month = _MONTHS.get(m.group(2))
        if month:
            rest = (t[:m.start()] + " " + t[m.end():]).strip()
            return (day, month), " ".join(rest.split())

    # 2) «двадцать третьего октября» — слова (сначала длинные)
    for day_str in sorted(_DAY_WORDS, key=len, reverse=True):
        m = re.search(r'\b' + re.escape(day_str) + r'\s+([а-яё]+)\b', t)
        if m:
            month = _MONTHS.get(m.group(1))
            if month:
                rest = (t[:m.start()] + " " + t[m.end():]).strip()
                return (_DAY_WORDS[day_str], month), " ".join(rest.split())

    return None, t


def parse_delay(text: str):
    """«через 10 минут X» / «через одну минуту X» / «через 2 часа X».

    Возвращает (delay_minutes, clean_text) или None.
    """
    t = text.strip()
    m = re.match(
        r'^через\s+(\d+|[а-яё\-]+)\s*(минут|мин|час|ч)\w*\s+(.+)$',
        t
    )
    if not m:
        return None
    raw = m.group(1).strip()
    unit = m.group(2)
    clean = m.group(3).strip()

    if raw.isdigit():
        value = int(raw)
    else:
        value = _NUM_WORDS.get(raw)
    if not value:
        return None
    delay = value * 60 if unit.startswith('час') or unit == 'ч' else value
    return delay, clean


def parse_when(text: str):
    """Парсит дату/время. Возвращает (due_datetime, clean_text) или None.

    Поддерживает:
      «29 октября в 10:00 X»
      «двадцать третьего октября в девять ноль-ноль X»
      «29 октября X» → 09:00
      «завтра в 9:00 X»
      «послезавтра в 15:30 X»
      «сегодня в 18:00 X»
      «в 10:00 X» → сегодня/завтра
      «в 9 часов X» → сегодня/завтра
      «в девять часов X» → сегодня/завтра
    """
    now = datetime.now()
    t = text.strip()

    # 1) «завтра/послезавтра/сегодня ...»
    m = re.match(r'^(завтра|послезавтра|сегодня)\s+(.+)$', t)
    if m:
        word = m.group(1)
        rest = m.group(2).strip()
        time_parsed, clean = _extract_time(rest)
        if word == "завтра":
            base = now + timedelta(days=1)
        elif word == "послезавтра":
            base = now + timedelta(days=2)
        else:
            base = now
        if time_parsed:
            hour, minute = time_parsed
            due = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
        else:
            due = base.replace(hour=9, minute=0, second=0, microsecond=0)
        return due, clean

    # 2) время + дата (в любом порядке)
    time_parsed, rest = _extract_time(t)
    date_parsed, clean = _extract_date(rest)

    if date_parsed and time_parsed:
        day, month = date_parsed
        hour, minute = time_parsed
        year = now.year
        try:
            due = datetime(year, month, day, hour, minute)
        except ValueError:
            return None
        if due < now:
            due = due.replace(year=year + 1)
        return due, clean

    if date_parsed:
        day, month = date_parsed
        year = now.year
        try:
            due = datetime(year, month, day, 9, 0)
        except ValueError:
            return None
        if due < now:
            due = due.replace(year=year + 1)
        return due, clean

    if time_parsed:
        hour, minute = time_parsed
        due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if due < now:
            due = due + timedelta(days=1)
        return due, clean

    return None