# brain/skills/music/skill.py
# MusicSkill — тонкая обёртка. Понимает Command, шлёт в music_inbox.

import json
import os
import random
import threading
import time

from brain.skills.base import Skill, register
from brain.skills._helpers import respond_and_say as _respond_and_say
from brain import relationship
from brain import pending
from shared import ipc
from shared import protocol
from shared import config as cfg
from shared.log import log
from shared.time_bucket import time_bucket


PENDING_KEY = "music_ask"

_TTS_START_WAIT_SEC = 5.0
_TTS_POLL_SEC = 0.02
_ANNOUNCE_TTL_SEC = 15.0

_dispatch_seq = 0
_dispatch_lock = threading.Lock()

_announce_next_track = False
_announce_armed_at = 0.0
_announce_lock = threading.Lock()

_radio_cache: dict = {}
_radio_mtime: float = -1.0


def arm_first_track_announce() -> None:
    """Разрешить озвучку ровно одного следующего события music_started (TTL 15с)."""
    global _announce_next_track, _announce_armed_at
    with _announce_lock:
        _announce_next_track = True
        _announce_armed_at = time.time()


def disarm_first_track_announce() -> None:
    """Отменить ожидание озвучки следующего трека."""
    global _announce_next_track, _announce_armed_at
    with _announce_lock:
        _announce_next_track = False
        _announce_armed_at = 0.0


def consume_first_track_announce() -> bool:
    """Проверить и атомарно сбросить флаг озвучки первого трека (с учётом TTL)."""
    global _announce_next_track, _announce_armed_at
    with _announce_lock:
        val = _announce_next_track
        armed_at = _announce_armed_at
        _announce_next_track = False
        _announce_armed_at = 0.0
        if not val:
            return False
        if (time.time() - armed_at) > _ANNOUNCE_TTL_SEC:
            return False
        return True


def _next_dispatch_seq() -> int:
    global _dispatch_seq
    with _dispatch_lock:
        _dispatch_seq += 1
        return _dispatch_seq


def _current_dispatch_seq() -> int:
    with _dispatch_lock:
        return _dispatch_seq


def _load_radio_stations() -> dict:
    global _radio_cache, _radio_mtime
    path = os.path.join(cfg.BASE_DIR, "config", "radio_stations.json")
    if not os.path.exists(path):
        _radio_cache = {}
        _radio_mtime = -1.0
        return _radio_cache
    try:
        mtime = os.path.getmtime(path)
        if mtime == _radio_mtime:
            return _radio_cache
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        _radio_cache = data if isinstance(data, dict) else {}
        _radio_mtime = mtime
    except Exception as e:
        log(f"music: _load_radio_stations: {e}", tag="BRAIN", level="WARN")
    return _radio_cache


def _pick_random_radio() -> str | None:
    data = _load_radio_stations()
    if not data:
        return None
    return random.choice(list(data.keys()))


def _station_name(station_key: str) -> str:
    data = _load_radio_stations()
    entry = data.get(station_key)
    if isinstance(entry, dict):
        name = entry.get("name")
        if name:
            return name
    return station_key


def _wait_tts_started(base_seq: int, timeout: float = _TTS_START_WAIT_SEC) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            from workers.tts.state_file import get_state
            if get_state()["phrase_seq"] > base_seq:
                return True
        except Exception:
            pass
        time.sleep(_TTS_POLL_SEC)
    return False


def _send_music_after_tts(protocol_fn, *args, seq: int, base_seq: int | None = None, **kwargs):
    def _run():
        if base_seq is not None:
            ok = _wait_tts_started(base_seq)
            if seq != _current_dispatch_seq():
                log("music: устаревший запрос после ожидания TTS — пропуск", tag="BRAIN")
                return
            if not ok:
                log(f"music: TTS не начал за {_TTS_START_WAIT_SEC:.1f}с — "
                    f"отправляю music без ожидания", tag="BRAIN", level="WARN")
        elif seq != _current_dispatch_seq():
            return

        try:
            ipc.put(protocol.MUSIC_INBOX, protocol_fn(*args, **kwargs))
        except Exception as e:
            log(f"music: ipc.put после TTS: {e}", tag="BRAIN", level="WARN")

    threading.Thread(target=_run, daemon=True, name=f"music-after-tts-{seq}").start()


