# brain/nlu/types.py
# NLU-контракты. Только структуры, без логики и логирования.
#
# Ответственность:
#   types.py     — типы данных и диагностических событий (этот файл)
#   pipeline.py, intent.py, entity/* — формирование DiagnosticEvent
#   brain/nlu/logging.py — запись и форматирование логов (этап 8, позже)
#
# ВАЖНО:
#   EntityState  — результат извлечения параметра (FOUND/ABSENT/UNRESOLVED/AMBIGUOUS)
#   DecisionAction — действие системы (EXECUTE/CLARIFY/FALLBACK)
#   Это разные вещи, не смешивать.
#
#   Entity.match_confidence  — насколько уверенно определена сущность
#   Decision.confidence      — насколько уверенно принято итоговое решение
#   Это разные оценки, не смешивать.

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ============================================================
# ENUMS
# ============================================================
class Intent(str, Enum):
    """Намерение пользователя. Отвечает на «что сделать?».

    Параметр (с чем/когда) — в Entity, не в Intent.
    Например: WEATHER_QUERY + entity(date="2026-10-31"), не WEATHER_TOMORROW.
    """
    # Музыка
    MUSIC_PLAY = "MUSIC_PLAY"        # включи/вруби/навали [параметр]
    MUSIC_WAVE = "MUSIC_WAVE"        # моя волна, русская волна
    MUSIC_STOP = "MUSIC_STOP"        # стоп
    MUSIC_PAUSE = "MUSIC_PAUSE"      # пауза
    MUSIC_RESUME = "MUSIC_RESUME"    # продолжи
    MUSIC_NEXT = "MUSIC_NEXT"        # дальше
    MUSIC_PREV = "MUSIC_PREV"        # назад

    # Громкость
    VOLUME_UP = "VOLUME_UP"
    VOLUME_DOWN = "VOLUME_DOWN"
    VOLUME_SET = "VOLUME_SET"

    # Погода
    WEATHER_QUERY = "WEATHER_QUERY"  # погода [date] [location]

    # Система
    SYSTEM_STATUS = "SYSTEM_STATUS"

    # Напоминания
    REMINDER_ADD = "REMINDER_ADD"

    # Очистка
    CLEANUP_DISK = "CLEANUP_DISK"
    CLEANUP_RECYCLE = "CLEANUP_RECYCLE"

    # Smalltalk
    SMALLTALK = "SMALLTALK"

    # LLM
    LLM_QUESTION = "LLM_QUESTION"

    # Не понято
    UNKNOWN = "UNKNOWN"


class EntityState(str, Enum):
    """Результат извлечения параметра. Отвечает на «что с параметром?»."""
    FOUND = "FOUND"              # параметр найден в каталоге
    ABSENT = "ABSENT"            # пользователь не указал параметр
    UNRESOLVED = "UNRESOLVED"    # параметр указан, но не найден в каталоге
    AMBIGUOUS = "AMBIGUOUS"      # несколько кандидатов с близкими оценками


class DecisionAction(str, Enum):
    """Действие системы. Отвечает на «что делать дальше?»."""
    EXECUTE = "EXECUTE"      # выполнить команду
    CLARIFY = "CLARIFY"      # уточнить у пользователя
    FALLBACK = "FALLBACK"    # отдать в LLM / NOT_UNDERSTOOD


# ============================================================
# ENTITY
# ============================================================
@dataclass
class EntityCandidate:
    """Один кандидат в Entity (для AMBIGUOUS)."""
    type: str                    # "artist", "genre", "track", "radio", "location", "date", ...
    value: str                   # "Ария", "рок", "Москва", "2026-10-31"
    match_confidence: float      # 0.0–1.0
    source: str = "unknown"      # "exact", "fuzzy", "phonetic", "alias"


@dataclass
class Entity:
    """Извлечённый параметр.

    state — результат извлечения (EntityState).
    value — найденное значение (только если FOUND).
    candidates — конкурирующие варианты (только если AMBIGUOUS).
    match_confidence — уверенность в извлечении (не путать с Decision.confidence).
    """
    state: EntityState
    type: Optional[str] = None           # "artist", "genre", "track", "radio", "location", "date"
    value: Optional[str] = None          # "Ария", "рок", "Москва", "2026-10-31"
    match_confidence: float = 0.0        # 0.0–1.0
    source: str = "unknown"              # "exact", "fuzzy", "phonetic", "alias", "none"
    candidates: list[EntityCandidate] = field(default_factory=list)

    # Удобные конструкторы
    @classmethod
    def absent(cls) -> "Entity":
        return cls(state=EntityState.ABSENT, source="none")

    @classmethod
    def found(cls, type_: str, value: str,
              match_confidence: float, source: str) -> "Entity":
        return cls(
            state=EntityState.FOUND,
            type=type_,
            value=value,
            match_confidence=match_confidence,
            source=source,
        )

    @classmethod
    def unresolved(cls, candidate_text: str,
                   match_confidence: float = 0.0) -> "Entity":
        return cls(
            state=EntityState.UNRESOLVED,
            value=candidate_text,
            match_confidence=match_confidence,
            source="none",
        )

    @classmethod
    def ambiguous(cls, candidates: list[EntityCandidate]) -> "Entity":
        best = max(candidates, key=lambda c: c.match_confidence) if candidates else None
        return cls(
            state=EntityState.AMBIGUOUS,
            type=best.type if best else None,
            value=None,
            match_confidence=best.match_confidence if best else 0.0,
            source="ambiguous",
            candidates=candidates,
        )


# ============================================================
# COMMAND
# ============================================================
@dataclass
class Command:
    """Структурированная команда для скила.

    Скил НЕ должен повторно распознавать original_text.
    Он получает готовый Command (intent + entity + action).
    """
    intent: Intent
    entity: Optional[Entity]
    skill_name: Optional[str]           # "music", "weather", "system", ...
    action: Optional[str]               # "play_artist", "play_genre", "play_wave", ...
    confidence: float                   # уверенность итоговой команды (0.0–1.0)
    normalized_text: str
    original_text: str
    context: dict = field(default_factory=dict)  # для ContextManager (этап 11)


# ============================================================
# DECISION
# ============================================================
@dataclass
class Decision:
    """Результат DecisionEngine.

    action — DecisionAction (EXECUTE/CLARIFY/FALLBACK), не EntityState.
    confidence — уверенность итогового решения (не match_confidence).
    """
    command: Command
    action: DecisionAction
    reason: str = ""
    confidence: float = 0.0
    clarify_question: Optional[str] = None  # если action=CLARIFY


# ============================================================
# DIAGNOSTIC EVENT (структура, не логгер)
# ============================================================
@dataclass
class DiagnosticEvent:
    """Структура диагностического события.

    Запись и форматирование — в brain/nlu/logging.py (этап 8, позже).
    Здесь только структура.
    """
    stage: str                       # "normalizer", "intent", "entity", "decision", "pipeline"
    level: str = "INFO"              # "DEBUG", "INFO", "WARN", "ERROR"
    message: str = ""
    data: dict = field(default_factory=dict)


# ============================================================
# CONFIDENCE — alias
# ============================================================
# Просто float, но с явным именем для читаемости.
Confidence = float