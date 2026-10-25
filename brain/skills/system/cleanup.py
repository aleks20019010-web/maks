# brain/skills/system/cleanup.py
# Очистка диска C. Голосовой скил (system).
#
# Логика:
#   «почисти диск C» → cleanup_dry() → «Могу освободить X ГБ. Очистить?»
#   «да» → cleanup_real() → «Очистка завершена. Освобождено X ГБ.»
#   «нет» → отмена.
#
# FIX: детали НЕ озвучиваются. Только итог (свободно / могу освободить).
# FIX: если Predicted == 0 — «Чистить нечего», без вопроса.
# FIX: темп быстрее (rate="+10%") для cleanup-фраз.
#
# ВАЖНО: cleanup — системная команда. Она НЕ должна зависеть от mood/energy.
# Поэтому используется прямой protocol.say (минуя engine/policy/composer).
#
# Pending — через brain.pending (set/get/pop).
#
# SIGNATURE = "— Макс"
# «Сэр» убран везде.
#
# cleanup_c.ps1 — в D:\Maks\tools\ (часть репозитория, не D:\Scripts\).

import os
import json
import random
import subprocess

from brain.skills.base import Skill, register
from shared.log import log
from shared import config as cfg
from shared import ipc
from shared import protocol


# ==================== ПУТИ ====================
# tools/ — относительно brain/skills/system/cleanup.py (на 3 уровня выше)
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
TOOLS_DIR = os.path.join(_BASE_DIR, "tools")
CLEANUP_SCRIPT = os.path.join(TOOLS_DIR, "cleanup_c.ps1")

PS_EXE = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
CLEANUP_RESULT = r"D:\cleanup_c_result.json"

CLEANUP_PENDING_KEY = "cleanup"
CLEANUP_PENDING_TIMEOUT = 120.0

CLEANUP_RATE = "+10%"  # темп быстрее для cleanup-фраз


# ==================== ПРЯМОЙ TTS (минуя policy) ====================

def _say_text(text: str, rate: str = None):
    """Прямой TTS с текстом. Минуя engine/policy/composer."""
    if not text:
        return
    try:
        ipc.put(protocol.TTS_INBOX, protocol.say(text, sync=True, rate=rate))
    except Exception as e:
        log(f"cleanup: _say_text: {e}", tag="BRAIN", level="WARN")


def _say_phrase(phrase_set: str, rate: str = None, **kw):
    """Выбрать фразу из responses/*.json и сказать напрямую."""
    try:
        from brain.skills._helpers import _engine
        if _engine is None:
            return
        phrase = _engine.pick_full(phrase_set, **kw)
        if phrase and phrase.get("text"):
            ipc.put(protocol.TTS_INBOX, protocol.say(phrase["text"], sync=True, rate=rate))
    except Exception as e:
        log(f"cleanup: _say_phrase({phrase_set}): {e}", tag="BRAIN", level="WARN")


# ==================== CLEANUP ====================

