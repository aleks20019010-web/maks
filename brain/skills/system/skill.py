"""System: навык «статус» — CPU, RAM, диски.

- build_text() — собирает данные + плейсхолдеры для фразы. Без отправки.
- SystemSkill.handle() — ACK + сбор + случайная фраза из responses/system.json
  с подстановкой {CPU}, {RAM}, {DISK} + отправка в TTS и MAX.

Речь — через ipc.put(TTS_INBOX, protocol.say(text, rate=..., emotion=...)).
"""

import json
import time
import random
from pathlib import Path

from shared.log import log
from shared import ipc
from shared import protocol
from shared import metrics
from shared import config as cfg
from brain.skills.base import Skill, register
from brain.skills.system.cpu.collect import collect as collect_cpu
from brain.skills.system.cpu.phrase import phrase as phrase_cpu
from brain.skills.system.ram.collect import collect as collect_ram
from brain.skills.system.ram.phrase import phrase as phrase_ram
from brain.skills.system.disks.collect import collect as collect_disks
from brain.skills.system.disks.phrase import phrase as phrase_disks
from brain.skills.system import max_sender


TRIGGERS = {"статус", "система", "отчёт", "состояние"}

# Кэш фраз (загружается один раз).
_phrases_cache = None


def _load_phrases() -> dict:
    """Загрузить responses/system.json. Кэш в памяти."""
    global _phrases_cache
    if _phrases_cache is not None:
        return _phrases_cache

    path = Path(cfg.BASE_DIR) / "responses" / "system.json"
    try:
        with open(path, "r", encoding="utf-8") as f:
            _phrases_cache = json.load(f)
        log(f"system: фразы загружены ({path})", tag="SYSTEM", level="INFO")
    except Exception as e:
        log(f"system: не удалось загрузить фразы: {e}", tag="SYSTEM", level="ERROR")
        _phrases_cache = {}
    return _phrases_cache


def _pick_phrase(key: str, fallback: str) -> dict:
    """Выбрать случайную фразу из списка. Fallback — если ключа нет."""
    phrases = _load_phrases().get(key, [])
    if not phrases:
        return {"text": fallback, "rate": "-5%", "emotion": "neutral"}
    return random.choice(phrases)


def _format_phrase(phrase_dict: dict, placeholders: dict) -> tuple[str, dict]:
    """Подставить плейсхолдеры в текст. Вернуть (text, {rate, emotion})."""
    text = phrase_dict.get("text", "")
    for key, value in placeholders.items():
        text = text.replace(f"{{{key}}}", value)
    style = {
        "rate": phrase_dict.get("rate", "-5%"),
        "emotion": phrase_dict.get("emotion", "neutral"),
    }
    return text, style


def build_text() -> dict:
    """Собирает данные из трёх подпакетов. НЕ отправляет и НЕ говорит."""
    try:
        cpu_phrase = phrase_cpu(collect_cpu())
    except Exception as e:
        log(f"system: cpu упал: {e}", tag="SYSTEM", level="ERROR")
        cpu_phrase = {"short": "CPU не вижу", "max_lines": ["💻 Процессор: данные недоступны"]}

    try:
        ram_phrase = phrase_ram(collect_ram())
    except Exception as e:
        log(f"system: ram упал: {e}", tag="SYSTEM", level="ERROR")
        ram_phrase = {"short": "RAM не вижу", "max_lines": ["⚡ Память: данные недоступны"]}

    try:
        disks_phrase = phrase_disks(collect_disks())
    except Exception as e:
        log(f"system: disks упал: {e}", tag="SYSTEM", level="ERROR")
        disks_phrase = {"short": "диски не вижу", "max_lines": ["💽 Диски: данные недоступны"]}

    placeholders = {
        "CPU": cpu_phrase["short"],
        "RAM": ram_phrase["short"],
        "DISK": disks_phrase["short"],
    }

    max_lines = ["🎩 Докладываю, сэр.", ""]
    max_lines.extend(ram_phrase["max_lines"])
    max_lines.extend(cpu_phrase["max_lines"])
    max_lines.extend(disks_phrase["max_lines"])
    max_lines.extend(["", "— Макс"])

    return {"placeholders": placeholders, "max_text": "\n".join(max_lines)}


def _status_key(status: str) -> str:
    if status == "sent":
        return "STATUS_REPORT"
    if status == "no_internet":
        return "STATUS_NO_INTERNET"
    if status == "retry_pending":
        return "STATUS_RETRY_PENDING"
    if status == "curl_missing":
        return "STATUS_CURL_MISSING"
    return "STATUS_MAX_ERROR"


class SystemSkill(Skill):
    name = "system"
    priority = 5

    def can_handle(self, text: str) -> bool:
        lower = text.lower().strip()
        return any(word in lower for word in TRIGGERS)

    def handle(self, text: str) -> str | None:
        metrics.inc("system_status")

        # 1. ACK — «Сию минуту, сэр. Собираю данные.»
        ack_phrase = _pick_phrase("STATUS_ACK", "Сию минуту, сэр.")
        ack_text, ack_style = _format_phrase(ack_phrase, {})
        log(f"system: ACK: {ack_text}", tag="SYSTEM", level="INFO")
        ipc.put(protocol.TTS_INBOX, protocol.say(
            ack_text,
            rate=ack_style["rate"],
            emotion=ack_style["emotion"],
        ))

        # 2. Пауза, чтобы ACK прозвучал. TTS worker может перебить —
        # sleep(2) страховка. Потом — очередь в TTS, если понадобится.
        time.sleep(2)

        # 3. Собираем данные — cpu_collect блокирует 3 сек.
        built = build_text()
        placeholders = built["placeholders"]
        max_text = built["max_text"]

        # 4. Отправляем в MAX.
        status = max_sender.send(max_text)
        key = _status_key(status)

        # 5. Говорим реальный статус — случайная фраза.
        phrase_dict = _pick_phrase(key, "Сэр, статус готов. Полный отчёт — в MAX.")
        full_text, style = _format_phrase(phrase_dict, placeholders)

        log(f"system: {full_text} (max={status})", tag="SYSTEM", level="INFO")

        ipc.put(protocol.TTS_INBOX, protocol.say(
            full_text,
            rate=style["rate"],
            emotion=style["emotion"],
        ))
        metrics.inc("tts_said")

        return None


# ── Регистрация ───────────────────────────────────────────────
register(SystemSkill())