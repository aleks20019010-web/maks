"""
brain/main.py — ядро Макса.

Поток:
  ASR → brain_inbox → router → навык → helpers → tts_inbox → TTS

Что делает:
  - Загружает PersonalityEngine.
  - Подключает навыки (smalltalk, controls, music, weather, system, cleanup).
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

NEW (v6): событие discovery от music worker.
  Music ловит трек-открытие и шлёт event_discovery в brain.
  Brain решает «когда» (пришло событие) → зовёт personality_core
  через respond_and_say("DISCOVERY_NEW", is_event=True, ...) → composer
  собирает фразу → TTS. Music сам не говорит, music_discovery.json
  напрямую не читает. Единая личность: discovery идёт через composer.

FIX (v6.4): события (discovery, wake, wake_wait, wake_repeat, now_playing,
  no_track_playing) → respond_and_say(..., is_event=True).
  Раньше respond_and_say звал engine.respond (event.type="command") —
  SELF-реакции (behavior_energy_low) перехватывали событие и вместо
  DISCOVERY_NEW звучало «Я устал». Теперь события идут через
  engine.respond_event (event.type="event") — SELF не трогается,
  composer собирает фразу напрямую.

FIX (день 28, аудит 1.1): при result == "exit" пишем data/brain_exit.flag
  с timestamp. Supervisor видит фреш-флаг (<60 сек) → не перезапускает brain.
  Раньше sys.exit(0) → supervisor видел «штатно» → auto_restart=True →
  перезапускал brain. Команда «выход» не останавливала Макса.

FIX (день 28): _time_bucket и _ritual_for_bucket переехали в shared/time_bucket.py.

WARMUP (новое): NLU warmup в фоне при старте.
  Раньше первая команда ждала 10-15 сек (intent: router загружен из
  intent_router.json + FastEmbedEncoder/ONNX). Теперь _start_nlu_warmup()
  запускает brain.nlu.intent.warmup() в daemon-thread при старте.
  Router греется параллельно, brain слушает brain_inbox сразу.

── FIX (этап 2.3): memory.start() явно ────────────────────────────
  MemoryEngine.__init__(start_writer=False) по умолчанию (см. memory/engine.py).
  _init_memory() вызывает memory.start() — иначе writer не запущен,
  события копятся в очереди, ничего не пишется.
  Вызывается после _init_engine(), до _init_skills() — скилы импортируют
  memory, но writer уже запущен.

── P1 (valence/arousal) ───────────────────────────────────────────
  on_event зовётся:
    - wake + утро/ночь → morning/night (arousal/valence сдвиг)
    - команда с похвалой/грубостью → user_praise/user_insult
    - тишина 30+ мин (в _tick) → silence (valence=0, arousal=0.4)
  music_liked/music_skipped/music_disliked — в controls.py и music/skill.py.

── YANDEX IMPORT (v6.5) ───────────────────────────────────────────
  _do_yandex_import() / _start_yandex_import_async().
  При старте (после _init_self_state) в фоне тянет лайки из Яндекса
  и мержит в brain.liked_tracks через merge_from_yandex.
  Не блокирует старт brain — daemon-thread.

── FIX (2026-11-11): двойной TTS на music_started ─────────────────
  Было: в ветке music_started вызывались и PLAY_SEARCH_FOUND (для
  mode=single/local_search), и NOW_PLAYING (всегда). TTS получал
  две команды — одна перебивала другую, иногда обе молчали.
  Стало: если mode=single/local_search → PLAY_SEARCH_FOUND.
         иначе → NOW_PLAYING / NO_TRACK_PLAYING.
  Плюс: в ветке now_playing был баг — использовалась переменная mode,
  которой в этом событии нет (NameError). Убран мёртвый блок.
"""

import sys
import time
import threading
from datetime import datetime

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
    """Аудит 1.1: пометить, что brain вышел штатно. Supervisor не перезапустит."""
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

        # P1: тишина 30+ мин → выдохнул (один раз за период тишины)
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
    """FIX (этап 2.3): явный memory.start() — writer не стартует в __init__."""
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
    """Импорт лайков из Яндекса. В фоне, не блокирует старт."""
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
    """Запустить импорт лайков в daemon-thread (не блокирует старт)."""
    threading.Thread(target=_do_yandex_import, daemon=True,
                     name="yandex-import").start()
    log("brain: импорт лайков запущен (в фоне)", tag="BRAIN")


def _warmup_nlu():
    """NLU warmup: загрузить ONNX + tokenizer + labels в фоне."""
    try:
        from brain.nlu import intent as nlu_intent
        nlu_intent.warmup()
    except Exception as e:
        log(f"brain: NLU warmup error: {e}", tag="BRAIN", level="WARN")


def _start_nlu_warmup():
    """Запустить NLU warmup в daemon-thread (не блокирует старт brain)."""
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

    try:
        result = process_command(text, source=source)

        # P1: реакция на похвалу/грубость по тексту
        # (process_command возвращает str, intent не отдаёт — эвристика по словам)
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
        # P1: события → self_state (valence/arousal реально меняются)
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
            # Это ответ на «что играет» — не путать с music_started.
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
            # NEW (v6): music сообщил «будет трек-открытие».
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
                from brain.skills.music.skill import on_music_started
                on_music_started(artist, title)
            except Exception as e:
                log(f"brain: on_music_started: {e}",
                    tag="BRAIN", level="WARN")

            # TTS с каноничным названием: только для play_search.
            # mode="single" — Яндекс-трек, mode="local_search" — локальный.
            # Для остальных (artist_only/wave/genre/...) TTS уже сказал skill.py.
            if (artist or title) and mode in ("single", "local_search"):
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

    _init_memory()   # FIX (этап 2.3): memory.start() явно
    _init_skills()
    _init_appraisal()
    _init_reminders()
    _init_self_state()

    _start_yandex_import_async()   # ← новое: лайки из Яндекса в фоне
    _start_nlu_warmup()             # ONNX warmup в фоне

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