"""System: навык «статус» — CPU, RAM, диски."""

import json
import threading
import time
import random
from pathlib import Path

from shared.log import log
from shared import ipc
from shared import protocol
from shared import metrics
from shared import config as cfg
from shared.ui_bridge import append_chat
from brain.skills.base import Skill, register
from brain.skills.system.cpu.collect import collect as collect_cpu
from brain.skills.system.cpu.phrase import phrase as phrase_cpu
from brain.skills.system.ram.collect import collect as collect_ram
from brain.skills.system.ram.phrase import phrase as phrase_ram
from brain.skills.system.disks.collect import collect as collect_disks
from brain.skills.system.disks.phrase import phrase as phrase_disks
from brain.skills.system import max_sender


TRIGGERS = {"статус", "система", "отчёт", "состояние"}

_phrases_cache = None


def _load_phrases() -> dict:
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
    phrases = _load_phrases().get(key, [])
    if not phrases:
        return {"text": fallback, "rate": "-5%", "emotion": "neutral"}
    return random.choice(phrases)


def _format_phrase(phrase_dict: dict, placeholders: dict) -> tuple[str, dict]:
    text = phrase_dict.get("text", "")
    for key, value in placeholders.items():
        text = text.replace(f"{{{key}}}", value)
    style = {
        "rate": phrase_dict.get("rate", "-5%"),
        "emotion": phrase_dict.get("emotion", "neutral"),
    }
    return text, style


def build_text() -> dict:
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


def _collect_and_report() -> None:
    try:
        time.sleep(1.5)
        built = build_text()
        placeholders = built["placeholders"]
        max_text = built["max_text"]

        status = max_sender.send(max_text, build_fn=build_text)
        key = _status_key(status)

        phrase_dict = _pick_phrase(key, "Сэр, статус готов. Полный отчёт — в MAX.")
        full_text, style = _format_phrase(phrase_dict, placeholders)

        log(f"system: {full_text} (max={status})", tag="SYSTEM", level="INFO")

        try:
            append_chat("assistant", full_text, meta="system")
        except Exception:
            pass

        ipc.put(protocol.TTS_INBOX, protocol.say(
            full_text,
            rate=style["rate"],
            emotion=style["emotion"],
        ))
        metrics.inc("tts_said")
    except Exception as e:
        log(f"system: _collect_and_report error: {e}", tag="SYSTEM", level="ERROR")


class SystemSkill(Skill):
    name = "system"
    priority = 5

    def can_handle(self, text: str) -> bool:
        return False

    def handle(self, command=None) -> str | None:
        metrics.inc("system_status")

        ack_phrase = _pick_phrase("STATUS_ACK", "Сию минуту, сэр.")
        ack_text, ack_style = _format_phrase(ack_phrase, {})
        log(f"system: ACK: {ack_text}", tag="SYSTEM", level="INFO")
        try:
            append_chat("assistant", ack_text, meta="system")
        except Exception:
            pass
        ipc.put(protocol.TTS_INBOX, protocol.say(
            ack_text,
            rate=ack_style["rate"],
            emotion=ack_style["emotion"],
        ))

        threading.Thread(
            target=_collect_and_report,
            daemon=True,
            name="system-status-worker",
        ).start()
        return None


register(SystemSkill())