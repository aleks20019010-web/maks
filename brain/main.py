"""
brain/main.py — ядро Макса.

Поток:
  ASR → brain_inbox → router → навык → helpers → tts_inbox → TTS

── DUCKING ────────────────────────────────────────────────────────
  Brain больше НЕ управляет ducking. `wake` снимает ASR (duck_worker),
  когда speaking=True или по таймауту 5с. Brain только обрабатывает
  команду. Это убирает окно wake→tts_synth, где музыка на 100%.

── ОЗВУЧКА МУЗЫКИ ────────────────────────────────────────────────
  - Плейлисты/потоки (wave/genre/mood/radio/liked): озвучивает skill.py
    до запуска («Включаю мою волну» и т.д.), отдельные треки не объявляются.
  - Точечные запросы (play_artist/play_track/play_search): skill.py молчит
    и взводит consume_first_track_announce(); brain озвучивает только первый
    реально запустившийся трек (PLAY_SEARCH_FOUND), остальные треки очереди —
    без озвучки.

── FIX (v6.4): события → respond_and_say(is_event=True) ─────────
── FIX (день 28, аудит 1.1): exit-флаг ──────────────────────────
── WARMUP: NLU warmup в фоне при старте ─────────────────────────
── FIX (этап 2.3): memory.start() явно ──────────────────────────
── P1 (valence/arousal) ─────────────────────────────────────────
── YANDEX IMPORT (v6.5) ─────────────────────────────────────────
── UI_BRIDGE (2026-11-18): чат-лог команд пользователя ──────────
  В _handle_command() сразу после log(...) добавляется append_chat("user", ...)
  — все распознанные команды попадают в UI-чат с meta=source.
"""

import sys
import time
import threading

from shared import ipc
from shared import protocol
from shared import metrics
from shared.log import log
from shared import config as cfg
from shared.sources import CommandSource
from shared.time_bucket import time_bucket, ritual_for_bucket


TICK_INTERVAL_SEC = 30
TICK_LOG_EVERY = 20

EXIT_FLAG = cfg.DATA_DIR / "brain_exit.flag"
EXIT_FLAG_TTL_SEC = 60.0

_tick_counter = 0


def _write_exit_flag():
    try:
        EXIT_FLAG.parent.mkdir(parents=True, exist_ok=True)
        EXIT_FLAG.write_text(str(time.time()), encoding="utf-8")
        log(f"brain: exit-флаг записан ({EXIT_FLAG})", tag="BRAIN")
    except Exception as e:
        log(f"brain: exit-флаг: {e}", tag="BRAIN", level="WARN")


def _maybe_ritual():
    try:
        from brain import relationship
        bucket = time_bucket()
        key = f"ritual_{bucket}"

        if relationship.was_asked_today(key):
            return

        phrase_set = ritual_for_bucket(bucket)
        if not phrase_set:
            return

        from brain.skills._helpers import respond_and_say
        respond_and_say(phrase_set, is_event=True)
        relationship.mark_asked_today(key)
        log(f"brain: ritual {bucket} → {phrase_set}", tag="BRAIN")
    except Exception as e:
        log(f"brain: _maybe_ritual: {e}", tag="BRAIN", level="WARN")


def _tick():
    global _tick_counter
    try:
        from personality_core import self_state as _self_state
        from personality_core import mind_stream as _mind
        st = _self_state.get()

        _now = time.time()
        _last = st.last_command_ts or 0
        if _last and (_now - _last) > 1800 and (_now - st.last_silence_ts) > 1800:
            st.on_event("silence")
            st.last_silence_ts = _now

        st.on_tick()
        _mind.get().on_tick(mood=st.mood)

        _tick_counter += 1
        if _tick_counter % TICK_LOG_EVERY == 0:
            log(f"brain: tick #{_tick_counter} energy={st.energy:.2f} mood={st.mood}",
                tag="BRAIN")
    except Exception as e:
        log(f"brain: tick: {e}", tag="BRAIN", level="WARN")


def _init_engine():
    from personality_core.engine import PersonalityEngine
    from brain.skills import _helpers

    log("brain: загружаю PersonalityEngine...", tag="BRAIN")
    engine = PersonalityEngine(cfg.BASE_DIR)
    _helpers.set_engine(engine)
    log("brain: PersonalityEngine готов", tag="BRAIN")
    return engine


def _init_memory():
    try:
        from memory import memory
        memory.start()
        log("brain: memory writer запущен", tag="BRAIN")
    except Exception as e:
        log(f"brain: memory start: {e}", tag="BRAIN", level="WARN")


