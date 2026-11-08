# workers/tts/engine.py
# Yandex SpeechKit.
# Поддерживает speed и emotion. pitch игнорируется.
#
# Ключ и folder_id читаются из:
#   config/yandex_key.txt
#   config/yandex_folder.txt
#
# v6.2 (2026-11-05): rate НЕ передаётся в Яндекс.
#   synth всегда speed=1.0 (cfg.YANDEX_SPEED), cache_fingerprint
#   не зависит от rate. Причина: prosody даёт динамический rate
#   (+5%, -10%, ...), и если rate идёт в Яндекс — mp3 разный,
#   кэш не находит прогретое. Теперь rate применяет VLC
#   (player.set_rate) — mp3 всегда один, кэш всегда попадает,
#   тон сохраняется (VLC Scaletempo).

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
    """'+5%' → '1.05', '-10%' → '0.90'.

    v6.2: для Яндекса rate больше НЕ используется (synth всегда
    cfg.YANDEX_SPEED). Оставлено для совместимости/других вызовов.
    """
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
        # v6.2: rate в Яндекс НЕ передаём. mp3 всегда speed=cfg.YANDEX_SPEED
        # (обычно 1.0). rate применяет VLC (player.set_rate) — тон сохраняется,
        # кэш всегда попадает (fingerprint без rate).
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
                timeout=8,
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
        # v6.2: rate НЕ входит в fingerprint. mp3 всегда speed=cfg.YANDEX_SPEED.
        # prosody-ный rate применяет VLC, кэш от него не зависит.
        speed = getattr(cfg, 'YANDEX_SPEED', "1.0")
        emotion = emotion if emotion else cfg.YANDEX_EMOTION
        return f"yandex|{cfg.YANDEX_VOICE}|{speed}|{emotion}"