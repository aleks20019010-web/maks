# brain/nlu/normalizer.py
# TextNormalizer: очистка текста для NLU.
#
# Что делает:
#   - lowercase
#   - убирает STOP_PREFIXES («макс», «слушаю», «готов», «на связи»)
#   - убирает _FILLERS («давай», «пожалуйста», «ну», «вот», «как бы», «типа»,
#     «эй», «слушай») — ОБЩИЙ шум, не музыкальный
#   - убирает пунктуацию
#   - нормализует пробелы
#
# Что НЕ делает:
#   - не извлекает параметры (это EntityResolver)
#   - не определяет intent (это IntentResolver)
#   - НЕ приводит числа словами → цифрами. Это дело controls.parse_volume
#     (NUMBER_WORDS + VOLUME_PATTERNS). Дублировать не надо.
#   - не меняет смысл
#
# ПОЧЕМУ «давай» ЗДЕСЬ, А НЕ В NOISE_WORDS:
#   «давай» — общий шум (не только музыка). Если оставить его в normalizer,
#   каждый скил должен сам его вычищать — дублирование. Убираем один раз.
#
# STOP_PREFIXES — из shared/music_words.py (один источник правды).
# Если STOP_PREFIXES пуст (config/music_words.json нет) — не падаем.

import re

from shared.music_words import STOP_PREFIXES


_PUNCT_RE = re.compile(r'[^\w\s]', re.UNICODE)
_SPACES_RE = re.compile(r'\s+', re.UNICODE)

# Общий шум (не музыкальный). Убираем один раз здесь.
_FILLERS = re.compile(
    r"\b(пожалуйста|будь добр|будь добра|макс|эй|слушай|ну|вот|как бы|типа|давай)\b",
    re.IGNORECASE,
)


def _strip_stop_prefixes(text: str) -> str:
    """Убрать STOP_PREFIXES из начала (как _strip_stop_prefix в music_words)."""
    if not text:
        return text
    q = text
    changed = True
    while changed:
        changed = False
        for prefix in STOP_PREFIXES:
            if q.startswith(prefix):
                q = q[len(prefix):].strip(' ,.!?-')
                changed = True
                break
    return q


def normalize(text: str) -> str:
    """Очистить текст для NLU.

    'Макс, навали арию!' → 'навали арию'
    '  Слушаю вас, включи рок  ' → 'включи рок'
    'давай послушаем киша' → 'послушаем киша'
    'громкость пятьдесят' → 'громкость пятьдесят'  (числа — не здесь)
    '' → ''
    """
    if not text:
        return ""
    t = text.lower().strip()
    # Убрать STOP_PREFIXES до пунктуации (чтобы «макс,» → «макс»)
    t = _strip_stop_prefixes(t)
    # Убрать пунктуацию
    t = _PUNCT_RE.sub(' ', t)
    # Убрать _FILLERS (общий шум)
    t = _FILLERS.sub(' ', t)
    # Нормализовать пробелы
    t = _SPACES_RE.sub(' ', t).strip()
    return t