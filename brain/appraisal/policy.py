# brain/appraisal/policy.py
# Правила appraisal: когда событие превращается в реакцию.
#
# Сейчас одно правило: 3 SKIP одного ключа за эпизод → REACT.
# Кулдаун — чтобы не реагировать каждым следующим скипом.
# «Не реагировал» хранится как None, а не 0.0 — чтобы не ловить
# ложный кулдаун в тестах с маленьким now.
#
# Решение, не действие. Сброс history — в service.py.
# artist передаётся в Decision, чтобы service мог подставить {artist} в фразу.

import threading
import time
from dataclasses import dataclass

from brain.appraisal.events import Event, KIND_SKIP
from brain.appraisal.history import History


SKIP_COUNT_THRESHOLD = 3
REACTION_COOLDOWN_SEC = 600.0  # 10 минут
APPRAISAL_SKIP_PHRASE_SET = "APPRAISAL_SKIP_3X"


@dataclass(frozen=True)
class Decision:
    reaction: bool
    phrase_set: str | None = None
    key: str = ""
    artist: str = ""


class Policy:
    def __init__(
        self,
        history: History,
        threshold: int = SKIP_COUNT_THRESHOLD,
        cooldown_sec: float = REACTION_COOLDOWN_SEC,
    ):
        self._history = history
        self._threshold = threshold
        self._cooldown = cooldown_sec
        self._lock = threading.Lock()
        self._last_reaction: dict[str, float] = {}

    def evaluate(self, event: Event, now: float | None = None) -> Decision:
        if event.kind != KIND_SKIP:
            return Decision(reaction=False, key=event.key())

        now = time.time() if now is None else now
        key = event.key()

        count = self._history.record(event, now=now)
        if count < self._threshold:
            return Decision(reaction=False, key=key)

        with self._lock:
            last = self._last_reaction.get(key)
            if last is not None and (now - last) < self._cooldown:
                return Decision(reaction=False, key=key)
            self._last_reaction[key] = now

        return Decision(
            reaction=True,
            phrase_set=APPRAISAL_SKIP_PHRASE_SET,
            key=key,
            artist=event.artist,
        )