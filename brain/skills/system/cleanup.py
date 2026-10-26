# brain/skills/system/cleanup.py
# Очистка диска C. Голосовой скил (system).
#
# Логика:
#   «почисти диск C» → cleanup_dry() → «Могу освободить: безопасно — X ГБ; корзина — Y ГБ. Очистить безопасное?»
#   «да» → cleanup_real() → «Очистка завершена. Освобождено X ГБ.» (или «не удалось — файлы используются»)
#   «нет» → отмена.
#
# cleanup_c.ps1 чистит ТОЛЬКО безопасное по умолчанию:
#   - %TEMP%, %LOCALAPPDATA%\Temp, C:\Windows\Temp
#   - Windows Update Download, CBS/DISM логи
#   - Jellyfin transcodes
# Корзина и Windows.old — только с флагами. Prefetch не трогаем.
#
# cleanup_dry разделяет safe_predicted / recycle_predicted / dangerous.
# Корзина — не Dangerous, но и не «безопасное по умолчанию». Она опциональна.
# В safe_predicted не входит, в msg упоминается отдельно.
#
# Проверяем returncode PowerShell. Если != 0 — не читаем старый JSON.
#
# Пути — от cfg.BASE_DIR (data/cleanup_c_result.json, tools/cleanup_c.ps1).

import json
import subprocess

from brain.skills.base import Skill, register
from shared.log import log
from shared import config as cfg
from shared import ipc
from shared import protocol


# ==================== ПУТИ ====================
TOOLS_DIR = cfg.BASE_DIR / "tools"
CLEANUP_SCRIPT = TOOLS_DIR / "cleanup_c.ps1"
CLEANUP_RESULT = cfg.DATA_DIR / "cleanup_c_result.json"

PS_EXE = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"

CLEANUP_PENDING_KEY = "cleanup"
CLEANUP_PENDING_TIMEOUT = 120.0
CLEANUP_RATE = "+10%"


# ==================== ПРЯМОЙ TTS ====================

def _say_text(text: str, rate: str = None):
    if not text:
        return
    try:
        ipc.put(protocol.TTS_INBOX, protocol.say(text, sync=True, rate=rate))
    except Exception as e:
        log(f"cleanup: _say_text: {e}", tag="BRAIN", level="WARN")


def _say_phrase(phrase_set: str, rate: str = None, **kw):
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

def _run_ps(dry_run: bool, include_recycle: bool = False,
            include_winold: bool = False) -> tuple[bool, str]:
    if not CLEANUP_SCRIPT.exists():
        return False, f"cleanup_c.ps1 не найден: {CLEANUP_SCRIPT}"

    try:
        if CLEANUP_RESULT.exists():
            CLEANUP_RESULT.unlink()
    except Exception:
        pass

    cmd = [
        PS_EXE, "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", str(CLEANUP_SCRIPT),
        "-ResultFile", str(CLEANUP_RESULT),
    ]
    if dry_run:
        cmd.append("-DryRun")
    if include_recycle:
        cmd.append("-IncludeRecycleBin")
    if include_winold:
        cmd.append("-IncludeWindowsOld")

    try:
        result = subprocess.run(
            cmd,
            timeout=1800,
            creationflags=subprocess.CREATE_NO_WINDOW,
            cwd=str(TOOLS_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return False, "Таймаут очистки (30 минут)"
    except Exception as e:
        return False, f"Ошибка запуска: {e}"

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()[:300]
        return False, f"PowerShell exit {result.returncode}: {stderr}"

    if not CLEANUP_RESULT.exists():
        return False, "PowerShell не создал cleanup_c_result.json"

    return True, ""


def _read_result() -> dict | None:
    if not CLEANUP_RESULT.exists():
        return None
    try:
        with open(CLEANUP_RESULT, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception as e:
        log(f"cleanup: JSON: {e}", tag="BRAIN", level="WARN")
        return None


def cleanup_dry(include_recycle: bool = False,
                include_winold: bool = False) -> tuple[dict | None, str, float]:
    """Dry-run. Вернуть (data, msg, safe_predicted_gb).

    safe_predicted_gb — только безопасные цели (без корзины и Dangerous).
    """
    ok, err = _run_ps(dry_run=True,
                      include_recycle=include_recycle,
                      include_winold=include_winold)
    if not ok:
        return None, f"Ошибка dry-run: {err}", 0.0

    data = _read_result()
    if data is None:
        return None, "Не получил результат dry-run.", 0.0

    free_gb = data.get("FreeBefore", 0)
    total_gb = data.get("TotalGB", 0)

    details = data.get("Details", [])
    safe_predicted = 0.0
    recycle_predicted = 0.0
    dangerous_items = []

    for item in details:
        freed = float(item.get("Freed", 0) or 0)
        desc = item.get("Desc", "")
        if desc.startswith("Корзина"):
            recycle_predicted += freed
        elif item.get("Dangerous"):
            if freed > 0:
                dangerous_items.append(desc)
        else:
            safe_predicted += freed

    safe_predicted = round(safe_predicted, 2)
    recycle_predicted = round(recycle_predicted, 2)

    if safe_predicted <= 0 and recycle_predicted <= 0 and not dangerous_items:
        msg = (
            f"На диске C: {free_gb} из {total_gb} ГБ свободно. "
            f"Чистить нечего, и так свободен."
        )
        return data, msg, 0.0

    parts = []
    if safe_predicted > 0:
        parts.append(f"безопасно — {safe_predicted} ГБ")
    if recycle_predicted > 0:
        parts.append(f"корзина — {recycle_predicted} ГБ")
    if dangerous_items:
        parts.append(f"{', '.join(dangerous_items)} — не трогаю")

    msg = (
        f"На диске C: {free_gb} из {total_gb} ГБ свободно. "
        f"Могу освободить: {'; '.join(parts)}. "
        f"Очистить безопасное? Да или нет."
    )

    return data, msg, safe_predicted


def cleanup_real() -> tuple[dict | None, str]:
    """Реальная очистка (только безопасное). Вернуть (data, msg)."""
    ok, err = _run_ps(dry_run=False,
                      include_recycle=False,
                      include_winold=False)
    if not ok:
        return None, f"Ошибка очистки: {err}"

    data = _read_result()
    if data is None:
        return None, "Ошибка: не получил результат."

    freed = data.get("Freed", 0)
    free_after = data.get("FreeAfter", 0)

    # FIX: если Freed == 0, а Predicted был > 0 — файлы заняты (Temp, CBS).
    # Не врём «освобождено 0», говорим честно.
    if freed <= 0:
        return data, (
            f"Очистка завершена, но освободить не удалось — файлы используются. "
            f"Свободно на C: {free_after} ГБ."
        )

    return data, (
        f"Очистка завершена. Освобождено: {freed} ГБ. "
        f"Свободно на C: {free_after} ГБ."
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

        if predicted <= 0:
            _say_text(msg, rate=CLEANUP_RATE)
            return True

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
            data, msg = cleanup_real()
            _say_text(msg, rate=CLEANUP_RATE)
        else:
            _say_text("Отменил.", rate=CLEANUP_RATE)
        return True


register(CleanupSkill())
register(CleanupConfirmSkill())