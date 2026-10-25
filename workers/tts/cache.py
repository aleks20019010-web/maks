# workers/tts/cache.py
# Кэш TTS-файлов. Ключ — md5(fingerprint|text).

import os
import hashlib
import shutil

from shared import config as cfg


CACHE_DIR = cfg.CACHE_DIR
os.makedirs(CACHE_DIR, exist_ok=True)


def cache_key(engine, text, rate=None, pitch=None, emotion=None):
    fingerprint = engine.cache_fingerprint(rate, pitch, emotion)
    raw = f"{fingerprint}|{text}"
    return hashlib.md5(raw.encode('utf-8')).hexdigest()[:16]


def cache_path(engine, text, rate=None, pitch=None, emotion=None):
    return os.path.join(CACHE_DIR, f"{cache_key(engine, text, rate, pitch, emotion)}.mp3")


def cache_hit(engine, text, out_path, rate=None, pitch=None, emotion=None):
    cached = cache_path(engine, text, rate, pitch, emotion)
    if os.path.exists(cached) and os.path.getsize(cached) > 1024:
        try:
            shutil.copy(cached, out_path)
            return True
        except Exception:
            return False
    return False


def cache_save(engine, text, out_path, rate=None, pitch=None, emotion=None):
    try:
        shutil.copy(out_path, cache_path(engine, text, rate, pitch, emotion))
    except Exception:
        pass