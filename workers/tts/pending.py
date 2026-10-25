# workers/tts/pending.py
# Pending — фразы, которые не удалось синтезировать.

import os
import json
import time

from shared import config as cfg


PENDING_FILE = os.path.join(cfg.DATA_DIR, 'pending_phrases.json')
os.makedirs(os.path.dirname(PENDING_FILE), exist_ok=True)


def load() -> list:
    if not os.path.exists(PENDING_FILE):
        return []
    try:
        with open(PENDING_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save(items: list):
    try:
        with open(PENDING_FILE, 'w', encoding='utf-8') as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠ tts: не могу сохранить pending: {e}")


def add(text, rate=None, pitch=None, emotion=None):
    if not text:
        return
    items = load()
    key = (text, rate, pitch, emotion)
    for it in items:
        if (it.get('text'), it.get('rate'), it.get('pitch'), it.get('emotion')) == key:
            return
    items.append({
        "text": text,
        "rate": rate,
        "pitch": pitch,
        "emotion": emotion,
        "added_at": time.time(),
    })
    save(items)
    print(f"   📝 pending: +1 фраза (всего: {len(items)})")