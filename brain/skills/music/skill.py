# brain/skills/music/skill.py
# MusicSkill — тонкая обёртка. Понимает команду, шлёт в music_inbox.
#
# A2-фикс (день 30): _try_ask — только утром, один раз в день.
#   Раньше: спрашивал при каждом play_wave, TTL 7 дней. Теперь:
#   утро (time_bucket == "morning"), был ли уже вопрос сегодня
#   (was_asked_today("weather_" + precip)). Если да — не спрашивает.
#   Перед вопросом — mark_asked_today. Завтра — новый день, снова спросит
#   (может, именно завтра захочешь спокойное).
#
# A2: TTS ДО play_index, с каноничным именем из алиаса. Один TTS.
#
# MOOD (новое): action="play_mood" → protocol.play_mood(mood_tag).
#   mood_tag — tag метатега («Энергичное»). Воркер музыки делает
#   client.metatag(tag) → station_id (mood:energetic) → start_genre.
#
# ЭТАП 9 (C, часть 1): handle принимает Command | str.
#   - Command (NLU-путь): диспатч по command.action, classify НЕ зовём.
#     Entity уже извлечён и сматчен — повторный парсинг не нужен.
#   - str (fast-path): classify(text) → _dispatch, как раньше.
#   router._execute_decision передаёт Command для music, str для остальных.
#   Переходный период: controls/weather/cleanup/smalltalk — пока на str.

import json

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


def _resolve_alias_name(query: str) -> str | None:
    """Прочитать config/aliases.json, вернуть artist_name или None."""
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


class MusicSkill(Skill):
    name = "music"
    priority = 40

    def can_handle(self, text: str) -> bool:
        t = text.lower().strip()
        stripped = _strip_stop_prefix(t)
        if not stripped:
            return False
        return classify(text) is not None

    def handle(self, text_or_command) -> str | None:
        """Handle Command (NLU-путь) или str (fast-path).

        Command → диспатч по command.action, classify НЕ зовём.
        str     → classify(text) → _dispatch, как раньше.
        """
        # ── NLU-путь: Command ──────────────────────────────
        from brain.nlu.types import Command
        if isinstance(text_or_command, Command):
            command = text_or_command
            action = command.action
            if not action:
                log("music: Command без action — пропуск", tag="BRAIN", level="WARN")
                return None
            result = self._command_to_result(command, action)
            if result is None:
                log(f"music: Command action={action} — не удалось собрать result",
                    tag="BRAIN", level="WARN")
                return None
            log(f"music: {action} ← Command «{command.original_text}»", tag="BRAIN")
            self._dispatch(action, result)
            return None

        # ── fast-path: str ─────────────────────────────────
        text = text_or_command
        if not text:
            return None
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

    def _command_to_result(self, command, action: str) -> dict | None:
        """Command → dict для _dispatch. По action и entity.

        play_artist  → {"artist": entity.value}
        play_track   → {"track": entity.value}
        play_genre   → {"genre": entity.value, "station": ""}  (station — из GENRE_ALIASES)
        play_mood    → {"mood": entity.value}
        play_wave    → {"action": "play_wave", "language": None}
        play_liked   → {"action": "play_liked"}
        play_radio   → {"station": entity.value}  (если radio-entity)
        """
        result: dict = {"action": action}
        ent = getattr(command, "entity", None)

        if action == "play_wave":
            result["language"] = None
            return result
        if action == "play_liked":
            return result

        if ent is None or not ent.value:
            return None

        if action == "play_artist":
            result["artist"] = ent.value
            return result
        if action == "play_track":
            result["track"] = ent.value
            return result
        if action == "play_mood":
            result["mood"] = ent.value
            return result
        if action == "play_genre":
            result["genre"] = ent.value
            # station — из GENRE_ALIASES (как в classify)
            try:
                from workers.music._shared import GENRE_ALIASES
                result["station"] = GENRE_ALIASES.get(ent.value, f"genre:{ent.value}")
            except Exception:
                result["station"] = f"genre:{ent.value}"
            return result
        if action == "play_radio":
            result["station"] = ent.value
            return result

        return None

    def _try_ask(self) -> bool:
        """Спросить про погоду (утром, один раз в день) или вчерашнее.

        A2-фикс: погода — только утром, только если сегодня не спрашивал.
        Если спросили (да/нет/timeout) — mark_asked_today, больше сегодня
        не спрашиваем. Завтра — новый день, снова спросит.
        """
        bucket = time_bucket()
        if bucket == "morning":
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
                asked_key = f"weather_{precip}"
                if not relationship.was_asked_today(asked_key):
                    phrase_key = {
                        "rain": "MUSIC_ASK_RAIN",
                        "snow": "MUSIC_ASK_SNOW",
                        "storm": "MUSIC_ASK_STORM",
                    }[precip]
                    self._ask(phrase_key, kind="weather",
                              data={"condition": precip})
                    relationship.mark_asked_today(asked_key)
                    return True

        recent = relationship.get_recent_prompt()
        if recent:
            self._ask("MUSIC_ASK_RECENT", kind="recent",
                      data={"artist": recent["name"]},
                      phrase_kwargs={"artist": recent["name"]})
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
            canonical = _resolve_alias_name(artist)
            name_for_tts = canonical or artist
            _respond_and_say("PLAY_ARTIST_STARTED", artist=name_for_tts)
            ipc.put(protocol.MUSIC_INBOX, protocol.play_artist(artist))

        elif action == "play_track":
            track = result["track"]
            canonical = _resolve_alias_name(track)
            name_for_tts = canonical or track
            _respond_and_say("PLAY_TRACK_STARTED", title=name_for_tts)
            ipc.put(protocol.MUSIC_INBOX, protocol.play_track(track))

        elif action == "play_genre":
            genre = result["genre"]
            station = result.get("station", "")
            _respond_and_say("PLAY_GENRE", genre=genre)
            ipc.put(protocol.MUSIC_INBOX, protocol.play_genre(genre, station=station))

        elif action == "play_mood":
            # MOOD: «Энергичное» → protocol.play_mood(mood_tag).
            # Воркер: client.metatag(tag) → station_id (mood:energetic) →
            # modes.start_genre(station_id, mood_tag).
            mood_tag = result["mood"]
            _respond_and_say("PLAY_MOOD", mood=mood_tag)
            ipc.put(protocol.MUSIC_INBOX, protocol.play_mood(mood_tag))

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
    """A2: TTS был ДО play_index (PLAY_ARTIST_STARTED). Здесь — пусто."""
    pass


register(MusicSkill())