"""
brain/main.py — ядро Макса.

Поток:
  ASR → brain_inbox → router → навык → helpers → tts_inbox → TTS

Что делает:
  - Загружает PersonalityEngine.
  - Подключает навыки (smalltalk, controls, music, weather, system, jellyfin, cleanup).
  - Запускает appraisal-воркер (reaction).
  - Запускает reminders-воркер (фоновые напоминания).
  - Тикает self_state и mind_stream раз в 30 сек.
  - Загружает self_state из data/self_state.json при старте.
  - Слушает brain_inbox.
  - На команду — router.process_command().
  - Навыки сами шлют в tts_inbox через helpers.
  - Раз в 20 минут дампит метрики в лог.
  - I: при music_started записывает привычку (time_bucket → artist).
  - F1: при первом wake за день — ритуал (утро/вечер/ночь).
  - F1: напоминания — фоновый воркер (не зависит от wake/команды).

Яндекс.Музыка — НЕ здесь. Она в workers/music/main.py.

FIX: source прокидывается в router.process_command(source=...),
     чтобы router знал про voice_dialog и не звал reminders первым.
"""

import sys
import time
from datetime import datetime

from shared import ipc
from shared import protocol
from shared import metrics
from shared.log import log
from shared import config as cfg


TICK_INTERVAL_SEC = 30
TICK_LOG_EVERY = 20  # логировать раз в 20 тиков (10 минут)

_tick_counter = 0


def _time_bucket() -> str:
    """morning (5-12) / day (12-18) / evening (18-23) / night (23-5)."""
    h = datetime.now().hour
    if 5 <= h < 12:
        return "morning"
    if 12 <= h < 18:
        return "day"
    if 18 <= h < 23:
        return "evening"
    return "night"


def _ritual_for_bucket(bucket: str) -> str | None:
    if bucket == "morning":
        return "RITUAL_MORNING"
    if bucket == "evening":
        return "RITUAL_EVENING"
    if bucket == "night":
        return "RITUAL_NIGHT"
    return None


def _maybe_ritual():
    """F1: первый wake за день → ритуал (утро/вечер/ночь). Один раз."""
    try:
        from brain import relationship
        bucket = _time_bucket()
        key = f"ritual_{bucket}"

        if relationship.was_asked_today(key):
            return

        phrase_set = _ritual_for_bucket(bucket)
        if not phrase_set:
            return

        from brain.skills._helpers import respond_and_say
        respond_and_say(phrase_set)
        relationship.mark_asked_today(key)
        log(f"brain: ritual {bucket} → {phrase_set}", tag="BRAIN")
    except Exception as e:
        log(f"brain: _maybe_ritual: {e}", tag="BRAIN", level="WARN")


def _tick():
    """Тик self_state и mind_stream. Раз в 30 сек.

    В лог пишем раз в TICK_LOG_EVERY тиков (10 минут).
    """
    global _tick_counter
    try:
        from personality_core import self_state as _self_state
        from personality_core import mind_stream as _mind
        st = _self_state.get()
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


def _init_skills():
    import brain.skills.smalltalk      # noqa: F401
    import brain.skills.controls       # noqa: F401
    import brain.skills.music.skill    # noqa: F401
    import brain.skills.weather.skill  # noqa: F401
    import brain.skills.system.skill   # noqa: F401
    import brain.skills.system.cleanup # noqa: F401
    log("brain: навыки подключены", tag="BRAIN")


def _init_appraisal():
    from brain import reaction
    reaction.start_worker()
    log("brain: appraisal-воркер запущен", tag="BRAIN")


def _init_reminders():
    """F1: фоновый воркер напоминаний."""
    from brain import reminders
    reminders.start_worker()
    log("brain: reminders-воркер запущен", tag="BRAIN")


def _init_self_state():
    """Загрузить self_state из data/self_state.json."""
    try:
        from personality_core import self_state as _self_state
        st = _self_state.get()
        log(f"brain: self_state загружен: {st.summary()}", tag="BRAIN")
    except Exception as e:
        log(f"brain: self_state load: {e}", tag="BRAIN", level="WARN")


def _handle_command(msg: protocol.Message):
    from brain.router import process_command
    from brain.errors import handle_error

    text = msg.text
    source = msg.data.get("source", "unknown")
    metrics.inc("commands")
    log(f"brain: команда «{text}» (source={source})", tag="BRAIN")

    try:
        result = process_command(text, source=source)
        if result == "exit":
            log("brain: команда exit — остановка", tag="BRAIN")
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
        if msg.name == "wake":
            _maybe_ritual()
            from brain.skills._helpers import respond_and_say
            respond_and_say("WAKE")

        elif msg.name == "wake_wait":
            from brain.skills._helpers import respond_and_say
            respond_and_say("WAKE_WAIT")

        elif msg.name == "wake_repeat":
            from brain.skills._helpers import respond_and_say
            respond_and_say("WAKE_REPEAT")

        elif msg.name == "now_playing":
            artist = msg.data.get("artist", "")
            title = msg.data.get("title", "")
            if artist or title:
                state.set_current(artist, title)
            from brain.skills._helpers import respond_and_say
            if title and artist:
                respond_and_say("NOW_PLAYING", title=title, artist=artist)
            elif title:
                respond_and_say("NOW_PLAYING", title=title, artist="")
            else:
                respond_and_say("NO_TRACK_PLAYING")

        elif msg.name == "music_stopped":
            state.clear_current()

        elif msg.name == "music_started":
            artist = msg.data.get("artist", "")
            title = msg.data.get("title", "")
            if artist or title:
                state.set_current(artist, title)

            if artist:
                try:
                    from brain import relationship
                    bucket = _time_bucket()
                    relationship.record_play_habit(bucket, artist)
                    log(f"brain: habit {bucket} → {artist}", tag="BRAIN")
                except Exception as e:
                    log(f"brain: record_play_habit: {e}", tag="BRAIN", level="WARN")

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

    _init_skills()
    _init_appraisal()
    _init_reminders()
    _init_self_state()

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