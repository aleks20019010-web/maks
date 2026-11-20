# brain/appraisal/policy.py
# Правила appraisal: когда событие превращается в реакцию.

import threading
import time
from dataclasses import dataclass

from brain.appraisal.events import Event, KIND_SKIP, KIND_PLAY, KIND_DISLIKE
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
        key = event.key()

        # Если трек дослушан или уже дизлайкнут — сбрасываем серию скипов этого ключа
        if event.kind in (KIND_PLAY, KIND_DISLIKE):
            if key != "unknown":
                self._history.reset(key)
            return Decision(reaction=False, key=key)

        if event.kind != KIND_SKIP or key == "unknown":
            return Decision(reaction=False, key=key)

        now = time.time() if now is None else now

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
            artist=event.artist.strip(),
        )