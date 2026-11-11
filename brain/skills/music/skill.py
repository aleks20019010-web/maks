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
# MOOD: action="play_mood" → protocol.play_mood(mood_tag).
#   mood_tag — tag метагега («Энергичное»). Воркер музыки делает
#   client.metatag(tag) → station_id (mood:energetic) → start_genre.
#
# ЭТАП 9 (часть 2, НОВОЕ): fast-path для музыки УБРАН.
#   can_handle → False. Музыка — ЦЕЛИКОМ через NLU (Command).
#   Причина: fast-path (classify) хватал «включи чего-нибудь» →
#   play_artist("чего-нибудь") → артист не найден → молчание. NLU даже
#   не участвовал. Теперь: всё через NLU → Command → handle(Command).
#   controls/weather — тоже на Command (см. router._execute_decision).
#   classify остаётся для тестов (test_music_classify) и потенциального
#   fast-path, но в проде НЕ используется.
#
# NOT_UNDERSTOOD: classify ловит грубые обращения («эй ты») → not_understood.
#   Но fast-path убран, значит, «эй ты» → NLU → UNKNOWN → LLM?
#   Нет: «эй ты» → intent.resolve → UNKNOWN (confidence < 0.5) → FALLBACK →
#   LLM скажет «Простите, можно ещё раз?». Это дольше (4.5 сек), но
#   правильно. Если хочешь быстро — вернём fast-path для not_understood
#   отдельно (потом).
#
# RADIO (2026-11-07): «включи радио» без станции → ent.value=None.
#   Раньше: if ent is None or not ent.value: return None (радио терялось).
#   Теперь: для play_radio без value — выбираем случайную станцию здесь,
#   чтобы _dispatch получил непустой station. _dispatch → protocol.play_radio
#   → workers/music/main.py::_handle_play → radio.start_radio(station).
#   Если станций нет — return None (нечего играть, router скажет «не понял»).
#
# RADIO TTS (2026-11-07): _dispatch("play_radio") теперь говорит
#   PLAY_RADIO (responses/radio.json, {name}) с человеческим именем
#   станции («Дорожное Радио», не ключ «дорожное»). _station_name()
#   читает radio_stations.json → entry["name"]. Раньше TTS не было —
#   радио включалось молча. TTS ДО ipc.put → радио стартует ПОСЛЕ TTS
#   (как play_wave: TTS первый, музыка после).
#
# FIX (2026-11-08): ipc.put ДО TTS (все ветки _dispatch).
#   Раньше: _respond_and_say (TTS, 5-6 сек) → ipc.put (музыка ПОСЛЕ TTS).
#   Музыка стартовала через 2 сек после TTS (VLC Opening) — 0 сек тишины
#   не было.
#   Теперь: ipc.put (музыка стартует сразу, VLC Opening 2 сек) →
#   _respond_and_say (TTS 5-6 сек поверх). TTS-hold (modes.py) приглушает
#   музыку до mark_ready, ASR mic_off → duck_on (30%). Музыка 30% пока
#   TTS говорит, 100% после. Пользователь слышит TTS сразу, музыка
#   стартует параллельно (0 сек тишины после TTS).
#
# P1: play_liked → on_event("music_liked"), dislike → on_event("music_disliked").
#
# ── LOCAL_LIKED / LIKE_CURRENT (v6.5) ──────────────────────────────
#   play_local_liked — локальный плейлист лайков (data/liked_tracks.json)
#     через protocol.play_local_liked() → workers/music/main.py.
#   like_current / dislike_current — голосовые «нравится»/«не нравится»
#     текущий трек: _lt.add_like / _lt.add_dislike + TTS + SELF-событие.
#     dislike_current дополнительно шлёт music_next (переключить трек).
#
# ── PLAY_SEARCH (v6.5) ──────────────────────────────────────────────
#   play_search — сырой запрос без явных hints (классификатор/entity
#   не угадали artist/track). Шлёт protocol.play_search(query) →
#   workers/music/main.py::_handle_play("search") — локально
#   (search_local: точный → phon → fuzzy) → Яндекс (search_track).

import json
import random

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


def _pick_random_radio() -> str | None:
    """Выбрать случайную станцию из config/radio_stations.json.

    RADIO (2026-11-07): «включи радио» без станции → любая.
    Читаем напрямую (без импорта из workers — brain не должен зависеть
    от воркеров, хотя _shared.GENRE_ALIASES уже импортируется — прецедент есть).
    """
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
    """Ключ станции → человеческое имя из radio_stations.json.

    RADIO TTS (2026-11-07): «дорожное» → «Дорожное Радио» для фразы
    PLAY_RADIO ({name}). Если ключ не найден — возвращаем ключ как есть
    (лучше сказать «дорожное», чем ничего).
    """
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


