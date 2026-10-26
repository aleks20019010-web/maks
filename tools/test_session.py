# tools/test_session.py
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brain.skills.llm._session import Session, SESSION_TIMEOUT_SEC
from brain.skills.llm._history import History


# 1. Пустая сессия
s = Session()
assert not s.is_active()
print("OK: пустая")

# 2. Открыть
s.open()
assert s.is_active()
print("OK: открыта")

# 3. Закрыть по тексту
assert s.should_close_by_text("хватит")
assert s.should_close_by_text("спасибо, достаточно")
assert not s.should_close_by_text("включи музыку")
print("OK: закрытие по тексту")

# 4. Закрыть
s.close()
assert not s.is_active()
print("OK: закрыта")

# 5. Таймаут (эмулируем)
s.open()
assert s.is_active()
# Подменяем _last_activity в прошлое
s._last_activity = time.time() - SESSION_TIMEOUT_SEC - 1
assert not s.is_active()
print("OK: таймаут")

print("Все тесты OK")