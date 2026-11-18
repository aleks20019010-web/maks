# workers/tts/engine.py
# Yandex SpeechKit.
# Поддерживает speed и emotion. pitch игнорируется.

import re
import requests

from shared import config as cfg
from shared.log import log


_KEY_CACHE = None
_FOLDER_CACHE = None


def _load_credentials():
    global _KEY_CACHE, _FOLDER_CACHE
    if _KEY_CACHE is None:
        try:
            with open(cfg.YANDEX_KEY_FILE, 'r', encoding='utf-8') as f:
                _KEY_CACHE = f.read().strip()
        except Exception as e:
            log(f"tts: не смог прочитать {cfg.YANDEX_KEY_FILE}: {e}",
                tag="TTS", level="WARN")
            _KEY_CACHE = ""
    if _FOLDER_CACHE is None:
        try:
            with open(cfg.YANDEX_FOLDER_FILE, 'r', encoding='utf-8') as f:
                _FOLDER_CACHE = f.read().strip()
        except Exception as e:
            log(f"tts: не смог прочитать {cfg.YANDEX_FOLDER_FILE}: {e}",
                tag="TTS", level="WARN")
            _FOLDER_CACHE = ""
    return _KEY_CACHE, _FOLDER_CACHE


def _rate_to_speed(rate):
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
            log("tts: Yandex — нет ключа или folder_id", tag="TTS", level="WARN")
            return False

        text = self._preprocess(text)
        speed = getattr(cfg, 'YANDEX_SPEED', "1.0")
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
                timeout=(2.5, 4.0),
            )
            if r.status_code != 200:
                log(f"tts: Yandex HTTP {r.status_code}: {r.text[:200]}",
                    tag="TTS", level="WARN")
                return False
            if len(r.content) < 1024:
                log(f"tts: Yandex — слишком маленький ответ ({len(r.content)} b)",
                    tag="TTS", level="WARN")
                return False
            with open(out_path, 'wb') as f:
                f.write(r.content)
            return True
        except Exception as e:
            log(f"tts: Yandex synth error: {e}", tag="TTS", level="WARN")
            return False

    def cache_fingerprint(self, rate=None, pitch=None, emotion=None):
        speed = getattr(cfg, 'YANDEX_SPEED', "1.0")
        emotion = emotion if emotion else cfg.YANDEX_EMOTION
        return f"yandex|{cfg.YANDEX_VOICE}|{speed}|{emotion}"