class MusicSkill(Skill):
    name = "music"
    priority = 40

    def can_handle(self, text: str) -> bool:
        """Fast-path для музыки УБРАН. Всё через NLU (Command).

        Причина: fast-path (classify) хватал «включи чего-нибудь» →
        play_artist("чего-нибудь") → артист не найден → молчание.
        NLU не участвовал. Теперь музыка — целиком NLU.
        """
        return False

    def handle(self, text_or_command) -> str | None:
        """Handle Command (NLU-путь). str — больше не поддерживается.

        Command → диспатч по command.action, classify НЕ зовём.
        """
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

        # play_wave: спросить про погоду/вчерашнее (как раньше)
        if action == "play_wave" and result.get("language") is None:
            if self._try_ask():
                return None

        self._dispatch(action, result)
        return None

    def _command_to_result(self, command, action: str) -> dict | None:
        """Command → dict для _dispatch. По action и entity."""
        result: dict = {"action": action}
        ent = getattr(command, "entity", None)

        if action == "play_wave":
            result["language"] = None
            return result
        if action == "play_liked":
            return result

        # RADIO (2026-11-07): «включи радио» без станции → ent.value=None.
        # Раньше: if ent is None or not ent.value: return None (радио терялось).
        # Теперь: для play_radio без value — выбираем случайную станцию
        # здесь, чтобы _dispatch получил непустой station.
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
        """Спросить про погоду (утром, один раз в день) или вчерашнее."""
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
        # FIX (2026-11-08): ipc.put ДО _respond_and_say (все ветки).
        # Музыка стартует сразу (VLC Opening 2 сек параллельно с TTS),
        # TTS поверх (ducking 30%). 0 сек тишины после TTS.
        if action == "play_artist":
            artist = result["artist"]
            canonical = _resolve_alias_name(artist)
            name_for_tts = canonical or artist
            ipc.put(protocol.MUSIC_INBOX, protocol.play_artist(artist))
            _respond_and_say("PLAY_ARTIST_STARTED", artist=name_for_tts)

        elif action == "play_track":
            track = result["track"]
            canonical = _resolve_alias_name(track)
            name_for_tts = canonical or track
            ipc.put(protocol.MUSIC_INBOX, protocol.play_track(track))
            _respond_and_say("PLAY_TRACK_STARTED", title=name_for_tts)

        elif action == "play_genre":
            genre = result["genre"]
            station = result.get("station", "")
            ipc.put(protocol.MUSIC_INBOX, protocol.play_genre(genre, station=station))
            _respond_and_say("PLAY_GENRE", genre=genre)

        elif action == "play_mood":
            mood_tag = result["mood"]
            log(f"music: → protocol.play_mood({mood_tag!r})", tag="BRAIN")
            ipc.put(protocol.MUSIC_INBOX, protocol.play_mood(mood_tag))
            try:
                _respond_and_say("PLAY_MOOD", mood=mood_tag)
            except Exception as e:
                log(f"music: PLAY_MOOD TTS ошибка: {e}", tag="BRAIN", level="WARN")

        elif action == "play_wave":
            language = result.get("language")
            ipc.put(protocol.MUSIC_INBOX, protocol.play_wave(language=language))
            if language == "russian":
                _respond_and_say("PLAY_PLAYLIST", name="русскую волну")
            else:
                _respond_and_say("PLAY_PLAYLIST", name="мою волну")

        elif action == "play_liked":
            ipc.put(protocol.MUSIC_INBOX, protocol.play_liked())
            _respond_and_say("PLAY_PLAYLIST", name="твою музыку")
            # P1: «включи мою музыку» — сигнал «люблю то, что там»
            try:
                from personality_core import self_state as _ss
                _ss.get().on_event("music_liked")
            except Exception:
                pass

        elif action == "play_local_liked":
            ipc.put(protocol.MUSIC_INBOX, protocol.play_local_liked())
            from brain import liked_tracks as _lt
            n = len(_lt.get_liked())
            if n == 0:
                _respond_and_say("PLAY_LOCAL_LIKED_EMPTY")
            else:
                _respond_and_say("PLAY_LOCAL_LIKED")

        elif action == "play_search":
            query = result["query"]
            ipc.put(protocol.MUSIC_INBOX, protocol.play_search(query))
            _respond_and_say("PLAY_SEARCH", query=query)

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
            added = _lt.add_dislike(artist, title, source="voice")
            try:
                from personality_core import self_state as _ss
                _ss.get().on_event("music_disliked")
            except Exception:
                pass
            if added:
                _respond_and_say("DISLIKE_ADDED", artist=artist, title=title)
                ipc.put(protocol.MUSIC_INBOX, protocol.music_next())
            else:
                _respond_and_say("DISLIKE_ALREADY", artist=artist, title=title)

        elif action == "play_radio":
            # RADIO (2026-11-07): station гарантированно непустой
            # (выбран в _command_to_result — value или случайная).
            # RADIO TTS (2026-11-07): говорим PLAY_RADIO (responses/radio.json,
            # {name}) с ЧЕЛОВЕЧЕСКИМ именем («Дорожное Радио», не «дорожное»).
            station = result["station"]
            name = _station_name(station)
            ipc.put(protocol.MUSIC_INBOX, protocol.play_radio(station))
            _respond_and_say("PLAY_RADIO", name=name)

        elif action == "dislike":
            ipc.put(protocol.MUSIC_INBOX, protocol.music_dislike())
            _respond_and_say("MUSIC_DISLIKE")
            # P1: «не нравится» — valence чуть вниз
            try:
                from personality_core import self_state as _ss
                _ss.get().on_event("music_disliked")
            except Exception:
                pass


def on_music_started(artist: str, title: str) -> None:
    """A2: TTS был ДО play_index (PLAY_ARTIST_STARTED). Здесь — пусто."""
    pass


register(MusicSkill())