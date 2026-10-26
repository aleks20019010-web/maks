# tools/test_llm.py
# Проверка: поднимается ли клиент AITUNNEL и отвечает ли модель.
#
# Запуск:
#   python tools/test_llm.py
#
# FIX (день 28): ключ читается из cfg.CONFIG_DIR, не хардкод D:/Maks.
#   sys.path — от BASE_DIR, чтобы запускалось из любой папки.

import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from openai import OpenAI
from shared import config as cfg


key_file = cfg.CONFIG_DIR / "llm_key.txt"
with open(key_file, "r", encoding="utf-8") as f:
    key = f.read().strip()

client = OpenAI(api_key=key, base_url="https://api.aitunnel.ru/v1/")

try:
    resp = client.chat.completions.create(
        model="deepseek-v4-flash-0731",
        messages=[{"role": "user", "content": "Привет! Ответь одним словом."}],
        max_tokens=100,
    )
    print("=== RESPONSE ===")
    print("finish_reason:", resp.choices[0].finish_reason)
    print("content:", repr(resp.choices[0].message.content))
    print("usage:", resp.usage)
except Exception as e:
    print("FAIL:", e)