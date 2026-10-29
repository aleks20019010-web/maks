# brain/skills/weather/intent.py
# A5 (день 29): parse_intent — «погода» vs «погода на завтра».
#
# Не «хардкод в skill.py», а отдельный слой. Intent — это «что спросили»,
# источник (sources.py) — «откуда взять». Разные вещи.
#
# Сейчас: now / tomorrow. «Послезавтра» / «через день» — потом, если болит.
#
# A5: «завтра» / «на завтра» / «завтрашняя» → tomorrow.
# Остальное (включая «погода») → now.

import re


_TOMORROW_PATTERNS = (
    r'\bзавтра\b',
    r'\bна\s+завтра\b',
    r'\bзавтрашн\w*\b',
)


def parse_intent(text: str) -> str:
    """Вернуть "now" или "tomorrow".

    «погода» → now. «погода на завтра» → tomorrow.
    «какая завтра погода» → tomorrow.
    «погода послезавтра» → now (послезавтра не делаем — излишки).
    """
    if not text:
        return "now"
    t = text.lower().strip()
    for pattern in _TOMORROW_PATTERNS:
        if re.search(pattern, t):
            return "tomorrow"
    return "now"