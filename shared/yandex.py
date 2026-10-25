"""
shared/yandex.py — клиент Яндекс.Музыки.

Один клиент на процесс brain. Инициализируется в brain/main.py при старте.
Токен — config/token.txt.

Почему не в state: state — данные музыки (playlist, prefetched, ...).
Клиент — это сетевое подключение. Разные вещи.
"""

import os
import threading

from shared import config as cfg


_client = None
_token = None
_lock = threading.Lock()


def init() -> bool:
    """Инициализировать клиент. True — успех, False — нет токена/ошибка.

    Вызывается из brain/main.py при старте.
    """
    global _client, _token

    with _lock:
        if _client is not None:
            return True

        if not os.path.exists(cfg.TOKEN_FILE):
            print(f"❌ yandex: не найден {cfg.TOKEN_FILE}")
            return False

        try:
            with open(cfg.TOKEN_FILE, "r", encoding="utf-8") as f:
                token = f.read().strip()
        except Exception as e:
            print(f"❌ yandex: ошибка чтения токена: {e}")
            return False

        if not token:
            print("❌ yandex: пустой токен")
            return False

        try:
            from yandex_music import Client
            client = Client(token).init()
            account = client.me.account.display_name
            print(f"🎵 yandex: подключён ({account})")
        except Exception as e:
            print(f"❌ yandex: ошибка подключения: {e}")
            return False

        _client = client
        _token = token
        return True


def get_client():
    """Вернуть клиент. None — если не инициализирован."""
    return _client


def get_token():
    """Вернуть токен. None — если не инициализирован."""
    return _token


def is_ready() -> bool:
    return _client is not None