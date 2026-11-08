"""
shared/runtime_state.py — единый механизм межпроцессных флагов с TTL.

Зачем
-----
До этого модуля флаги между процессами жили в трёх разных форматах:

  music_playing.flag    — write_text(str(time.time())), exists() БЕЗ TTL.
                          music упал с флагом → ASR навсегда думает, что
                          музыка играет, hot words идут в brain.
  expecting_input.flag  — то же самое, пишет brain/pending.py. brain упал
                          между set и pop → флаг залип.
  session_open.flag     — float(read_text()), TTL 20 сек проверяется
                          вручную в _in_dialog. Работает, но это
                          единственный флаг, который сделал всё правильно —
                          и то руками.

Три формата, три поведения. Этот модуль сводит всё к одному: JSON-файл
на флаг, поле ts + ttl внутри, чтение с проверкой свежести.

Формат
------
data/runtime/<name>.flag.json:
    {"value": <json>, "ts": <float>, "ttl": <float|null>}

Почему один файл на флаг
------------------------
Каждый флаг пишет РОВНО ОДИН процесс (контракт). Если бы все флаги лежали
в одном JSON — пришлось бы делать межпроцессный лок на каждую запись
(read-modify-write всего файла), иначе brain затрёт music_playing, который
в этот момент пишет music. С отдельными файлами гонки нет by design.

Нарушение контракта «один писатель» — не рантайм-ошибка, а ошибка
проектирования: если двум процессам нужен один флаг — это либо два разных
флага, либо нужен IPC.

Атомарность
-----------
Запись: <name>.flag.json.tmp.<pid>.<tid> → os.replace(target). os.replace
атомарен на NTFS и POSIX: читатель видит либо старый файл целиком, либо
новый целиком.

Чтение: open() + json.load(). Нет файла — нет флага. Битый JSON — считаем,
что флага нет, и логируем WARN один раз на имя (не спамим в цикле).

Почему не IPC
-------------
IPC-событие семантически правильнее, но это новая инфраструктура
(подписки, reconnect, потеря событий при рестарте). Для трёх флагов —
из пушки по воробьям. Если флагов станет 20 — переедем, интерфейс
get/set/clear не изменится.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from shared.log import log


# Локи на запись — по одному на имя, лениво. Защищают от гонки внутри
# ОДНОГО процесса (два потока пишут один флаг). Межпроцессный лок не нужен:
# один флаг — один писатель-процесс (контракт выше).
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()

# Имена, на которые уже ругнулись за битый JSON. Один раз за жизнь процесса.
_warned_bad: set[str] = set()


def _runtime_dir() -> Path:
    """Папка data/runtime. Создаётся в config.py при импорте, но на всякий
    случай mkdir(exist_ok=True) здесь — если config поменяется."""
    from shared import config as cfg
    d = cfg.RUNTIME_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def _flag_path(name: str) -> Path:
    """Путь к файлу флага. Имя — только [a-z0-9_], иначе ValueError.

    Не паранойя: имя приходит из кода, но если однажды кто-то передаст
    '../config/aliases', не хотим писать в aliases.json. Лучше упасть
    на старте, чем тихо перезаписать чужой файл."""
    if not name or not all(c.isalnum() or c == "_" for c in name):
        raise ValueError(f"runtime_state: недопустимое имя флага {name!r} "
                         f"(только [a-z0-9_])")
    return _runtime_dir() / f"{name}.flag.json"


def _get_lock(name: str) -> threading.Lock:
    with _locks_guard:
        lock = _locks.get(name)
        if lock is None:
            lock = threading.Lock()
            _locks[name] = lock
        return lock


def _atomic_write(path: Path, payload: dict) -> None:
    """tmp → os.replace. tmp-имя уникально в пределах процесса (pid+tid),
    межпроцессных коллизий нет — один флаг пишет один процесс."""
    tmp = path.with_suffix(
        path.suffix + f".tmp.{os.getpid()}.{threading.get_ident()}"
    )
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, path)
    finally:
        # Если os.replace упал (диск отключён, права) — tmp остался.
        # Убираем, чтобы не мусорить. Не падаем: запись флага — не критичная.
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def set(name: str, value: Any = True, ttl: float | None = None) -> None:
    """Записать флаг.

    name  — имя (см. _flag_path)
    value — любое JSON-сериализуемое; по умолчанию True
    ttl   — секунды жизни; None = бессрочно (до clear или перезаписи)

    Почему ttl по умолчанию None, а не «30 сек»: TTL — решение вызывающего,
    а не модуля. music_playing хочет 30 сек, session_open — 20,
    expecting_input — 60. Явное лучше неявного. Константы TTL_* внизу.
    """
    path = _flag_path(name)
    payload = {"value": value, "ts": time.time(), "ttl": ttl}
    with _get_lock(name):
        _atomic_write(path, payload)


def get(name: str, default: Any = None) -> Any:
    """Прочитать value флага, если он жив, иначе default.

    Протухание: ts + ttl < now → default. Файл не удаляем при чтении:
    читателей может быть несколько, если один удалит — другой получит
    FileNotFoundError. Проще игнорировать протухшее. Мусор — 3 файла
    по 100 байт, не проблема; set перезапишет, clear удалит.
    """
    payload = _read_payload(name)
    if payload is None:
        return default

    ts = payload.get("ts")
    ttl = payload.get("ttl")
    if ttl is not None:
        try:
            if (time.time() - float(ts)) > float(ttl):
                return default
        except (TypeError, ValueError):
            return default
    return payload.get("value", default)


def get_ts(name: str) -> float | None:
    """Timestamp записи флага (unix time), или None если флага нет/протух.

    Нужен, когда важна не «жив ли», а «когда записан» (например, для
    диагностики в логах). TTL здесь тоже проверяется — протухший флаг
    не отдаём.
    """
    payload = _read_payload(name)
    if payload is None:
        return None
    ts = payload.get("ts")
    ttl = payload.get("ttl")
    if ttl is not None:
        try:
            if (time.time() - float(ts)) > float(ttl):
                return None
        except (TypeError, ValueError):
            return None
    try:
        return float(ts)
    except (TypeError, ValueError):
        return None


def _read_payload(name: str) -> dict | None:
    """Внутреннее: прочитать JSON без проверки TTL. None если нет/битый."""
    path = _flag_path(name)
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        if not isinstance(payload, dict):
            raise ValueError("not a dict")
        return payload
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, OSError, ValueError) as e:
        if name not in _warned_bad:
            _warned_bad.add(name)
            log(f"runtime_state: битый флаг {name!r}: {e}",
                tag="RUNTIME", level="WARN")
        return None


def clear(name: str) -> None:
    """Удалить флаг. Идемпотентно: нет файла — не ошибка."""
    path = _flag_path(name)
    with _get_lock(name):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError as e:
            # clear вызывается в том числе в finally — падать оттуда плохо.
            log(f"runtime_state: не удалось удалить {name!r}: {e}",
                tag="RUNTIME", level="WARN")


def exists(name: str) -> bool:
    """Есть ли живой флаг. Отдельно от get(), потому что value может быть
    None (если кто-то так записал) — а exists() про «жив ли»."""
    return get(name) is not None


# ---------------------------------------------------------------------------
# Имена флагов — константы, чтобы не было опечаток в строках по проекту.
# Одна опечатка "music_playng" — и флаг «не работает», а искать полдня.
# ---------------------------------------------------------------------------

FLAG_MUSIC_PLAYING = "music_playing"      # пишет: workers/music/playback.py
FLAG_EXPECTING_INPUT = "expecting_input"  # пишет: brain/pending.py
FLAG_SESSION_OPEN = "session_open"        # пишет: brain/skills/llm/skill.py
                                          #        (+ возможно tts — проверить)

# TTL — константы для вызывающих. Значения выбраны так:
#   music_playing: 30 сек. Переживёт короткий лаг (prefetch, download),
#     но не переживёт падение music-воркера (supervisor перезапустит
#     за ~3 сек, флаг протухнет за 30).
#   expecting_input: 60 сек. С запасом на «пользователь думает».
#   session_open: 20 сек — как было в _in_dialog, не меняем поведение.
TTL_MUSIC_PLAYING = 30.0
TTL_EXPECTING_INPUT = 60.0
TTL_SESSION_OPEN = 20.0