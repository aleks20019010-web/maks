# tools/test_history.py
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brain.skills.llm._history import History


h = History()

# 1. Пусто
assert len(h) == 0
assert h.get_last_pair() is None
print("OK: пусто")

# 2. Добавить пару
h.add("user", "Почему небо голубое?")
h.add("assistant", "Рассеяние света.")
assert len(h) == 2
pair = h.get_last_pair()
assert pair == ("Почему небо голубое?", "Рассеяние света."), pair
print("OK: пара")

# 3. Добавить ещё
h.add("user", "Давай подробнее.")
h.add("assistant", "Свет рассеивается на молекулах воздуха.")
assert len(h) == 4
pair = h.get_last_pair()
assert pair[0] == "Давай подробнее.", pair
print("OK: вторая пара")

# 4. Обрезка по парам (MAX_PAIRS=5 → 10 сообщений)
for i in range(10):
    h.add("user", f"вопрос {i}")
    h.add("assistant", f"ответ {i}")
assert len(h) <= 10, len(h)
print(f"OK: обрезка по парам ({len(h)} сообщений)")

# 5. clear
h.clear()
assert len(h) == 0
print("OK: clear")

print("Все тесты OK")