def _init_skills():
    import brain.skills.smalltalk      # noqa: F401
    import brain.skills.controls       # noqa: F401
    import brain.skills.music.skill    # noqa: F401
    import brain.skills.weather.skill  # noqa: F401
    import brain.skills.system.skill   # noqa: F401
    import brain.skills.system.cleanup # noqa: F401
    import brain.skills.llm.skill      # noqa: F401
    import brain.skills.reminders.skill  # noqa: F401
    log("brain: навыки подключены", tag="BRAIN")


def _init_appraisal():
    from brain import reaction
    reaction.start_worker()
    log("brain: appraisal-воркер запущен", tag="BRAIN")


def _init_reminders():
    from brain import reminders
    reminders.start_worker()
    log("brain: reminders-воркер запущен", tag="BRAIN")


def _init_self_state():
    try:
        from personality_core import self_state as _self_state
        st = _self_state.get()
        log(f"brain: self_state загружен: {st.summary()}", tag="BRAIN")
    except Exception as e:
        log(f"brain: self_state load: {e}", tag="BRAIN", level="WARN")


def _do_yandex_import():
    try:
        from shared import yandex
        from brain import liked_tracks as _lt

        if not yandex.is_ready():
            log("brain: импорт лайков — Яндекс не подключён", tag="BRAIN", level="DEBUG")
            return

        client = yandex.get_client()
        if client is None:
            return

        liked = client.users_likes_tracks()
        if not liked or not liked.tracks:
            log("brain: импорт лайков — пусто", tag="BRAIN")
            return

        tracks = []
        for short in liked.tracks:
            try:
                t = short.fetch_track()
                if not t:
                    continue
                artist = ', '.join(a.name for a in t.artists) if t.artists else ""
                tracks.append({
                    "artist": artist,
                    "title": t.title or "",
                    "track_id": str(t.id) if t.id else "",
                })
            except Exception:
                continue

        added = _lt.merge_from_yandex(tracks)
        log(f"brain: импорт лайков: +{added}, всего {len(_lt.get_liked())}",
            tag="BRAIN")
    except Exception as e:
        log(f"brain: импорт лайков: {e}", tag="BRAIN", level="WARN")


def _start_yandex_import_async():
    threading.Thread(target=_do_yandex_import, daemon=True,
                     name="yandex-import").start()
    log("brain: импорт лайков запущен (в фоне)", tag="BRAIN")


def _warmup_nlu():
    try:
        from brain.nlu import intent as nlu_intent
        nlu_intent.warmup()
    except Exception as e:
        log(f"brain: NLU warmup error: {e}", tag="BRAIN", level="WARN")


def _start_nlu_warmup():
    threading.Thread(
        target=_warmup_nlu, daemon=True, name="nlu-warmup"
    ).start()
    log("brain: NLU warmup запущен (в фоне)", tag="BRAIN")


def _parse_source(raw) -> CommandSource:
    if isinstance(raw, CommandSource):
        return raw
    try:
        return CommandSource(str(raw))
    except ValueError:
        return CommandSource.UNKNOWN


def _handle_command(msg: protocol.Message):
    from brain.router import process_command
    from brain.errors import handle_error

    text = msg.text
    source = _parse_source(msg.data.get("source", CommandSource.UNKNOWN))
    metrics.inc("commands")
    log(f"brain: команда «{text}» (source={source.value})", tag="BRAIN")

    # ── UI_BRIDGE (2026-11-18): чат-лог команды пользователя ──
    from shared.ui_bridge import append_chat
    append_chat("user", text, meta=source.value)

    try:
        result = process_command(text, source=source)

        try:
            from personality_core import self_state as _ss
            _st = _ss.get()
            _low = text.lower()
            if any(w in _low for w in ("спасибо", "молодец", "умница", "класс", "супер")):
                _st.on_event("user_praise")
            elif any(w in _low for w in ("дурак", "тупой", "идиот", "заткнись", "надоел", "бесишь")):
                _st.on_event("user_insult")
        except Exception as _e:
            log(f"brain: self_state.on_event(cmd): {_e}", tag="BRAIN", level="WARN")

        if result == "exit":
            log("brain: команда exit — остановка", tag="BRAIN")
            _write_exit_flag()
            sys.exit(0)
    except SystemExit:
        raise
    except Exception as e:
        handle_error(e, context="brain._handle_command", speak=True)


