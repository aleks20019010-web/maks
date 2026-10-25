# workers/tts/engine.py
# Yandex SpeechKit.
# Поддерживает speed и emotion. pitch игнорируется.
#
# Ключ и folder_id читаются из:
#   config/yandex_key.txt
#   config/yandex_folder.txt

import re
import requests

from shared import config as cfg


_KEY_CACHE = None
_FOLDER_CACHE = None


def _load_credentials():
    global _KEY_CACHE, _FOLDER_CACHE
    if _KEY_CACHE is None:
        try:
            with open(cfg.YANDEX_KEY_FILE, 'r', encoding='utf-8') as f:
                _KEY_CACHE = f.read().strip()
        except Exception as e:
            print(f"   ⚠ Не смог прочитать {cfg.YANDEX_KEY_FILE}: {e}")
            _KEY_CACHE = ""
    if _FOLDER_CACHE is None:
        try:
            with open(cfg.YANDEX_FOLDER_FILE, 'r', encoding='utf-8') as f:
                _FOLDER_CACHE = f.read().strip()
        except Exception as e:
            print(f"   ⚠ Не смог прочитать {cfg.YANDEX_FOLDER_FILE}: {e}")
            _FOLDER_CACHE = ""
    return _KEY_CACHE, _FOLDER_CACHE


def _rate_to_speed(rate):
    """'+5%' → '1.05', '-10%' → '0.90'."""
    if not rate:
        return getattr(cfg, 'YANDEX_SPEED', "1.0")
    try:
        pct = int(rate.replace('%', '').replace('+', '').strip())
        return f"{1.0 + pct / 100:.2f}"
    except Exception:
        return getattr(cfg, 'YANDEX_SPEED', "1.0")


class YandexEngine:
    name = "yandex"

    @staticmethod
    def _preprocess(text):
        text = re.sub(r',(\s*)сэр\b', r'...\1сэр', text)
        text = re.sub(r';(\s*)', r'... ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text

    def synth(self, text, out_path, rate=None, pitch=None, emotion=None):
        key, folder = _load_credentials()
        if not key or not folder:
            print("   ⚠ Yandex: нет ключа или folder_id")
            return False

        text = self._preprocess(text)
        speed = _rate_to_speed(rate if rate else cfg.YANDEX_SPEED)
        voice = cfg.YANDEX_VOICE
        emotion = emotion if emotion else cfg.YANDEX_EMOTION

        data = {
            'text': text,
            'lang': 'ru-RU',
            'voice': voice,
            'emotion': emotion,
            'speed': speed,
            'format': 'mp3',
            'folderId': folder,
        }
        headers = {'Authorization': f'Api-Key {key}'}

        try:
            r = requests.post(
                'https://tts.api.cloud.yandex.net/speech/v1/tts:synthesize',
                headers=headers,
                data=data,
                timeout=30,
            )
            if r.status_code != 200:
                print(f"   ⚠ Yandex HTTP {r.status_code}: {r.text[:200]}")
                return False
            if len(r.content) < 1024:
                print(f"   ⚠ Yandex: слишком маленький ответ ({len(r.content)} b)")
                return False
            with open(out_path, 'wb') as f:
                f.write(r.content)
            return True
        except Exception as e:
            print(f"   ⚠ Yandex synth: {e}")
            return False

    def cache_fingerprint(self, rate=None, pitch=None, emotion=None):
        speed = _rate_to_speed(rate if rate else cfg.YANDEX_SPEED)
        emotion = emotion if emotion else cfg.YANDEX_EMOTION
        return f"yandex|{cfg.YANDEX_VOICE}|{speed}|{emotion}"