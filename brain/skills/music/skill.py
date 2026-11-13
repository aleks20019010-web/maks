# brain/skills/music/skill.py
# MusicSkill — тонкая обёртка. Понимает команду, шлёт в music_inbox.
#
# ── FIX (v8.5, 2026-11-13): _wait_tts_started_then_music ─────────────
#   Дёрганье музыки при старте: TTS и music стартовали одновременно
#   (два ipc.put в разные очереди). Music успевал заиграть 100% до того,
#   как TTS поставит speaking=True → ASR duck_on → музыка 30%.
#   Пользователь слышал «громко → тихо».
#
#   Фикс: music отправляется ТОЛЬКО после того, как TTS реально начал
#   (speaking=True в workers/tts/state_file). В отдельном потоке —
#   brain не блокируется, другие команды обрабатываются параллельно.
#   Music стартует уже под duck (30%), без дёрганья.
#
#   Для play_search не нужно: там pre-duck в workers/music/main.py
#   (_handle_play «search» → music.duck(30) только если TTS говорит).
#
# A2-фикс (день 30): _try_ask — только утром, один раз в день.
# A2: TTS ДО play_index, с каноничным именем из алиаса. Один TTS.
# MOOD: action="play_mood" → protocol.play_mood(mood_tag).
# RADIO (2026-11-07): «включи радио» без станции → случайная.
# RADIO TTS (2026-11-07): PLAY_RADIO с человеческим именем станции.
# FIX (2026-11-08): ipc.put ДО TTS — УБРАН в v8.5 (см. выше).
# P1: play_liked → on_event("music_liked"), dislike → on_event("music_disliked").
# LOCAL_LIKED / LIKE_CURRENT (v6.5).
# PLAY_SEARCH (v6.5).

import json
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

# v8.5: сколько ждать speaking=True после отправки TTS.
# Synth 1–2с (Яндекс + кэш), с запасом 5с.
_TTS_START_WAIT_SEC = 10.0
# v8.5: шаг polling state_file.
_TTS_POLL_SEC = 0.05


def _resolve_alias_name(query: str) -> str | None:
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


