"""
brain/errors.py — единая обработка ошибок.

Правило:
  - Ошибка — это ЛОГ + СЧЁТЧИК. Фраза — только если ошибка видна пользователю.
  - speak=True  — ошибка в команде (скил упал, TTS не сказал). Пользователь ждёт ответа.
  - speak=False — фоновая ошибка (tick, IPC, событие). Пользователь не ждёт. Молчим.

Почему: если каждую фоновую ошибку озвучивать — Макс задолбает
"Что-то пошло не так" каждые 30 секунд. Это хуже, чем молчание.
"""

from shared.log import log
from shared import metrics


def handle_error(e: Exception, context: str = "", speak: bool = False) -> None:
    """Обработать ошибку.

    e       — исключение.
    context — где (для лога): "router", "engine", "skill:music".
    speak   — сказать пользователю ERROR_GENERIC (только если ошибка видна ему).
    """
    log(f"ERROR [{context}]: {type(e).__name__}: {e}", tag="BRAIN", level="ERROR")
    metrics.inc("errors")
    metrics.inc(f"errors_{context}" if context else "errors_unknown")

    if speak:
        try:
            from brain.skills._helpers import respond_and_say
            respond_and_say("ERROR_GENERIC")
        except Exception as e2:
            log(f"ERROR [errors.handle_error]: не смог сказать: {e2}", tag="BRAIN", level="ERROR")