def _handle_event(msg: protocol.Message):
    from brain.errors import handle_error
    from shared import state

    metrics.inc("events")
    log(f"brain: событие {msg.name} {msg.data}", tag="BRAIN")

    try:
        try:
            from personality_core import self_state as _ss
            _st = _ss.get()
            _h = time.localtime().tm_hour
            if msg.name == "wake":
                if 5 <= _h < 12:
                    _st.on_event("morning")
                elif _h >= 23 or _h < 5:
                    _st.on_event("night")
        except Exception as _e:
            log(f"brain: self_state.on_event(wake): {_e}", tag="BRAIN", level="WARN")

        if msg.name == "wake":
            _maybe_ritual()
            from brain.skills._helpers import respond_and_say
            respond_and_say("WAKE", is_event=True)

        elif msg.name == "wake_wait":
            from brain.skills._helpers import respond_and_say
            respond_and_say("WAKE_WAIT", is_event=True)

        elif msg.name == "wake_repeat":
            from brain.skills._helpers import respond_and_say
            respond_and_say("WAKE_REPEAT", is_event=True)

        elif msg.name == "now_playing":
            artist = msg.data.get("artist", "")
            title = msg.data.get("title", "")
            if artist or title:
                state.set_current(artist, title)
            from brain.skills._helpers import respond_and_say
            if title and artist:
                respond_and_say("NOW_PLAYING", is_event=True,
                                title=title, artist=artist)
            elif title:
                respond_and_say("NOW_PLAYING", is_event=True,
                                title=title, artist="")
            else:
                respond_and_say("NO_TRACK_PLAYING", is_event=True)

        elif msg.name == "discovery":
            artist = msg.data.get("artist", "")
            title = msg.data.get("title", "")
            if artist or title:
                state.set_current(artist, title)
            from brain.skills._helpers import respond_and_say
            respond_and_say("DISCOVERY_NEW", is_event=True, artist=artist, title=title)

        elif msg.name == "music_stopped":
            state.clear_current()

        elif msg.name == "music_started":
            artist = msg.data.get("artist", "")
            title = msg.data.get("title", "")
            mode = msg.data.get("mode", "") or ""
            if artist or title:
                state.set_current(artist, title)

            _HABIT_MODES = ("artist_only", "single", "genre")
            if artist and mode in _HABIT_MODES:
                try:
                    from brain import relationship
                    bucket = time_bucket()
                    relationship.record_play_habit(bucket, artist)
                    log(f"brain: habit {bucket} → {artist} (mode={mode})",
                        tag="BRAIN")
                except Exception as e:
                    log(f"brain: record_play_habit: {e}",
                        tag="BRAIN", level="WARN")
            elif artist:
                log(f"brain: habit skip (mode={mode or '?'}, "
                    f"не явная команда)", tag="BRAIN", level="DEBUG")

            try:
                from brain.skills.music.skill import (
                    on_music_started,
                    consume_first_track_announce,
                )
                on_music_started(artist, title)
                should_announce = consume_first_track_announce()
            except Exception as e:
                log(f"brain: on_music_started: {e}",
                    tag="BRAIN", level="WARN")
                should_announce = False

            # Озвучиваем по факту только первый трек после запроса
            # артиста/трека/поиска; последующие треки и потоки (волна/радио) — молча.
            if should_announce and (artist or title):
                try:
                    from brain.skills._helpers import respond_and_say
                    respond_and_say("PLAY_SEARCH_FOUND",
                                    artist=artist or "?",
                                    title=title or "?")
                except Exception as e:
                    log(f"brain: PLAY_SEARCH_FOUND TTS: {e}",
                        tag="BRAIN", level="WARN")

        elif msg.name == "track_finished":
            state.clear_current()

        else:
            log(f"brain: неизвестное событие {msg.name}", tag="BRAIN", level="WARN")

    except Exception as e:
        handle_error(e, context=f"brain._handle_event({msg.name})", speak=False)


def main():
    log("brain: старт", tag="BRAIN")
    ipc.init()

    try:
        _init_engine()
    except Exception as e:
        from brain.errors import handle_error
        handle_error(e, context="brain._init_engine", speak=False)
        sys.exit(1)

    _init_memory()
    _init_skills()
    _init_appraisal()
    _init_reminders()
    _init_self_state()

    _start_yandex_import_async()
    _start_nlu_warmup()

    log("brain: слушаю brain_inbox", tag="BRAIN")

    last_metrics_dump = time.time()
    last_tick = time.time()

    while True:
        msg = ipc.get(protocol.BRAIN_INBOX, timeout=1.0)
        now = time.time()

        if now - last_tick >= TICK_INTERVAL_SEC:
            _tick()
            last_tick = now

        if now - last_metrics_dump >= 1200.0:
            metrics.dump()
            last_metrics_dump = now

        if msg is None:
            continue

        try:
            if msg.type == "command":
                _handle_command(msg)
            elif msg.type == "event":
                _handle_event(msg)
            else:
                log(f"brain: неизвестный тип {msg.type}", tag="BRAIN", level="WARN")
        except SystemExit:
            raise
        except Exception as e:
            from brain.errors import handle_error
            handle_error(e, context="brain.main_loop", speak=False)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("brain: остановка по Ctrl+C", tag="BRAIN")
        sys.exit(0)