def _pick_random_radio() -> str | None:
    import os
    path = os.path.join(cfg.BASE_DIR, "config", "radio_stations.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not data:
            return None
        return random.choice(list(data.keys()))
    except Exception as e:
        log(f"music: _pick_random_radio: {e}", tag="BRAIN", level="WARN")
        return None


def _station_name(station_key: str) -> str:
    import os
    path = os.path.join(cfg.BASE_DIR, "config", "radio_stations.json")
    if not os.path.exists(path):
        return station_key
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        entry = data.get(station_key) if isinstance(data, dict) else None
        if isinstance(entry, dict):
            name = entry.get("name")
            if name:
                return name
    except Exception as e:
        log(f"music: _station_name: {e}", tag="BRAIN", level="WARN")
    return station_key


def _tts_is_speaking_now() -> bool:
    try:
        from workers.tts.state_file import get_state
        return bool(get_state().get("speaking", False))
    except Exception:
        return False


def _wait_tts_started(timeout: float = _TTS_START_WAIT_SEC) -> bool:
    """Ждать speaking=True в state_file TTS. True — дождались.

    Используется перед отправкой music: TTS synth 1–2с, потом
    speaking=True → ASR duck_on → music стартует 30%.
    """
    t0 = time.time()
    while time.time() - t0 < timeout:
        if _tts_is_speaking_now():
            return True
        time.sleep(_TTS_POLL_SEC)
    return False


def _send_music_after_tts(protocol_fn, *args, **kwargs):
    """v8.5: отправить music-команду ПОСЛЕ speaking=True от TTS.

    В отдельном потоке — brain не блокируется. Если TTS так и не
    начал говорить за timeout — отправляем всё равно (лучше поздно,
    чем никогда).
    """
    def _run():
        ok = _wait_tts_started()
        if not ok:
            log(f"music: TTS не начал за {_TTS_START_WAIT_SEC:.1f}с — "
                f"отправляю music без ожидания", tag="BRAIN", level="WARN")
        try:
            ipc.put(protocol.MUSIC_INBOX, protocol_fn(*args, **kwargs))
        except Exception as e:
            log(f"music: ipc.put после TTS: {e}", tag="BRAIN", level="WARN")

    threading.Thread(target=_run, daemon=True, name="music-after-tts").start()


class MusicSkill(Skill):
    name = "music"
    priority = 40

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

        log(f"music: {action} ← Command «{command.original_text}»", tag="BRAIN")

        if action == "play_wave" and result.get("language") is None:
            if self._try_ask():
                return None

        self._dispatch(action, result)
        return None

    def _command_to_result(self, command, action: str) -> dict | None:
        result: dict = {"action": action}
        ent = getattr(command, "entity", None)

        if action == "play_wave":
            result["language"] = None
            return result
        if action == "play_liked":
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

        if action == "play_local_liked":
            return result

        if action in ("like_current", "dislike_current"):
            from shared import state as _st
            cur = _st.get_current() or {}
            artist = cur.get("artist", "")
            title = cur.get("title", "")
            if not (artist or title):
                return None
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
            result["genre"] = ent.value
            try:
                from workers.music._shared import GENRE_ALIASES
                result["station"] = GENRE_ALIASES.get(ent.value, f"genre:{ent.value}")
            except Exception:
                result["station"] = f"genre:{ent.value}"
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
            _send_music_after_tts(protocol.play_wave, language=None)

        pending.set(
            PENDING_KEY,
            timeout=10.0,
            on_timeout=_on_timeout,
            data={"kind": kind, **data},
            wait_tts=True,
        )
        log(f"music: спросил ({kind}), pending={PENDING_KEY} (wait_tts)", tag="BRAIN")

    def _dispatch(self, action: str, result: dict):
        # v8.5: music отправляется ПОСЛЕ speaking=True от TTS.
        # Раньше: ipc.put(MUSIC_INBOX) ДО _respond_and_say → music успевал
        # заиграть 100% до duck_on → дёрганье (100% → 30%).
        # Теперь: _respond_and_say → поток ждёт speaking=True → music
        # стартует сразу под duck (30%), TTS закончил → 100%.
        # Brain не блокируется (поток daemon).

        if action == "play_artist":
            artist = result["artist"]
            canonical = _resolve_alias_name(artist)
            name_for_tts = canonical or artist
            _respond_and_say("PLAY_ARTIST_STARTED", artist=name_for_tts)
            _send_music_after_tts(protocol.play_artist, artist)

        elif action == "play_track":
            track = result["track"]
            canonical = _resolve_alias_name(track)
            name_for_tts = canonical or track
            _respond_and_say("PLAY_TRACK_STARTED", title=name_for_tts)
            _send_music_after_tts(protocol.play_track, track)

        elif action == "play_genre":
            genre = result["genre"]
            station = result.get("station", "")
            _respond_and_say("PLAY_GENRE", genre=genre)
            _send_music_after_tts(protocol.play_genre, genre, station=station)

        elif action == "play_mood":
            mood_tag = result["mood"]
            log(f"music: → protocol.play_mood({mood_tag!r})", tag="BRAIN")
            try:
                _respond_and_say("PLAY_MOOD", mood=mood_tag)
            except Exception as e:
                log(f"music: PLAY_MOOD TTS ошибка: {e}", tag="BRAIN", level="WARN")
            _send_music_after_tts(protocol.play_mood, mood_tag)

        elif action == "play_wave":
            language = result.get("language")
            if language == "russian":
                _respond_and_say("PLAY_PLAYLIST", name="русскую волну")
            else:
                _respond_and_say("PLAY_PLAYLIST", name="мою волну")
            _send_music_after_tts(protocol.play_wave, language=language)

        elif action == "play_liked":
            # TTS всегда есть (PLAY_PLAYLIST) — music после TTS.
            _respond_and_say("PLAY_PLAYLIST", name="твою музыку")
            _send_music_after_tts(protocol.play_liked)
            try:
                from personality_core import self_state as _ss
                _ss.get().on_event("music_liked")
            except Exception:
                pass

        elif action == "play_local_liked":
            from brain import liked_tracks as _lt
            n = len(_lt.get_liked())
            if n == 0:
                _respond_and_say("PLAY_LOCAL_LIKED_EMPTY")
                return  # music не нужна
            _respond_and_say("PLAY_LOCAL_LIKED")
            _send_music_after_tts(protocol.play_local_liked)

        elif action == "play_search":
            # v8.4: pre-duck в music (_handle_play «search»).
            # Здесь music отправляется сразу, без ожидания TTS —
            # pre-duck в music сработает, если TTS говорит.
            query = result["query"]
            ipc.put(protocol.MUSIC_INBOX, protocol.play_search(query))

        elif action == "like_current":
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

        elif action == "play_radio":
            station = result["station"]
            name = _station_name(station)
            _respond_and_say("PLAY_RADIO", name=name)
            _send_music_after_tts(protocol.play_radio, station)

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