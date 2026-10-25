"""
shared/ipc.py — файловая шина общения между процессами.

Работает через файлы в data/ipc/<topic>/<uuid>.json.
Просто, надёжно, работает на Windows без Manager.

Использование:
    from shared import ipc
    ipc.put(protocol.BRAIN_INBOX, msg)
    msg = ipc.get(protocol.TTS_INBOX, timeout=0.5)

Очистка:
    ipc.init(clean=True) — вызывается ТОЛЬКО из supervisor при старте.
    Удаляет все старые .json из всех топиков, чтобы мусор прошлых
    сессий не читался как команды.
"""

import glob
import os
import time
import uuid
from typing import Optional

from shared.protocol import Message

IPC_DIR = "data/ipc"


def _topic_dir(topic: str) -> str:
    path = os.path.join(IPC_DIR, topic)
    os.makedirs(path, exist_ok=True)
    return path


def put(topic: str, msg) -> None:
    """Положить сообщение в очередь (файл)."""
    folder = _topic_dir(topic)
    fname = f"{uuid.uuid4().hex}.json"
    path = os.path.join(folder, fname)

    if isinstance(msg, Message):
        raw = msg.to_json()
    elif isinstance(msg, str):
        raw = msg
    else:
        raise ValueError(f"ipc.put: ожидалось Message или str, получено {type(msg)}")

    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(raw)
    os.replace(tmp, path)


def get(topic: str, timeout: float = 1.0) -> Optional[Message]:
    """Достать самое старое сообщение. None при таймауте."""
    folder = _topic_dir(topic)
    start = time.time()

    while time.time() - start < timeout:
        try:
            files = sorted(glob.glob(os.path.join(folder, "*.json")))
        except Exception:
            files = []

        if files:
            oldest = files[0]
            try:
                with open(oldest, "r", encoding="utf-8") as f:
                    raw = f.read()
                # Сначала парсим — если битый JSON, файл остаётся для разбора
                msg = Message.from_json(raw)
                try:
                    os.remove(oldest)
                except Exception:
                    pass
                return msg
            except Exception:
                # Битый JSON — оставляем файл, чтобы можно было разобрать вручную
                try:
                    os.rename(oldest, oldest + ".bad")
                except Exception:
                    try:
                        os.remove(oldest)
                    except Exception:
                        pass
                continue

        time.sleep(0.05)

    return None


def init(clean: bool = False):
    """Создать папку data/ipc/.

    clean=True — удалить все старые сообщения из всех топиков.
    Вызывается ТОЛЬКО из supervisor при старте, до запуска воркеров.
    """
    os.makedirs(IPC_DIR, exist_ok=True)

    if not clean:
        return

    removed = 0
    for entry in os.listdir(IPC_DIR):
        topic_path = os.path.join(IPC_DIR, entry)
        if not os.path.isdir(topic_path):
            continue
        for pattern in ("*.json", "*.tmp", "*.json.tmp"):
            for f in glob.glob(os.path.join(topic_path, pattern)):
                try:
                    os.remove(f)
                    removed += 1
                except Exception:
                    pass

    return removed