def cleanup_dry():
    """Запустить cleanup_c.ps1 -DryRun. Вернуть (data, msg, predicted)."""
    try:
        if os.path.exists(CLEANUP_RESULT):
            os.remove(CLEANUP_RESULT)
    except Exception:
        pass

    try:
        subprocess.run(
            [PS_EXE, "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", CLEANUP_SCRIPT, "-DryRun"],
            timeout=300, creationflags=subprocess.CREATE_NO_WINDOW, cwd=TOOLS_DIR
        )
    except Exception as e:
        return None, f"Ошибка запуска dry-run: {e}", 0

    if not os.path.exists(CLEANUP_RESULT):
        return None, "Не получил результат dry-run.", 0

    try:
        with open(CLEANUP_RESULT, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except Exception as e:
        return None, f"Ошибка JSON: {e}", 0

    free_gb = data.get("FreeBefore", 0)
    total_gb = data.get("TotalGB", 0)
    predicted = data.get("Predicted", 0)

    # FIX: детали НЕ озвучиваем. Только итог.
    if predicted <= 0:
        msg = (
            f"На диске C: {free_gb} из {total_gb} ГБ свободно. "
            f"Чистить нечего, и так свободен."
        )
        return data, msg, 0

    msg = (
        f"На диске C: {free_gb} из {total_gb} ГБ свободно. "
        f"Могу освободить около {predicted} ГБ. "
        f"Очистить? Да или нет."
    )
    return data, msg, predicted


def cleanup_real():
    """Запустить cleanup_c.ps1 (без -DryRun). Вернуть msg."""
    try:
        if os.path.exists(CLEANUP_RESULT):
            os.remove(CLEANUP_RESULT)
    except Exception:
        pass

    try:
        subprocess.run(
            [PS_EXE, "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", CLEANUP_SCRIPT],
            timeout=1800, creationflags=subprocess.CREATE_NO_WINDOW, cwd=TOOLS_DIR
        )
    except Exception as e:
        return f"Ошибка очистки: {e}"

    if not os.path.exists(CLEANUP_RESULT):
        return "Ошибка: не получил результат."

    try:
        with open(CLEANUP_RESULT, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except Exception as e:
        return f"Ошибка JSON: {e}"

    # FIX: только итог, без деталей
    return (
        f"Очистка завершена. Освобождено: {data.get('Freed',0)} ГБ. "
        f"Свободно на C: {data.get('FreeAfter',0)} ГБ."
    )


# ==================== СКИЛЫ ====================

class CleanupSkill(Skill):
    name = "cleanup"
    priority = 6

    def can_handle(self, text: str) -> bool:
        tl = text.lower().strip()
        if "почисти" in tl and ("диск" in tl or "c" in tl or "ц" in tl):
            return True
        if "очисти" in tl and ("диск" in tl or "c" in tl or "ц" in tl):
            return True
        return False

    def handle(self, text: str) -> bool:
        _say_phrase("CLEANUP_DRY_ACK", rate=CLEANUP_RATE)
        data, msg, predicted = cleanup_dry()

        if data is None:
            _say_text(f"Ошибка: {msg}", rate=CLEANUP_RATE)
            return True

        # FIX: если Predicted == 0 — не спрашиваем, просто говорим
        if predicted <= 0:
            _say_text(msg, rate=CLEANUP_RATE)
            return True

        # Сохраняем pending — ждём «да»/«нет»
        try:
            from brain import pending
            pending.set(
                CLEANUP_PENDING_KEY,
                timeout=CLEANUP_PENDING_TIMEOUT,
                on_timeout=None,
                data={"data": data, "msg": msg},
            )
            log(f"cleanup: pending set ({CLEANUP_PENDING_TIMEOUT}с)", tag="BRAIN")
        except Exception as e:
            log(f"cleanup: pending.set: {e}", tag="BRAIN", level="WARN")

        # Прямой TTS, минуя policy
        _say_text(msg, rate=CLEANUP_RATE)
        return True


class CleanupConfirmSkill(Skill):
    name = "cleanup_confirm"
    priority = 1

    def can_handle(self, text: str) -> bool:
        try:
            from brain import pending
            if pending.get(CLEANUP_PENDING_KEY) is None:
                return False
        except Exception:
            return False
        tl = text.lower().strip()
        return tl in ("да", "yes", "ок", "окей", "подтверждаю", "+",
                      "нет", "no", "отмена", "-")

    def handle(self, text: str) -> bool:
        from brain import pending
        tl = text.lower().strip()

        p = pending.pop(CLEANUP_PENDING_KEY)
        if p is None:
            _say_text("Отменил.", rate=CLEANUP_RATE)
            return True

        if tl in ("да", "yes", "ок", "окей", "подтверждаю", "+"):
            _say_phrase("CLEANUP_RUNNING", rate=CLEANUP_RATE)
            result = cleanup_real()
            _say_text(result, rate=CLEANUP_RATE)
        else:
            _say_text("Отменил.", rate=CLEANUP_RATE)
        return True


register(CleanupSkill())
register(CleanupConfirmSkill())