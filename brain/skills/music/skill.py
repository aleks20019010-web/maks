# brain/skills/music/skill.py
# MusicSkill — тонкая обёртка. Понимает команду, шлёт в music_inbox.
#
# A2 (день 29): TTS ДО play_index, с КАНОНИЧНЫМ именем из алиаса.
#   Раньше: «Ищу» → ждём music_started → «Ария. Неплохо» → музыка.
#   Задержка 3-5 сек (поиск, скачивание, старт). Долго.
#   Теперь: «Король и Шут. Включаю» ДО ipc.put(play_artist) → музыка
#   стартует сразу после поиска. Один TTS, без pending.
#   Имя для TTS — из config/aliases.json (если алиас есть),
#   иначе пользовательское («включи киша» → «Король и Шут»).
#
#   resolve_alias_name читает config/aliases.json напрямую (brain и music —
#   разные процессы, brain не видит workers/music/aliases.py).
#
# FIX (день 28): _try_ask больше не пишет свои hour < 9 / hour >= 23.
#   time_bucket() из shared/time_bucket.py — один источник правды.

import json
import time

from brain.skills.base import Skill, register
from brain.skills._helpers import respond_and_say as _respond_and_say
from brain.skills.music._shared import _strip_stop_prefix
from brain.skills.music.classify import classify
from brain import relationship
from brain import pending
from shared import ipc
from shared import protocol
from shared import config as cfg
from shared.log import log
from shared.time_bucket import time_bucket


PENDING_KEY = "music_ask"


# ── Алиасы (A2) ───────────────────────────────────────────────
def _resolve_alias_name(query: str) -> str | None:
    """Прочитать config/aliases.json, вернуть artist_name или None.

    brain и music — разные процессы. brain не видит workers/music/aliases.py,
    поэтому читаем файл напрямую. Кэш не нужен — файл маленький, читается
    только при play_artist / play_track.
    """
    if not query:
        return None
    path = cfg.ALIASES_FILE
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    q = query.lower().strip()
    entry = data.get(q)
    if isinstance(entry, dict):
        name = entry.get("artist_name")
        if name:
            return name
    return None


def _get_artist_weight(artist: str) -> float:
    try:
        from memory import memory
        return float(memory.get_artist_weight(artist) or 0.0)
    except Exception:
        return 0.0


def _get_track_weight_by_title(title: str) -> float:
    try:
        from memory import memory
        return float(memory.get_track_weight(title) or 0.0)
    except Exception:
        return 0.0


def _plays_artist_today(artist: str) -> int:
    try:
        from memory import memory
        return int(memory.plays_artist_today(artist) or 0)
    except Exception:
        return 0


class MusicSkill(Skill):
    name = "music"
    priority = 40

    def can_handle(self, text: str) -> bool:
        t = text.lower().strip()
        stripped = _strip_stop_prefix(t)
        if not stripped:
            return False
        return classify(text) is not None

    def handle(self, text: str) -> str | None:
        result = classify(text)
        if result is None:
            return None

        action = result.get("action")
        log(f"music: {action} ← «{text}»", tag="BRAIN")

        if action == "play_wave" and result.get("language") is None:
            if self._try_ask():
                return None

        self._dispatch(action, result)
        return None

    def _try_ask(self) -> bool:
        try:
            from brain.skills.weather.sources import fetch_weather
            from brain.skills.weather.classify import classify as wclassify
            wdata = fetch_weather()
            wvars = wclassify(wdata)
            precip = wvars.get("precip", "")
        except Exception as e:
            log(f"music: weather ошибка: {e}", tag="BRAIN", level="WARN")
            precip = ""

        if precip in ("rain", "snow", "storm"):
            if not relationship.weather_known(precip):
                phrase_key = {
                    "rain": "MUSIC_ASK_RAIN",
                    "snow": "MUSIC_ASK_SNOW",
                    "storm": "MUSIC_ASK_STORM",
                }[precip]
                self._ask(phrase_key, kind="weather",
                          data={"condition": precip})
                return True

        recent = relationship.get_recent_prompt()
        if recent:
            self._ask("MUSIC_ASK_RECENT", kind="recent",
                      data={"artist": recent["name"]},
                      phrase_kwargs={"artist": recent["name"]})
            return True

        bucket = time_bucket()
        if bucket == "morning" and not relationship.was_asked_today("morning"):
            self._ask("MUSIC_ASK_MORNING", kind="time",
                      data={"time_of_day": "morning"})
            return True
        if bucket == "night" and not relationship.was_asked_today("night"):
            self._ask("MUSIC_ASK_NIGHT", kind="time",
                      data={"time_of_day": "night"})
            return True

        return False

    def _ask(self, phrase_key: str, kind: str,
             data: dict, phrase_kwargs: dict | None = None):
        kwargs = phrase_kwargs or {}
        _respond_and_say(phrase_key, **kwargs)

        def _on_timeout(_data):
            _respond_and_say("MUSIC_TIMEOUT")
            ipc.put(protocol.MUSIC_INBOX, protocol.play_wave(language=None))

        pending.set(
            PENDING_KEY,
            timeout=10.0,
            on_timeout=_on_timeout,
            data={"kind": kind, **data},
            wait_tts=True,
        )
        log(f"music: спросил ({kind}), pending={PENDING_KEY} (wait_tts)", tag="BRAIN")

    def _dispatch(self, action: str, result: dict):
        if action == "play_artist":
            artist = result["artist"]
            # A2: имя для TTS — из алиаса (каноничное), иначе пользовательское.
            canonical = _resolve_alias_name(artist)
            name_for_tts = canonical or artist
            # TTS ДО play_index — без задержки на поиск.
            _respond_and_say("PLAY_ARTIST_STARTED", artist=name_for_tts)
            ipc.put(protocol.MUSIC_INBOX, protocol.play_artist(artist))

        elif action == "play_track":
            track = result["track"]
            # A2: имя для TTS — из алиаса (если трек — алиас артиста), иначе трек.
            canonical = _resolve_alias_name(track)
            name_for_tts = canonical or track
            _respond_and_say("PLAY_TRACK_STARTED", title=name_for_tts)
            ipc.put(protocol.MUSIC_INBOX, protocol.play_track(track))

        elif action == "play_genre":
            genre = result["genre"]
            station = result.get("station", "")
            _respond_and_say("PLAY_GENRE", genre=genre)
            ipc.put(protocol.MUSIC_INBOX, protocol.play_genre(genre, station=station))

        elif action == "play_wave":
            language = result.get("language")
            if language == "russian":
                _respond_and_say("PLAY_PLAYLIST", name="русскую волну")
            else:
                _respond_and_say("PLAY_PLAYLIST", name="мою волну")
            ipc.put(protocol.MUSIC_INBOX, protocol.play_wave(language=language))

        elif action == "play_liked":
            _respond_and_say("PLAY_PLAYLIST", name="твою музыку")
            ipc.put(protocol.MUSIC_INBOX, protocol.play_liked())

        elif action == "play_radio":
            station = result["station"]
            ipc.put(protocol.MUSIC_INBOX, protocol.play_radio(station))

        elif action == "dislike":
            _respond_and_say("MUSIC_DISLIKE")
            ipc.put(protocol.MUSIC_INBOX, protocol.music_dislike())


def on_music_started(artist: str, title: str) -> None:
    """A2: TTS был ДО play_index (PLAY_ARTIST_STARTED). Здесь — пусто.
    Музыка уже играет, TTS не нужен.
    """
    pass


register(MusicSkill())