# brain/skills/llm/_session.py
# Сессия диалога LLM.
#
# Открывается — после ответа LLM на вопрос.
# Закрывается:
#   - явная команда (включи музыку, пауза, статус, погода и т.д.)
#   - тишина N секунд (1 минута)
#   - явное «хватит» / «спасибо, достаточно» / «закончили»
#   - новая тема (LLM сама решает — но это в skill.py)
#
# В сессии:
#   - история с ролями (из _history.py)
#   - время открытия
#   - последний ответ LLM

import threading
import time


SESSION_TIMEOUT_SEC = 60.0  # 1 минута тишины → закрыть


class Session:
    def __init__(self):
        self._active = False
        self._opened_at = 0.0
        self._last_activity = 0.0
        self._lock = threading.Lock()

    def open(self) -> None:
        """Открыть сессию."""
        with self._lock:
            self._active = True
            now = time.time()
            if self._opened_at == 0.0:
                self._opened_at = now
            self._last_activity = now

    def close(self) -> None:
        """Закрыть сессию."""
        with self._lock:
            self._active = False
            self._opened_at = 0.0
            self._last_activity = 0.0
        # Очистить историю
        try:
            from brain.skills.llm import _history
            _history.get().clear()
        except Exception:
            pass

    def touch(self) -> None:
        """Обновить время последней активности."""
        with self._lock:
            if self._active:
                self._last_activity = time.time()

    def is_active(self) -> bool:
        """Проверить, активна ли сессия (с учётом таймаута)."""
        with self._lock:
            if not self._active:
                return False
            if time.time() - self._last_activity > SESSION_TIMEOUT_SEC:
                # Таймаут — закрыть
                self._active = False
                self._opened_at = 0.0
                self._last_activity = 0.0
                return False
            return True

    def should_close_by_text(self, text: str) -> bool:
        """Проверить, просит ли пользователь закрыть сессию."""
        if not text:
            return False
        tl = text.lower().strip()
        close_words = {
            "хватит", "достаточно", "закончили", "закройся", "замолчи",
            "спасибо достаточно", "спасибо, достаточно", "всё, хватит",
            "все, хватит", "стоп общение", "пока",
        }
        return tl in close_words


# ==================== СИНГЛТОН ====================

_session: Session | None = None


def get() -> Session:
    global _session
    if _session is None:
        _session = Session()
    return _session


def reset() -> None:
    global _session
    _session = Session()