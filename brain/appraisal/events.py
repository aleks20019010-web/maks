# brain/appraisal/events.py
# Нормализованное событие для appraisal.
# Только данные, никакой логики. Логика — в history/policy/service.
#
# kind: SKIP, DISLIKE, PLAY
# source: откуда пришло ("controls", "music")
# artist / track: могут быть пустыми (например, SKIP без известного трека)

from dataclasses import dataclass, field
import time


KIND_SKIP = "SKIP"
KIND_DISLIKE = "DISLIKE"
KIND_PLAY = "PLAY"

VALID_KINDS = {KIND_SKIP, KIND_DISLIKE, KIND_PLAY}


@dataclass(frozen=True)
class Event:
    kind: str
    artist: str = ""
    track: str = ""
    source: str = ""
    ts: float = field(default_factory=time.time)

    def __post_init__(self):
        if self.kind not in VALID_KINDS:
            raise ValueError(f"Event: неизвестный kind={self.kind!r}")

    def key(self) -> str:
        """Ключ для группировки: артист, иначе трек, иначе пусто."""
        if self.artist:
            return f"artist:{self.artist}"
        if self.track:
            return f"track:{self.track}"
        return "unknown"

    def __str__(self) -> str:
        return f"Event({self.kind}, artist={self.artist!r}, track={self.track!r}, source={self.source})"