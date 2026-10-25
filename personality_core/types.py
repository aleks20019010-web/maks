"""
personality_core/types.py — типы данных Personality Core.

Три ключевых dataclass:
    Event          — что пришло в ядро
    Context        — что происходит сейчас + ленивые запросы к памяти
    JarvisResponse — что вернуть наружу

JarvisResponse.text = None означает "молчать" — валидный ответ.
"""

from dataclasses import dataclass, field
from typing import Any, Optional
from datetime import datetime


@dataclass
class Event:
    """Событие, пришедшее в Personality Core.

    type: "command" | "event" | "tick"
    name: для event — имя события, для command — intent
    """
    type: str
    name: str = ""
    text: str = ""
    data: dict = field(default_factory=dict)
    timestamp: float = 0.0
    source: str = "system"          # voice | keyboard | system | memory | bot


@dataclass
class Context:
    """Собранный контекст ситуации.

    Содержит "статические" поля (время, запрос, музыка) +
    ленивые методы (запросы к памяти по требованию).

    Ленивые методы кэшируются в _cache — правило спрашивает один раз.
    """
    # --- Время ---
    now: Optional[datetime] = None
    time_of_day: str = ""
    day_of_week: str = ""
    hour: int = 0
    is_weekend: bool = False

    # --- Текущий запрос ---
    query: str = ""
    query_intent: str = ""
    query_source: str = ""

    # --- Музыка (сейчас) ---
    current_track: str = ""
    current_artist: str = ""
    current_mode: str = ""

    # --- Сырое событие ---
    event: Optional[Event] = None

    # --- Внутреннее: ссылка на preferences + кэш ---
    _prefs: Any = None
    _cache: dict = field(default_factory=dict)

    # --- Self-state (ссылка на синглтон) ---
    self_state: Any = None

    # --- Character (ссылка на синглтон) ---
    character: Any = None

    # --- Identity (ссылка на синглтон) ---
    identity: Any = None

    # ========================================================
    # ЛЕНИВЫЕ МЕТОДЫ — запросы к memory по требованию
    # ========================================================
    def _get(self, key, func):
        """Кэширующий вызов."""
        if key not in self._cache:
            try:
                self._cache[key] = func()
            except Exception as e:
                print(f"⚠ context.{key}: {e}")
                self._cache[key] = None
        return self._cache[key]

    # --- Плеи по периоду ---
    def plays_last_10min(self):
        return self._get("plays_last_10min",
                         lambda: self._prefs.plays_last_10min() if self._prefs else {})

    def plays_last_hour(self):
        return self._get("plays_last_hour",
                         lambda: self._prefs.plays_last_hour() if self._prefs else {})

    def plays_last_day(self):
        return self._get("plays_last_day",
                         lambda: self._prefs.plays_last_day() if self._prefs else {})

    def plays_last_week(self):
        return self._get("plays_last_week",
                         lambda: self._prefs.plays_last_week() if self._prefs else {})

    # --- Поведение ---
    def skips_last_hour(self):
        return self._get("skips_last_hour",
                         lambda: self._prefs.skips_last_hour() if self._prefs else 0)

    def finishes_last_hour(self):
        return self._get("finishes_last_hour",
                         lambda: self._prefs.finishes_last_hour() if self._prefs else 0)

    def diversity_last_day(self):
        return self._get("diversity_last_day",
                         lambda: self._prefs.diversity_last_day() if self._prefs else 0)

    # --- Тренды ---
    def streak_artist(self, artist: str) -> int:
        key = f"streak_artist.{artist}"
        return self._get(key,
                         lambda: self._prefs.artist_streak_days(artist) if self._prefs else 0)

    def days_since_artist(self, artist: str):
        key = f"days_since_artist.{artist}"
        return self._get(key,
                         lambda: self._prefs.days_since_artist(artist) if self._prefs else None)

    def genre_diversity_7d(self):
        return self._get("genre_diversity_7d",
                         lambda: self._prefs.genre_diversity_days(7) if self._prefs else 0)

    def top_artist_hour(self, hour: int):
        key = f"top_artist_hour.{hour}"
        return self._get(key,
                         lambda: self._prefs.top_artist_by_hour(hour) if self._prefs else None)

    # --- Команды ---
    def commands_last_2min(self):
        return self._get("commands_last_2min",
                         lambda: self._prefs.commands_last_2min() if self._prefs else 0)

    def last_request(self):
        return self._get("last_request",
                         lambda: self._prefs.last_request() if self._prefs else None)

    # --- Метрики для правил ---
    def plays_artist_last_hour(self, artist: str) -> int:
        """Сколько раз артист играл за час."""
        plays = self.plays_last_hour() or {}
        return plays.get(artist, 0)

    def plays_artist_today(self, artist: str) -> int:
        """Сколько раз артист играл с начала дня (с 8:00)."""
        if not self._prefs:
            return 0
        try:
            return self._prefs.plays_artist_today(artist)
        except Exception:
            return 0

    def plays_artist_last_day(self, artist: str) -> int:
        """Сколько раз артист играл за сутки."""
        plays = self.plays_last_day() or {}
        return plays.get(artist, 0)

    # ========================================================
    # SELF-STATE (mood, energy, valence, arousal)
    # ========================================================
    def mood(self) -> str:
        """Текущее настроение Макса."""
        if self.self_state is None:
            return "neutral"
        return self.self_state.mood

    def energy(self) -> float:
        """Текущая энергия Макса."""
        if self.self_state is None:
            return 1.0
        return self.self_state.energy

    def valence(self) -> float:
        """Текущая валентность (-1..+1)."""
        if self.self_state is None:
            return 0.0
        return self.self_state.valence

    def arousal(self) -> float:
        """Текущий уровень возбуждения (0..1)."""
        if self.self_state is None:
            return 0.4
        return self.self_state.arousal

    # ========================================================
    # STYLE (вычисляется по контексту)
    # ========================================================
    def style(self) -> str:
        """Текущий стиль ответа (normal / formal / night / tired / ...)."""
        try:
            from personality_core.style import pick_style
            return pick_style(self)
        except Exception as e:
            print(f"⚠ context.style(): {e}")
            return "normal"

    # ========================================================
    # IDENTITY (манифест «кто я»)
    # ========================================================
    def identity_name(self) -> str:
        """Имя Макса."""
        if self.identity is None:
            return "Макс"
        return self.identity.name

    def identity_about(self) -> str:
        """Полный манифест — для WHO_ARE_YOU."""
        if self.identity is None:
            return "Я — Макс."
        return self.identity.about()

    def identity_can(self) -> str:
        """Что умею — для WHAT_CAN_YOU_DO."""
        if self.identity is None:
            return "Умею слушать."
        return self.identity.can()

    def identity_wont(self) -> str:
        """Чего не делаю — для объяснений."""
        if self.identity is None:
            return ""
        return self.identity.wont()


@dataclass
class JarvisResponse:
    """Ответ Personality Core.

    text=None → молчать (валидный ответ).
    actions — что выполнить после TTS.
    explain — для отладки, какое правило сработало.
    """
    text: Optional[str] = None
    rate: Optional[str] = None
    pitch: Optional[str] = None
    emotion: Optional[str] = None

    pause_before: float = 0.0
    pause_after: float = 0.0

    actions: list = field(default_factory=list)
    explain: str = ""

    def is_silent(self) -> bool:
        return self.text is None