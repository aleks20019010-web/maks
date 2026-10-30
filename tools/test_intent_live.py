# tools/test_intent_live.py
# Живой прогон IntentResolver на тестовом наборе.
# Запуск: python tools/test_intent_live.py
#
# ВАЖНО: «расскажи про группу ария» и «что такое музыкальная волна» —
# это LLM_QUESTION (вопрос → LLM → ответ), не UNKNOWN. Ожидания поправлены.

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from brain.nlu.intent import resolve, _THRESHOLD
from brain.nlu.types import Intent


CASES = [
    ("навали арию", Intent.MUSIC_PLAY),
    ("вруби чего-нибудь эдакое", Intent.MUSIC_PLAY),
    ("хочу послушать музыку", Intent.MUSIC_PLAY),
    ("поставь киша", Intent.MUSIC_PLAY),
    ("что с погодой", Intent.WEATHER_QUERY),
    ("какая погода завтра", Intent.WEATHER_QUERY),
    ("расскажи про группу ария", Intent.LLM_QUESTION),
    ("почему ария так называется", Intent.LLM_QUESTION),
    ("что такое музыкальная волна", Intent.LLM_QUESTION),
    ("останови музыку", Intent.MUSIC_STOP),
]


def main():
    print(f"Порог: {_THRESHOLD}\n")
    ok = 0
    total = len(CASES)
    for text, expected in CASES:
        intent, conf = resolve(text)
        status = "OK " if intent == expected else "FAIL"
        if intent == expected:
            ok += 1
        print(f"{status}  {text!r:40} → {intent.value:20} "
              f"(conf={conf:.2f})  ожидалось {expected.value}")
    print(f"\nИтог: {ok}/{total}")


if __name__ == "__main__":
    main()