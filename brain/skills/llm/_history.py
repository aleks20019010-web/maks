# brain/skills/llm/_history.py
# История диалога LLM — с ролями (user / assistant).
#
# Формат (как OpenAI Chat):
#   [
#     {"role": "user", "content": "Почему небо голубое?"},
#     {"role": "assistant", "content": "Рассеяние света."},
#     {"role": "user", "content": "Давай подробнее."},
#   ]
#
# Обрезка:
#   - Не более N пар (5).
#   - Или по токенам (4000) — простая оценка: len(text) / 4.
#
# Очистка:
#   - При закрытии сессии — clear().
#   - При старте Макса — пусто.

import threading


MAX_PAIRS = 5           # максимум пар user+assistant
MAX_TOKENS = 4000       # примерная оценка (len / 4)


class History:
    def __init__(self):
        self._messages: list[dict] = []
        self._lock = threading.Lock()

    def add(self, role: str, content: str) -> None:
        """Добавить сообщение. role: 'user' или 'assistant'."""
        if not content:
            return
        with self._lock:
            self._messages.append({"role": role, "content": content})
            self._trim()

    def get_messages(self) -> list[dict]:
        """Вернуть копию истории для LLM."""
        with self._lock:
            return list(self._messages)

    def get_last_pair(self) -> tuple[str, str] | None:
        """Вернуть последнюю пару (user, assistant) или None."""
        with self._lock:
            if len(self._messages) < 2:
                return None
            # Ищем последний assistant и user перед ним
            last_assistant = None
            last_user = None
            for i in range(len(self._messages) - 1, -1, -1):
                msg = self._messages[i]
                if msg["role"] == "assistant" and last_assistant is None:
                    last_assistant = msg["content"]
                elif msg["role"] == "user" and last_assistant is not None:
                    last_user = msg["content"]
                    break
            if last_user and last_assistant:
                return (last_user, last_assistant)
            return None

    def clear(self) -> None:
        """Очистить историю."""
        with self._lock:
            self._messages = []

    def __len__(self) -> int:
        with self._lock:
            return len(self._messages)

    # ==================== ВНУТРЕННЕЕ ====================

    def _trim(self) -> None:
        """Обрезать историю: по парам и токенам."""
        # 1. По парам
        if len(self._messages) > MAX_PAIRS * 2:
            self._messages = self._messages[-MAX_PAIRS * 2:]

        # 2. По токенам (примерно)
        while self._estimate_tokens() > MAX_TOKENS and len(self._messages) > 2:
            self._messages = self._messages[2:]

    def _estimate_tokens(self) -> int:
        """Примерная оценка токенов: len(text) / 4."""
        total = 0
        for msg in self._messages:
            total += len(msg.get("content", "")) // 4
        return total


# ==================== СИНГЛТОН (на сессию) ====================

_history: History | None = None


def get() -> History:
    global _history
    if _history is None:
        _history = History()
    return _history


def reset() -> None:
    global _history
    _history = History()