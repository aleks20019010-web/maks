# brain/appraisal/events.py
# Нормализованное событие для appraisal.

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
        """Нормализованный ключ для группировки: артист, иначе трек, иначе unknown."""
        norm_artist = self.artist.strip().lower()
        if norm_artist:
            return f"artist:{norm_artist}"
        norm_track = self.track.strip().lower()
        if norm_track:
            return f"track:{norm_track}"
        return "unknown"

    def __str__(self) -> str:
        return f"Event({self.kind}, artist={self.artist!r}, track={self.track!r}, source={self.source})"