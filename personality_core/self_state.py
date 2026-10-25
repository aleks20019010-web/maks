# personality_core/self_state.py
# Внутреннее состояние Макса: mood, energy, valence, arousal.
#
# valence — это ОТКЛОНЕНИЕ от базового настроения. База зависит от энергии:
#   energy >= 0.8 → +0.4 (довольный)
#   energy >= 0.5 → +0.2 (спокойный)
#   energy >= 0.2 → 0.0 (нейтральный)
#   energy < 0.2  → -0.2 (уставший)
#
# События (похвала, неудачи) меняют valence временно. Потом затухает к 0.
# mood вычисляется из (base_valence + valence) × arousal.
#
# Сохраняется в data/self_state.json при каждом изменении.
# Загружается из data/self_state.json при старте.
#
# FIX: ENERGY_PER_COMMAND снижен с 0.01 до 0.0003. Макс создан для команд —
#      команды не должны истощать. 0.0003 × 100 команд = 0.03 (мягко).
# FIX: ENERGY_PER_TICK_NIGHT увеличен, чтобы за ночь (9 часов, 1080 тиков)
#      восстанавливаться с 0 до 1.0. 1.0 / 1080 = 0.000926, округлено до 0.00093.

from dataclasses import dataclass, field, asdict
import json
import os
import time

from shared import config as cfg


# ============================================================
# КОНСТАНТЫ
# ============================================================
ENERGY_PER_TICK_DAY      = 0.000400  # 1.0 → 0.15 за 15 часов (1800 тиков)
ENERGY_PER_TICK_NIGHT    = -0.00093  # 0.0 → 1.0 за 9 часов (1080 тиков)
ENERGY_PER_COMMAND       = 0.0003    # от команд падает (было 0.01, потом 0.003)
ENERGY_PER_SKIP          = 0.0005    # тратим за скип
ENERGY_RECOVER_THANKS    = 0.05      # восстанавливаем от "спасибо"
ENERGY_RESET_HELLO       = 1.0       # сбрасываем при "привет" (если >6ч)
ENERGY_RESET_HELLO_HOURS = 6.0

# Drowsy
DROWSY_ENERGY_THRESHOLD  = 0.15
DROWSY_IDLE_SEC          = 900.0
DROWSY_HOUR_START        = 21
DROWSY_HOUR_END          = 5

# Отклонения
VALENCE_POSITIVE = 0.15
VALENCE_NEGATIVE = -0.05

DECAY_RATE = 0.02
TICK_DECAY_RATE = 0.005
RECENT_SAID_MAX = 5

MIN_ENERGY = 0.0
MAX_ENERGY = 1.0
MIN_VALENCE = -1.0
MAX_VALENCE = 1.0
MIN_AROUSAL = 0.0
MAX_AROUSAL = 1.0

STATE_FILE = os.path.join(cfg.DATA_DIR, "self_state.json")


def _is_night_now() -> bool:
    h = time.localtime().tm_hour
    return h >= 23 or h < 8


def _is_drowsy_time_now() -> bool:
    h = time.localtime().tm_hour
    return h >= DROWSY_HOUR_START or h < DROWSY_HOUR_END


def _base_valence(energy: float) -> float:
    """Базовое настроение от энергии. Дворецкий доволен, если выспался."""
    if energy >= 0.8:
        return 0.4
    if energy >= 0.5:
        return 0.2
    if energy >= 0.2:
        return 0.0
    return -0.2


