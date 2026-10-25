# brain/skills/music/skill.py
# MusicSkill — тонкая обёртка. Понимает команду, шлёт в music_inbox.
#
# Логика вопросов (погода → музыка, вчерашнее, время):
#   1. Погода плохая + нет привычки → спросить (pending "music_ask")
#   2. Вчерашнее есть + не спрашивали → спросить
#   3. Утро/ночь + не спрашивали сегодня → спросить
#   4. Иначе — play_wave (как обычно)
#
# Ответ на вопрос обрабатывает router.py (pending "music_ask").
#
# Dislike: brain шлёт dislike в music_inbox. Music blacklist текущего трека.
#
# SUGGEST / REPEAT:
#   - Если артист/трек с высоким весом (>40) — "Я как раз собирался предложить".
#   - Если артист/трек уже играл 2+ раза сегодня — "Снова. Ну хорошо, сэр" (сарказм).

import time

from brain.skills.base import Skill, register
from brain.skills._helpers import respond_and_say as _respond_and_say
from brain.skills.music._shared import _strip_stop_prefix
from brain.skills.music.classify import classify
from brain import relationship
from brain import pending
from shared import ipc
from shared import protocol
from shared.log import log


PENDING_KEY = "music_ask"
SUGGEST_WEIGHT_THRESHOLD = 40.0
REPEAT_TODAY_THRESHOLD = 2


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

        # Только для play_wave без языка — пробуем спросить
        if action == "play_wave" and result.get("language") is None:
            if self._try_ask():
                return None

        # Обычный путь
        self._dispatch(action, result)
        return None

    # ────────────────────────────────────────────────────────
    # ВОПРОСЫ
    # ────────────────────────────────────────────────────────
    def _try_ask(self) -> bool:
        """Попробовать задать вопрос. True — задали, play_wave не нужен."""
        # 1. Погода
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

        # 2. Вчерашнее
        recent = relationship.get_recent_prompt()
        if recent:
            self._ask("MUSIC_ASK_RECENT", kind="recent",
                      data={"artist": recent["name"]},
                      phrase_kwargs={"artist": recent["name"]})
            return True

        # 3. Время суток
        hour = time.localtime().tm_hour
        if hour < 9 and not relationship.was_asked_today("morning"):
            self._ask("MUSIC_ASK_MORNING", kind="time",
                      data={"time_of_day": "morning"})
            return True
        if hour >= 23 and not relationship.was_asked_today("night"):
            self._ask("MUSIC_ASK_NIGHT", kind="time",
                      data={"time_of_day": "night"})
            return True

        return False

    def _ask(self, phrase_key: str, kind: str,
             data: dict, phrase_kwargs: dict | None = None):
        """Задать вопрос + поставить pending после TTS."""
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

    # ────────────────────────────────────────────────────────
    # ОБЫЧНЫЙ ПУТЬ
    # ────────────────────────────────────────────────────────
    def _dispatch(self, action: str, result: dict):
        if action == "play_artist":
            artist = result["artist"]
            weight = _get_artist_weight(artist)
            today = _plays_artist_today(artist)

            if today >= REPEAT_TODAY_THRESHOLD:
                _respond_and_say("REPEAT_ARTIST", artist=artist)
            elif weight > SUGGEST_WEIGHT_THRESHOLD:
                _respond_and_say("WAS_GOING_TO_SUGGEST_ARTIST", artist=artist)
            else:
                _respond_and_say("PLAY_ARTIST", artist=artist)

            ipc.put(protocol.MUSIC_INBOX, protocol.play_artist(artist))

        elif action == "play_track":
            track = result["track"]
            weight = _get_track_weight_by_title(track)

            if weight > SUGGEST_WEIGHT_THRESHOLD:
                _respond_and_say("WAS_GOING_TO_SUGGEST_TRACK", title=track)
            else:
                _respond_and_say("PLAY_TRACK", title=track)

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


register(MusicSkill())