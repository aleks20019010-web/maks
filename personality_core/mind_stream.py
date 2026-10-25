"""
personality_core/mind_stream.py — MindStream.

Накопитель контекста сессии. Не говорит сам — поставляет данные
для Context и ResponseBuilder.

Что хранит:
    - session_start        — когда началась сессия
    - last_events          — последние 50 событий (type, name, ts)
    - recent_artists       — последние 10 артистов
    - recent_tracks        — последние 10 треков
    - counters             — plays / skips / finishes / repeats / commands
    - mood_history         — последние 20 смен настроения
    - last_command_ts      — когда была последняя команда

Обновляется:
    - engine._process      — на каждую команду (via on_command)
    - tick_worker          — раз в 30 сек (via on_tick)
    - skills.music         — на play/skip/finish (via on_play / on_skip / on_finish)

Сбрасывается:
    - reset()              — для тестов
"""

import time
from collections import deque
from dataclasses import dataclass, field


MAX_EVENTS = 50
MAX_RECENT = 10
MAX_MOOD_HISTORY = 20

# Порог «активной сессии» — если нет команд N секунд, начинаем новую.
SESSION_IDLE_SEC = 1800  # 30 минут


@dataclass
class MindStream:
    session_start_ts: float = 0.0
    last_command_ts: float = 0.0

    last_events: deque = field(default_factory=lambda: deque(maxlen=MAX_EVENTS))
    recent_artists: deque = field(default_factory=lambda: deque(maxlen=MAX_RECENT))
    recent_tracks: deque = field(default_factory=lambda: deque(maxlen=MAX_RECENT))
    mood_history: deque = field(default_factory=lambda: deque(maxlen=MAX_MOOD_HISTORY))

    counters: dict = field(default_factory=lambda: {
        "commands": 0,
        "plays": 0,
        "skips": 0,
        "finishes": 0,
        "repeats": 0,
    })

    def __post_init__(self):
        if not self.session_start_ts:
            self.session_start_ts = time.time()

    # ========================================================
    # ОБНОВЛЕНИЕ
    # ========================================================
    def on_command(self, intent: str = "", **kw):
        """Каждая команда — обновляем."""
        self._check_session_reset()
        self.last_command_ts = time.time()
        self.counters["commands"] += 1
        self._add_event("command", intent, kw)

        # Повтор?
        if intent == "REPEAT_ANNOYED":
            self.counters["repeats"] += 1

    def on_play(self, artist: str = "", track: str = ""):
        """Трек начал играть."""
        self.counters["plays"] += 1
        if artist:
            self.recent_artists.append((artist, time.time()))
        if track:
            self.recent_tracks.append((track, time.time()))
        self._add_event("play", track, {"artist": artist})

    def on_skip(self, artist: str = "", track: str = ""):
        """Трек пропущен."""
        self.counters["skips"] += 1
        self._add_event("skip", track, {"artist": artist})

    def on_finish(self, artist: str = "", track: str = ""):
        """Трек дослушан."""
        self.counters["finishes"] += 1
        self._add_event("finish", track, {"artist": artist})

    def on_tick(self, mood: str = ""):
        """Тик от tick_worker — раз в 30 сек."""
        if mood:
            self.mood_history.append((time.time(), mood))
        self._add_event("tick", "", {"mood": mood})

    # ========================================================
    # ЧТЕНИЕ
    # ========================================================
    def session_duration_sec(self) -> float:
        return time.time() - self.session_start_ts

    def is_fresh_session(self) -> bool:
        """True, если сессия только началась (< 5 минут)."""
        return self.session_duration_sec() < 300

    def artists_in_session(self) -> list:
        """Уникальные артисты за сессию, в порядке появления."""
        seen = []
        for a, _ in self.recent_artists:
            if a not in seen:
                seen.append(a)
        return seen

    def top_artist_session(self) -> str:
        """Самый частый артист сессии."""
        counts = {}
        for a, _ in self.recent_artists:
            counts[a] = counts.get(a, 0) + 1
        if not counts:
            return ""
        return max(counts, key=counts.get)

    def plays_this_session(self, artist: str) -> int:
        """Сколько раз артист играл в этой сессии."""
        return sum(1 for a, _ in self.recent_artists if a == artist)

    def last_artist(self) -> str:
        if self.recent_artists:
            return self.recent_artists[-1][0]
        return ""

    def last_track(self) -> str:
        if self.recent_tracks:
            return self.recent_tracks[-1][0]
        return ""

    def last_mood(self) -> str:
        if self.mood_history:
            return self.mood_history[-1][1]
        return ""

    def summary(self) -> str:
        """Для логов."""
        dur = int(self.session_duration_sec())
        return (f"session={dur}s "
                f"cmds={self.counters['commands']} "
                f"plays={self.counters['plays']} "
                f"skips={self.counters['skips']}")

    # ========================================================
    # ВНУТРЕННЕЕ
    # ========================================================
    def _add_event(self, etype: str, name: str, data: dict = None):
        self.last_events.append({
            "ts": time.time(),
            "type": etype,
            "name": name,
            "data": data or {},
        })

    def _check_session_reset(self):
        """Если долго не было команд — начинаем новую сессию."""
        now = time.time()
        if self.last_command_ts and (now - self.last_command_ts) > SESSION_IDLE_SEC:
            print(f"   🧠 mind_stream: новая сессия (idle {int(now - self.last_command_ts)}s)")
            self._reset_counters()
            self.session_start_ts = now
            self.last_events.clear()
            self.recent_artists.clear()
            self.recent_tracks.clear()

    def _reset_counters(self):
        self.counters = {
            "commands": 0,
            "plays": 0,
            "skips": 0,
            "finishes": 0,
            "repeats": 0,
        }


# ============================================================
# СИНГЛТОН
# ============================================================
_stream: MindStream | None = None


def get() -> MindStream:
    global _stream
    if _stream is None:
        _stream = MindStream()
    return _stream


def reset():
    global _stream
    _stream = MindStream()