"""
personality_core/state.py — внутреннее состояние Personality Core.

ВАЖНО: здесь НЕ долговременная память. Только "сейчас":
    - last_pick[phrase_set]       — что говорили в прошлый раз
    - rule_cooldowns[rule_name]   — когда правило срабатывало
    - ticks_since_last_comment    — счётчик для инициативы

Долговременный опыт → memory/
Долговременные правила → personality_core/rules/*.json
"""

import time
import threading

# Защита от гонок между потоками
_lock = threading.RLock()

# last_pick[phrase_set] = последняя выбранная фраза (для анти-повтора)
last_pick = {}

# cooldowns[rule_name] = timestamp последнего срабатывания
rule_cooldowns = {}

# счётчик тиков с последнего комментария (для инициативы)
ticks_since_last_comment = 0

# счётчик команд за последние N секунд (для "should_stay_silent")
recent_command_times = []        # timestamps

# --- Утилиты ---
def remember_pick(phrase_set: str, text: str):
    with _lock:
        last_pick[phrase_set] = text


def get_last_pick(phrase_set: str):
    with _lock:
        return last_pick.get(phrase_set)


def mark_rule_fired(rule_name: str):
    with _lock:
        rule_cooldowns[rule_name] = time.time()


def get_rule_cooldown(rule_name: str, cooldown_sec: float) -> bool:
    """True, если правило ещё в cooldown (нельзя запускать)."""
    with _lock:
        last = rule_cooldowns.get(rule_name, 0.0)
        return (time.time() - last) < cooldown_sec


def mark_command():
    """Запомнить время команды."""
    with _lock:
        now = time.time()
        recent_command_times.append(now)
        # Чистим старше 120 секунд
        cutoff = now - 120
        while recent_command_times and recent_command_times[0] < cutoff:
            recent_command_times.pop(0)


def commands_last_2min() -> int:
    with _lock:
        now = time.time()
        cutoff = now - 120
        while recent_command_times and recent_command_times[0] < cutoff:
            recent_command_times.pop(0)
        return len(recent_command_times)


def reset():
    """Полный сброс внутреннего состояния (для тестов)."""
    with _lock:
        last_pick.clear()
        rule_cooldowns.clear()
        recent_command_times.clear()
        global ticks_since_last_comment
        ticks_since_last_comment = 0