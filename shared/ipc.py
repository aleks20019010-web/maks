"""
shared/ipc.py — файловая шина общения между процессами.

Работает через файлы в data/ipc/<topic>/<timestamp_ns>_<uuid>.json.
Просто, надёжно, работает на Windows без Manager.

FIX: имя файла с timestamp_ns вместо uuid.hex.
Раньше: sorted(glob("*.json")) по UUID — НЕ по времени, порядок сообщений
не гарантирован. Для музыки (play → stop → next) это критично.
Теперь: f"{time.time_ns()}_{uuid.uuid4().hex}.json" — сортировка по имени
= сортировка по времени. Старое сообщение всегда первым.

FIX: IPC_DIR — от cfg.DATA_DIR, а не относительный "data/ipc".
Раньше: работал только если cwd = D:\\Maks. Если воркер запущен
из другой папки — IPC ломался. Теперь — абсолютный путь от BASE_DIR.

Использование:
    from shared import ipc
    ipc.put(protocol.BRAIN_INBOX, msg)
    msg = ipc.get(protocol.TTS_INBOX, timeout=0.5)

Очистка:
    ipc.init(clean=True) — вызывается ТОЛЬКО из supervisor при старте.
"""

import glob
import os
import time
import uuid
from typing import Optional

from shared import config as cfg
from shared.protocol import Message

# FIX: абсолютный путь от BASE_DIR, не относительный "data/ipc"
IPC_DIR = str(cfg.DATA_DIR / "ipc")


def _topic_dir(topic: str) -> str:
    path = os.path.join(IPC_DIR, topic)
    os.makedirs(path, exist_ok=True)
    return path


def put(topic: str, msg) -> None:
    """Положить сообщение в очередь (файл).

    FIX: имя = <timestamp_ns>_<uuid>.json — сортировка по имени = по времени.
    """
    folder = _topic_dir(topic)
    # timestamp_ns — 19 цифр, лексикографически сортируется как число
    fname = f"{time.time_ns():019d}_{uuid.uuid4().hex}.json"
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
                msg = Message.from_json(raw)
                try:
                    os.remove(oldest)
                except Exception:
                    pass
                return msg
            except Exception:
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
        return 0

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