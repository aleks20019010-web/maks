"""
shared/yandex.py — клиент Яндекс.Музыки.

Один клиент на процесс brain. Инициализируется в brain/main.py при старте.
Токен — config/token.txt.

Почему не в state: state — данные музыки (playlist, prefetched, ...).
Клиент — это сетевое подключение. Разные вещи.

FIX: cfg.TOKEN_FILE теперь Path (из shared/config.py), не str.
Используем Path.exists() и open() — работают с Path.
log вместо print — единообразие с другими воркерами.
"""

import threading

from shared import config as cfg
from shared.log import log


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

        if not cfg.TOKEN_FILE.exists():
            log(f"yandex: не найден {cfg.TOKEN_FILE}", tag="MUSIC", level="ERROR")
            return False

        try:
            with open(cfg.TOKEN_FILE, "r", encoding="utf-8") as f:
                token = f.read().strip()
        except Exception as e:
            log(f"yandex: ошибка чтения токена: {e}", tag="MUSIC", level="ERROR")
            return False

        if not token:
            log("yandex: пустой токен", tag="MUSIC", level="ERROR")
            return False

        try:
            from yandex_music import Client
            client = Client(token).init()
            account = client.me.account.display_name
            log(f"yandex: подключён ({account})", tag="MUSIC")
        except Exception as e:
            log(f"yandex: ошибка подключения: {e}", tag="MUSIC", level="ERROR")
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