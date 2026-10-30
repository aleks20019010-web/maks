# brain/nlu/context.py
# NLU-контекст: продолжение диалога, уточнения, TTL 60 секунд.
#
# НЕ ПУТАТЬ с personality_core/context.py — это ДРУГОЙ Context.
# personality_core.Context — для PolicyEngine/PhrasePicker (mood, energy, time).
# brain.nlu.context.NLUContext — для NLU pipeline (pending, history, ttl).
# Одинаковые имена — ловушка. Явно развожу: NLUContext vs Context.
#
# ЗАЧЕМ:
#   «включи арию» → CLARIFY(«не нашёл Орион») → пользователь: «арию»
#   Без контекста второй «арию» — это новая команда MUSIC_PLAY с ABSENT
#   → play_wave. С контекстом — это ответ на уточнение: заполняем entity
#   в pending, выполняем play_artist("Ария").
#
# TTL 60с. Отмена: «отмена»/«не надо»/«забудь» → clear().
# Сброс после выполнения: pending.clear() в pipeline, когда EXECUTE.
#
# ПРИМЕНЯЕТСЯ ДО IntentResolver:
#   pipeline.process(text, context):
#     1. if context.pending and not context.is_expired():
#          # пробуем заполнить entity в pending, НЕ зовём IntentResolver
#     2. else: обычный путь normalizer → intent → entity → decision
#   Это ключевое правило из architecture.md: «Context применяется до
#   IntentResolver. Если pending активен и TTL не истёк — заполнение entity,
#   не новый intent».

import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from brain.nlu.types import Entity, Intent


# ── TTL по умолчанию ────────────────────────────────────────
DEFAULT_TTL_SEC = 60.0

# ── Слова отмены ────────────────────────────────────────────
CANCEL_WORDS = frozenset({
    "отмена", "отмени", "забудь", "не надо", "неважно", "не важно",
    "не хочу", "стоп", "хватит", "нет",
})


@dataclass
class PendingCommand:
    """Ожидающая команда: intent известен, entity не хватает.

    missing — какие entity не хватает (например, ["artist"]).
    partial_entity — что уже извлекли (может быть None).
    original_text — исходная фраза (для логов и fallback).
    created_at — время создания (для TTL).
    ttl_sec — сколько живёт (60с по умолчанию).
    """
    intent: Intent
    missing: list[str] = field(default_factory=list)
    partial_entity: Optional[Entity] = None
    original_text: str = ""
    created_at: float = field(default_factory=time.time)
    ttl_sec: float = DEFAULT_TTL_SEC

    def is_expired(self, now: Optional[float] = None) -> bool:
        now = time.time() if now is None else now
        return (now - self.created_at) > self.ttl_sec

    def age_sec(self, now: Optional[float] = None) -> float:
        now = time.time() if now is None else now
        return max(0.0, now - self.created_at)


@dataclass
class NLUContext:
    """Контекст NLU между вызовами pipeline.

    pending — активное уточнение (или None).
    history — последние N (intent, text) для диагностики (не для LLM).
    ttl_until — когда pending истечёт (дублирует pending.created_at + ttl,
                для удобства чтения снаружи).

    НЕ ХРАНИТ: mood, energy, time_of_day — это personality_core.Context.
    """
    pending: Optional[PendingCommand] = None
    history: list[tuple[Intent, str]] = field(default_factory=list)
    ttl_until: float = 0.0

    # ── Pending ────────────────────────────────────────────
    def set_pending(self, pending: PendingCommand) -> None:
        self.pending = pending
        self.ttl_until = pending.created_at + pending.ttl_sec

    def get_pending(self) -> Optional[PendingCommand]:
        """Вернуть pending, если он активен и не истёк. Иначе None.

        Истёкший pending НЕ удаляем здесь — удалит clear_expired() или
        pipeline (явно). get_pending только читает.
        """
        if self.pending is None:
            return None
        if self.pending.is_expired():
            return None
        return self.pending

    def clear_pending(self) -> None:
        self.pending = None
        self.ttl_until = 0.0

    def clear_expired(self) -> bool:
        """Удалить pending, если истёк. True — удалили."""
        if self.pending is not None and self.pending.is_expired():
            self.clear_pending()
            return True
        return False

    # ── History ────────────────────────────────────────────
    def add_history(self, intent: Intent, text: str, max_len: int = 20) -> None:
        self.history.append((intent, text))
        if len(self.history) > max_len:
            self.history = self.history[-max_len:]

    # ── Cancel ─────────────────────────────────────────────
    def is_cancel(self, text: str) -> bool:
        """«отмена» / «не надо» / «забудь» — пользователь отменил уточнение."""
        if not text:
            return False
        t = text.lower().strip()
        return t in CANCEL_WORDS


# ── Менеджер контекста (singleton на процесс) ──────────────
# ВАЖНО: в brain один процесс, один контекст. Если когда-нибудь появится
# несколько пользователей (бот + голос) — контекст надо будет ключевать
# по source. Пока — один, как и задумано для домашнего Макса.
_context: Optional[NLUContext] = None
_lock = threading.Lock()


def get() -> NLUContext:
    """Вернуть синглтон NLUContext. Создаёт при первом вызове."""
    global _context
    with _lock:
        if _context is None:
            _context = NLUContext()
        return _context


def reset() -> None:
    """Сбросить контекст (для тестов и при «выход»)."""
    global _context
    with _lock:
        _context = NLUContext()