class MusicSkill(Skill):
    name = "music"
    priority = 40
    accepts_command = True

    def can_handle(self, text: str) -> bool:
        return False

    def handle(self, text_or_command) -> str | None:
        from brain.nlu.types import Command
        if not isinstance(text_or_command, Command):
            log(
                f"music: handle получил {type(text_or_command).__name__}, "
                f"а не Command — fast-path убран, ожидался Command",
                tag="BRAIN", level="WARN",
            )
            return None

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

        orig_text = getattr(command, "original_text", "")
        log(f"music: {action} ← Command «{orig_text}»", tag="BRAIN")

        if action == "play_wave" and result.get("language") is None:
            if self._try_ask():
                return None

        self._dispatch(action, result)
        return None

    def _command_to_result(self, command, action: str) -> dict | None:
        result: dict = {"action": action}
        ent = getattr(command, "entity", None)

        if action == "play_wave":
            if ent and isinstance(ent.value, dict):
                result["language"] = ent.value.get("language")
            else:
                result["language"] = None
            return result
        if action in ("play_liked", "play_local_liked", "dislike"):
            return result

        if action == "play_radio":
            station = ent.value if ent and ent.value else None
            if not station:
                station = _pick_random_radio()
                if station:
                    log(f"music: play_radio без станции → случайная «{station}»",
                        tag="BRAIN")
            if not station:
                log("music: play_radio — нет станций в radio_stations.json",
                    tag="BRAIN", level="WARN")
                return None
            result["station"] = station
            return result

        if action in ("like_current", "dislike_current"):
            from shared import state as _st
            cur = _st.get_current() or {}
            artist = cur.get("artist", "")
            title = cur.get("title", "")
            if not (artist or title):
                result["empty"] = True
                return result
            result["artist"] = artist
            result["title"] = title
            return result

        if action == "play_search":
            if ent is None or not ent.value:
                return None
            result["query"] = ent.value
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
            if isinstance(ent.value, dict):
                genre_val = ent.value.get("value", "")
                lang_val = ent.value.get("language")
            else:
                genre_val = ent.value
                lang_val = None

            if not genre_val:
                return None
            result["genre"] = genre_val
            result["language"] = lang_val
            try:
                from workers.music._shared import GENRE_ALIASES
                if lang_val == "russian" and f"русский {genre_val}" in GENRE_ALIASES:
                    result["station"] = GENRE_ALIASES[f"русский {genre_val}"]
                else:
                    result["station"] = GENRE_ALIASES.get(genre_val, f"genre:{genre_val}")
            except Exception:
                result["station"] = f"genre:{genre_val}"
            return result

        return None

    def _try_ask(self) -> bool:
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
                    if self._ask(phrase_key, kind="weather", data={"condition": precip}):
                        relationship.mark_asked_today(asked_key)
                        return True

        if not relationship.was_asked_today("music_recent"):
            recent = relationship.get_recent_prompt()
            if recent:
                if self._ask(
                    "MUSIC_ASK_RECENT",
                    kind="recent",
                    data={"artist": recent["name"]},
                    phrase_kwargs={"artist": recent["name"]},
                ):
                    relationship.mark_asked_today("music_recent")
                    return True

        return False

    def _ask(self, phrase_key: str, kind: str,
             data: dict, phrase_kwargs: dict | None = None) -> bool:
        kwargs = phrase_kwargs or {}
        base_seq = _respond_and_say(phrase_key, **kwargs)
        if base_seq is None:
            log(f"music: _ask({phrase_key}) не озвучен — пропуск pending",
                tag="BRAIN", level="WARN")
            return False

        def _on_timeout(_data):
            disarm_first_track_announce()
            seq = _next_dispatch_seq()
            timeout_seq = _respond_and_say("MUSIC_TIMEOUT")
            _send_music_after_tts(protocol.play_wave, language=None, seq=seq, base_seq=timeout_seq)

        pending.set(
            PENDING_KEY,
            timeout=10.0,
            on_timeout=_on_timeout,
            data={"kind": kind, **data},
            wait_tts=True,
            base_phrase_seq=base_seq,
        )
        log(f"music: спросил ({kind}), pending={PENDING_KEY} (wait_tts)", tag="BRAIN")
        return True

    def _dispatch(self, action: str, result: dict):
        seq = _next_dispatch_seq()

        if action == "play_artist":
            arm_first_track_announce()
            ipc.put(protocol.MUSIC_INBOX, protocol.play_artist(result["artist"]))

        elif action == "play_track":
            arm_first_track_announce()
            ipc.put(protocol.MUSIC_INBOX, protocol.play_track(result["track"]))

        elif action == "play_search":
            arm_first_track_announce()
            ipc.put(protocol.MUSIC_INBOX, protocol.play_search(result["query"]))

        elif action == "play_genre":
            disarm_first_track_announce()
            genre = result["genre"]
            lang = result.get("language")
            label = f"русский {genre}" if lang == "russian" else genre
            station = result.get("station", "")
            bseq = _respond_and_say("PLAY_GENRE", genre=label)
            _send_music_after_tts(protocol.play_genre, label, station=station, seq=seq, base_seq=bseq)

        elif action == "play_mood":
            disarm_first_track_announce()
            mood_tag = result["mood"]
            log(f"music: → protocol.play_mood({mood_tag!r})", tag="BRAIN")
            bseq = _respond_and_say("PLAY_MOOD", mood=mood_tag)
            _send_music_after_tts(protocol.play_mood, mood_tag, seq=seq, base_seq=bseq)

        elif action == "play_wave":
            disarm_first_track_announce()
            language = result.get("language")
            if language == "russian":
                bseq = _respond_and_say("PLAY_PLAYLIST", name="русскую волну")
            elif language == "not-russian":
                bseq = _respond_and_say("PLAY_PLAYLIST", name="зарубежную волну")
            else:
                bseq = _respond_and_say("PLAY_PLAYLIST", name="мою волну")
            _send_music_after_tts(protocol.play_wave, language=language, seq=seq, base_seq=bseq)

        elif action == "play_liked":
            disarm_first_track_announce()
            bseq = _respond_and_say("PLAY_PLAYLIST", name="твою музыку")
            _send_music_after_tts(protocol.play_liked, seq=seq, base_seq=bseq)
            try:
                from personality_core import self_state as _ss
                _ss.get().on_event("music_liked")
            except Exception:
                pass

        elif action == "play_local_liked":
            disarm_first_track_announce()
            from brain import liked_tracks as _lt
            if len(_lt.get_liked()) == 0:
                _respond_and_say("PLAY_LOCAL_LIKED_EMPTY")
                return
            bseq = _respond_and_say("PLAY_LOCAL_LIKED")
            _send_music_after_tts(protocol.play_local_liked, seq=seq, base_seq=bseq)

        elif action == "play_radio":
            disarm_first_track_announce()
            station = result["station"]
            name = _station_name(station)
            bseq = _respond_and_say("PLAY_RADIO", name=name)
            _send_music_after_tts(protocol.play_radio, station, seq=seq, base_seq=bseq)

        elif action == "like_current":
            if result.get("empty"):
                _respond_and_say("NO_TRACK_PLAYING")
                return
            from brain import liked_tracks as _lt
            artist = result.get("artist", "")
            title = result.get("title", "")
            added = _lt.add_like(artist, title, source="voice")
            try:
                from personality_core import self_state as _ss
                _ss.get().on_event("music_liked")
            except Exception:
                pass
            if added:
                _respond_and_say("LIKE_ADDED", artist=artist, title=title)
            else:
                _respond_and_say("LIKE_ALREADY", artist=artist, title=title)

        elif action == "dislike_current":
            if result.get("empty"):
                _respond_and_say("NO_TRACK_PLAYING")
                return
            from brain import liked_tracks as _lt
            artist = result.get("artist", "")
            title = result.get("title", "")
            removed = _lt.remove_like(artist, title)
            try:
                from personality_core import self_state as _ss
                _ss.get().on_event("music_disliked")
            except Exception:
                pass
            if removed:
                _respond_and_say("DISLIKE_ADDED", artist=artist, title=title)
            else:
                _respond_and_say("DISLIKE_ALREADY", artist=artist, title=title)
            ipc.put(protocol.MUSIC_INBOX, protocol.music_next())

        elif action == "dislike":
            ipc.put(protocol.MUSIC_INBOX, protocol.music_dislike())
            _respond_and_say("MUSIC_DISLIKE")
            try:
                from personality_core import self_state as _ss
                _ss.get().on_event("music_disliked")
            except Exception:
                pass


def on_music_started(artist: str, title: str) -> None:
    pass


register(MusicSkill())