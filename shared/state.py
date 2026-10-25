# shared/state.py
# Единственное общее состояние между brain и skills.
# Только то, что реально нужно нескольким модулям.
#
# Сейчас: что играет сейчас (артист, трек).
# Обновляется из brain/main.py на события now_playing / music_started / track_finished.
# Читается из controls.py (для appraisal).

import threading


_lock = threading.Lock()
_current = None  # {"artist": str, "title": str} или None


def set_current(artist: str, title: str) -> None:
    """Записать текущий трек. Пустые поля допустимы."""
    global _current
    with _lock:
        _current = {"artist": artist or "", "title": title or ""}


def get_current() -> dict | None:
    """Вернуть копию текущего трека или None."""
    with _lock:
        if _current is None:
            return None
        return dict(_current)


def clear_current() -> None:
    """Очистить текущий трек."""
    global _current
    with _lock:
        _current = None