# ============================================================
# SELF-STATE
# ============================================================
@dataclass
class SelfState:
    mood: str = "neutral"
    energy: float = 1.0
    is_drowsy: bool = False

    valence: float = 0.0
    arousal: float = 0.4

    recent_said: list = field(default_factory=list)
    commands_answered: int = 0
    proactive_count: int = 0
    last_command_ts: float = 0.0
    last_tick_ts: float = 0.0
    last_hello_ts: float = 0.0
    session_start_ts: float = 0.0

    def __post_init__(self):
        if not self.session_start_ts:
            self.session_start_ts = time.time()

    # ========================================================
    # СОХРАНЕНИЕ / ЗАГРУЗКА
    # ========================================================
    def save(self):
        try:
            data = asdict(self)
            data["recent_said"] = [list(x) for x in data.get("recent_said", [])]
            os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"⚠ self_state.save: {e}")

    @classmethod
    def load(cls) -> "SelfState":
        if not os.path.exists(STATE_FILE):
            return cls()
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            rs = data.get("recent_said", [])
            data["recent_said"] = [tuple(x) if isinstance(x, list) else x for x in rs]
            allowed = {f.name for f in cls.__dataclass_fields__.values()}
            data = {k: v for k, v in data.items() if k in allowed}
            return cls(**data)
        except Exception as e:
            print(f"⚠ self_state.load: {e}")
            return cls()

    # ========================================================
    # ОБНОВЛЕНИЕ — команда
    # ========================================================
    def on_command(self, intent: str = "", **kw):
        now = time.time()
        self.commands_answered += 1
        self.last_command_ts = now

        self.energy = max(MIN_ENERGY, self.energy - ENERGY_PER_COMMAND)

        if intent == "THANKS":
            self.energy = min(MAX_ENERGY, self.energy + ENERGY_RECOVER_THANKS)
            self.valence = min(MAX_VALENCE, self.valence + VALENCE_POSITIVE)
            self.arousal = min(MAX_AROUSAL, self.arousal + 0.05)

        elif intent == "HELLO":
            hours_since = (now - self.last_hello_ts) / 3600.0 if self.last_hello_ts else 999
            if hours_since > ENERGY_RESET_HELLO_HOURS:
                self.energy = ENERGY_RESET_HELLO
                self.valence = 0.0
                self.arousal = 0.4
                self.last_hello_ts = now
            else:
                self.energy = min(MAX_ENERGY, self.energy + 0.05)
                self.valence = min(MAX_VALENCE, self.valence + 0.05)

        elif intent in ("REPEAT_ANNOYED", "NOT_UNDERSTOOD"):
            self.valence = max(MIN_VALENCE, self.valence + VALENCE_NEGATIVE)
            self.arousal = min(MAX_AROUSAL, self.arousal + 0.05)

        elif intent == "SKIP":
            self.energy = max(MIN_ENERGY, self.energy - ENERGY_PER_SKIP)

        self._decay()
        self._recompute_mood()
        self._recompute_drowsy()
        self.save()

    # ========================================================
    # ОБНОВЛЕНИЕ — тик
    # ========================================================
    def on_tick(self, **kw):
        self.last_tick_ts = time.time()

        if _is_night_now():
            self.energy = min(MAX_ENERGY, self.energy + abs(ENERGY_PER_TICK_NIGHT))
        else:
            self.energy = max(MIN_ENERGY, self.energy - ENERGY_PER_TICK_DAY)

        self._tick_decay()
        self._recompute_mood()
        self._recompute_drowsy()
        self.save()

    def on_response(self, text: str = ""):
        if not text:
            return
        self.recent_said.append((text, time.time()))
        if len(self.recent_said) > RECENT_SAID_MAX:
            self.recent_said = self.recent_said[-RECENT_SAID_MAX:]
        self.save()

    # ========================================================
    # ВНУТРЕННЕЕ
    # ========================================================
    def _recompute_drowsy(self):
        idle = (time.time() - self.last_command_ts) if self.last_command_ts else 9999
        tired = self.energy < DROWSY_ENERGY_THRESHOLD
        idle_long = idle > DROWSY_IDLE_SEC
        is_evening_night = _is_drowsy_time_now()
        self.is_drowsy = bool(tired and idle_long and is_evening_night)

    def _decay(self):
        """Затухание отклонения valence и arousal к базе/нейтрали."""
        if self.valence > 0:
            self.valence = max(0.0, self.valence - DECAY_RATE)
        elif self.valence < 0:
            self.valence = min(0.0, self.valence + DECAY_RATE)

        if self.arousal > 0.4:
            self.arousal = max(0.4, self.arousal - DECAY_RATE)
        elif self.arousal < 0.4:
            self.arousal = min(0.4, self.arousal + DECAY_RATE)

    def _tick_decay(self):
        if self.valence > 0:
            self.valence = max(0.0, self.valence - TICK_DECAY_RATE)
        elif self.valence < 0:
            self.valence = min(0.0, self.valence + TICK_DECAY_RATE)

        if self.arousal > 0.4:
            self.arousal = max(0.4, self.arousal - TICK_DECAY_RATE)
        elif self.arousal < 0.4:
            self.arousal = min(0.4, self.arousal + TICK_DECAY_RATE)

    def _recompute_mood(self):
        """Mood = база (от энергии) + отклонение (valence) × arousal."""
        base_v = _base_valence(self.energy)
        v = base_v + self.valence
        a = self.arousal

        if self.energy <= 0.2:
            self.mood = "tired"
            return

        if v > 0.3 and a > 0.5:
            self.mood = "playful"
        elif v > 0.1 and a > 0.5:
            self.mood = "excited"
        elif v > 0.1 and a <= 0.5:
            self.mood = "friendly"
        elif -0.1 <= v <= 0.1 and a > 0.5:
            self.mood = "focused"
        elif -0.1 <= v <= 0.1 and a <= 0.5:
            self.mood = "neutral"
        elif v < -0.3 and a > 0.5:
            self.mood = "sarcastic"
        elif v < -0.1 and a > 0.5:
            self.mood = "ironic"
        elif v < -0.1 and a <= 0.5:
            self.mood = "tired"
        else:
            self.mood = "neutral"

    # ========================================================
    # ЧТЕНИЕ
    # ========================================================
    def effective_valence(self) -> float:
        """Итоговое настроение с учётом базы."""
        return _base_valence(self.energy) + self.valence

    def is_tired(self) -> bool:
        return self.mood == "tired" or self.energy <= 0.3

    def is_positive(self) -> bool:
        return self.effective_valence() > 0.1

    def is_negative(self) -> bool:
        return self.effective_valence() < -0.1

    def is_high_energy(self) -> bool:
        return self.energy > 0.7

    def summary(self) -> str:
        drowsy = " drowsy" if self.is_drowsy else ""
        base_v = _base_valence(self.energy)
        return (f"mood={self.mood} "
                f"energy={self.energy:.2f} "
                f"valence={self.effective_valence():+.2f} "
                f"(base={base_v:+.1f} dev={self.valence:+.2f}) "
                f"arousal={self.arousal:.2f} "
                f"cmds={self.commands_answered}{drowsy}")


# ============================================================
# СИНГЛТОН
# ============================================================
_state: SelfState | None = None


def get() -> SelfState:
    global _state
    if _state is None:
        _state = SelfState.load()
    return _state


def reset():
    global _state
    _state = SelfState()