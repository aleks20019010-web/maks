"""
shared/sources.py — источник команды (enum вместо magic strings).

FIX: source="voice" / "voice_dialog" / "voice_hot" — magic strings.
Опечатка в строке → маршрутизация ломается молча.
CommandSource(str, Enum) — наследует str, поэтому:
  - json.dumps(CommandSource.VOICE) → "voice" (работает)
  - protocol.command(source=CommandSource.VOICE) → "voice" в JSON
  - source == "voice" → True (сравнение со строкой работает)
  - source == CommandSource.VOICE → True (сравнение с enum работает)
"""

from enum import Enum


class CommandSource(str, Enum):
    """Источник команды. str, Enum — совместим со строкой в JSON и сравнении."""

    VOICE = "voice"
    VOICE_HOT = "voice_hot"
    VOICE_DIALOG = "voice_dialog"
    SYSTEM = "system"
    UNKNOWN = "unknown"