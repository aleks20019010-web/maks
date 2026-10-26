# brain/skills/llm/skill.py
# LLM-скил — точка входа.
#
# АРХИТЕКТУРА (потоки):
#   brain (главный) → llm_queue → llm_worker → tts_queue → tts_worker → TTS
#
#   brain НЕ блокируется на _call_llm. Он кладёт задачу в llm_queue и сразу
#   возвращается к чтению brain_inbox. llm_worker обрабатывает по одной
#   (порядок гарантирован, история последовательна). tts_worker отправляет
#   ответы в TTS строго по порядку номеров.
#
# FIX 1: очередь TTS не зависает.
#   КАЖДЫЙ req_id попадает в _tts_queue, даже command/cancel/error —
#   со статусом "NO_TTS" (ничего не говорить).
#
# FIX 2 (ПРАВИЛЬНЫЙ): отмена через generation_id. cancel_all() НЕ очищает
#   очереди — только _generation += 1. Старые запросы сами увидят
#   _is_cancelled(gen) → положат NO_TTS → пропустятся. next_id идёт
#   последовательно, НИКОГДА не рвётся.
#
#   Раньше: cancel_all() очищал _llm_queue и _tts_queue, и next_id в
#   _tts_worker «зависал» на пропущенном номере. Следующий ответ ждал
#   next_id, которого нет → очередь стояла.
#
# FIX 3: cancel_all() закрывает сессию (_session.get().close()).
#   Иначе _history живёт после «хватит», и следующий вопрос получит
#   старый контекст. Session.close() вызывает _history.clear().
#
# ПРОВАЙДЕР:
#   provider: {"sort": "latency"} — AITUNNEL выбирает провайдера с минимальной
#   задержкой до первого токена.
#
# FIX: process_command(cmd, source=CommandSource.SYSTEM). CommandSource enum.
# FIX: _in_dialog() и session_open.flag — через cfg.DATA_DIR.
# FIX: llm_key.txt — через cfg.CONFIG_DIR.
# FIX: _whitelist.is_command_allowed(cmd) — LLM-команда проверяется перед
#      выполнением. Запрещённые (формат, shutdown) → NOT_UNDERSTOOD.

import queue
import threading
import time

from brain.skills.base import Skill, register
from brain.skills._helpers import respond_and_say as _respond_and_say
from shared.log import log
from shared import config as cfg
from shared.sources import CommandSource

from brain.skills.llm import _prompt, _parser, _history, _session, _whitelist


# ==================== ГЛОБАЛЬНЫЕ ОЧЕРЕДИ И ФЛАГИ ====================

_llm_queue: "queue.Queue[tuple[int, str, bool, int]]" = queue.Queue()
_tts_queue: "queue.Queue[tuple[int, object]]" = queue.Queue()

# FIX 2: generation вместо глобального _cancelled
_generation = 0
_generation_lock = threading.Lock()

_request_counter = 0
_counter_lock = threading.Lock()

_history_lock = threading.Lock()

_engine_ref = None

_workers_started = False
_workers_lock = threading.Lock()


def set_engine(engine):
    global _engine_ref
    _engine_ref = engine


def cancel_all():
    """Отменить всё: увеличить generation.

    FIX 2 (ПРАВИЛЬНЫЙ): НЕ очищаем очереди. Старые запросы сами увидят
    _is_cancelled(gen) → NO_TTS → пропустятся. next_id не рвётся.

    FIX 3: закрываем сессию — иначе _history живёт после «хватит»,
    и следующий вопрос получит старый контекст.
    """
    global _generation
    with _generation_lock:
        _generation += 1
        gen = _generation

    # Закрыть сессию — очистить историю диалога
    try:
        _session.get().close()
    except Exception as e:
        log(f"llm: cancel_all session.close: {e}", tag="BRAIN", level="WARN")

    log(f"llm: cancelled all (gen={gen})", tag="BRAIN")


def _current_gen() -> int:
    with _generation_lock:
        return _generation


def _is_cancelled(gen: int) -> bool:
    """Запрос отменён, если generation изменился с момента старта."""
    with _generation_lock:
        return _generation != gen


def _next_id() -> int:
    global _request_counter
    with _counter_lock:
        _request_counter += 1
        return _request_counter


# ==================== КЛИЕНТ ====================

_client = None
_client_error = None


def _get_client():
    global _client, _client_error
    if _client is not None:
        return _client
    if _client_error:
        return None

    try:
        from openai import OpenAI
    except ImportError as e:
        _client_error = f"openai not installed: {e}"
        log(f"llm: {_client_error}", tag="BRAIN", level="ERROR")
        return None

    key_file = cfg.CONFIG_DIR / "llm_key.txt"
    if not key_file.exists():
        _client_error = f"llm_key.txt not found: {key_file}"
        log(f"llm: {_client_error}", tag="BRAIN", level="ERROR")
        return None

    try:
        with open(key_file, "r", encoding="utf-8") as f:
            key = f.read().strip()
    except Exception as e:
        _client_error = f"read llm_key.txt: {e}"
        log(f"llm: {_client_error}", tag="BRAIN", level="ERROR")
        return None

    if not key:
        _client_error = "llm_key.txt is empty"
        log(f"llm: {_client_error}", tag="BRAIN", level="ERROR")
        return None

    try:
        _client = OpenAI(api_key=key, base_url="https://api.aitunnel.ru/v1/", timeout=15.0)
    except Exception as e:
        _client_error = f"OpenAI init: {e}"
        log(f"llm: {_client_error}", tag="BRAIN", level="ERROR")
        return None

    return _client


# ==================== ДИАЛОГ ====================

def _in_dialog() -> bool:
    try:
        flag = cfg.DATA_DIR / "session_open.flag"
        if not flag.exists():
            return False
        ts = float(flag.read_text().strip())
        return (time.time() - ts) < 20.0
    except Exception:
        return False


# ==================== ДЕТЕКТОР «ПОДРОБНЕЕ» ====================

_DETAIL_TRIGGERS = (
    "подробнее", "подробней", "поподробнее",
    "расскажи", "рассказывай",
    "объясни", "объясняй",
    "поясни", "разъясни",
    "в деталях", "детально",
    "полный ответ", "развёрнуто", "развернуто",
    "а почему", "почему именно", "как именно",
    "и ещё", "и еще", "продолжай",
)


def _wants_detail(text: str) -> bool:
    tl = text.lower().strip()
    if not tl:
        return False
    return any(trigger in tl for trigger in _DETAIL_TRIGGERS)


# ==================== КОНТЕКСТ ====================

def _build_context() -> dict:
    ctx = {}
    try:
        from shared import state
        cur = state.get_current() or {}
        if cur.get("artist") or cur.get("title"):
            ctx["current_track"] = f"{cur.get('title', '?')} — {cur.get('artist', '?')}"
    except Exception:
        pass
    try:
        from personality_core import self_state as _ss
        from datetime import datetime
        st = _ss.get()
        h = datetime.now().hour
        if 5 <= h < 12:
            ctx["time_of_day"] = "утро"
        elif 12 <= h < 18:
            ctx["time_of_day"] = "день"
        elif 18 <= h < 23:
            ctx["time_of_day"] = "вечер"
        else:
            ctx["time_of_day"] = "ночь"
        ctx["mood"] = st.mood
        ctx["energy"] = st.energy
    except Exception:
        pass
    return ctx


# ==================== LLM-ЗАПРОС ====================

def _call_llm(user_text: str, detail: bool = False) -> str | None:
    client = _get_client()
    if client is None:
        return None

    t0 = time.time()
    history = _history.get().get_messages()
    context = _build_context()
    messages = _prompt.build_messages(user_text, history=history, context=context, detail=detail)
    t1 = time.time()
    log(f"llm: build_messages {t1-t0:.2f}s (detail={detail})", tag="LLM")

    try:
        t2 = time.time()
        resp = client.chat.completions.create(
            model="deepseek-v4-flash-0731",
            messages=messages,
            max_tokens=1500,
            timeout=15.0,
            extra_body={
                "thinking": {"type": "disabled"},
                "provider": {"sort": "latency"},
            },
        )
        t3 = time.time()
        log(f"llm: create {t3-t2:.2f}s", tag="LLM")
        raw = resp.choices[0].message.content
        log(f"llm: raw_len={len(raw) if raw else 0}", tag="LLM")
        return raw
    except Exception as e:
        log(f"llm: request failed: {e}", tag="BRAIN", level="WARN")
        return None


# ==================== ПОТОКИ ====================

def _llm_worker():
    log("llm: llm_worker запущен", tag="BRAIN")
    while True:
        try:
            req_id, user_text, detail, gen = _llm_queue.get(timeout=1.0)
        except queue.Empty:
            continue

        # Отмена до старта
        if _is_cancelled(gen):
            log(f"llm: req {req_id} cancelled before start (gen={gen})", tag="BRAIN")
            _tts_queue.put((req_id, "NO_TTS"))
            continue

        log(f"llm: worker req={req_id} text={user_text!r} detail={detail}", tag="LLM")

        if not _in_dialog():
            _respond_and_say("LLM_THINKING")
        else:
            log("llm: LLM_THINKING skipped (dialog)", tag="LLM")

        raw = _call_llm(user_text, detail=detail)

        # Отмена после LLM
        if _is_cancelled(gen):
            log(f"llm: req {req_id} cancelled after llm (gen={gen})", tag="BRAIN")
            _tts_queue.put((req_id, "NO_TTS"))
            continue

        if raw is None:
            _tts_queue.put((req_id, None))
            continue

        parsed = _parser.parse_response(raw)
        if parsed is None:
            _tts_queue.put((req_id, None))
            continue

        ptype = parsed.get("type")
        log(f"llm: worker req={req_id} ptype={ptype!r}", tag="LLM")

        if ptype == "command":
            cmd = parsed.get("text", "")
            log(f"llm: command → {cmd!r}", tag="BRAIN")

            # FIX: whitelist — LLM-команда проверяется перед выполнением
            if not _whitelist.is_command_allowed(cmd):
                log(f"llm: command blocked by whitelist: {cmd!r}", tag="BRAIN", level="WARN")
                _tts_queue.put((req_id, "NOT_UNDERSTOOD"))
                continue

            from brain.router import process_command
            try:
                process_command(cmd, source=CommandSource.SYSTEM)
            except Exception as e:
                log(f"llm: process_command({cmd!r}): {e}", tag="BRAIN", level="WARN")
            _tts_queue.put((req_id, "NO_TTS"))
            continue

        if ptype == "answer":
            try:
                from brain.skills._helpers import _engine
                if _engine is None:
                    _tts_queue.put((req_id, None))
                    continue

                if detail:
                    text = parsed.get("full") or parsed.get("short") or parsed.get("minimal")
                else:
                    text = parsed.get("short") or parsed.get("full") or parsed.get("minimal")

                if not text:
                    _tts_queue.put((req_id, None))
                    continue

                response = _engine.process_llm_answer(text, parsed.get("explicit_detail", False))

                with _history_lock:
                    _history.get().add("user", user_text)
                    _history.get().add("assistant", text)

                try:
                    flag = cfg.DATA_DIR / "session_open.flag"
                    flag.parent.mkdir(exist_ok=True)
                    flag.write_text(str(time.time()))
                except Exception:
                    pass

                _tts_queue.put((req_id, response))

            except Exception as e:
                log(f"llm: answer: {e}", tag="BRAIN", level="WARN")
                _tts_queue.put((req_id, None))
            continue

        log(f"llm: unknown for {user_text!r}", tag="BRAIN")
        _tts_queue.put((req_id, "NOT_UNDERSTOOD"))


def _tts_worker():
    log("llm: tts_worker запущен", tag="BRAIN")
    next_id = 1
    buffer = {}

    while True:
        try:
            req_id, response = _tts_queue.get(timeout=1.0)
        except queue.Empty:
            continue

        buffer[req_id] = response

        while next_id in buffer:
            resp = buffer.pop(next_id)

            if resp == "NO_TTS":
                log(f"llm: tts req {next_id} skipped (NO_TTS)", tag="LLM")
                next_id += 1
                continue

            if resp is None:
                _respond_and_say("LLM_ERROR")
            elif resp == "NOT_UNDERSTOOD":
                _respond_and_say("NOT_UNDERSTOOD")
            else:
                from shared import ipc, protocol
                ipc.put(protocol.TTS_INBOX, protocol.say(
                    text=resp.text,
                    sync=True,
                    rate=resp.rate,
                    pitch=resp.pitch,
                    emotion=resp.emotion,
                ))

            next_id += 1


def _start_workers():
    global _workers_started
    with _workers_lock:
        if _workers_started:
            return
        _workers_started = True

    t1 = threading.Thread(target=_llm_worker, daemon=True, name="llm_worker")
    t1.start()

    t2 = threading.Thread(target=_tts_worker, daemon=True, name="tts_worker")
    t2.start()

    log("llm: воркеры запущены (llm_worker, tts_worker)", tag="BRAIN")


# ==================== СКИЛ ====================

class LLMSkill(Skill):
    name = "llm"
    priority = 100

    def can_handle(self, text: str) -> bool:
        return True

    def handle(self, text: str) -> bool:
        _start_workers()

        gen = _current_gen()

        detail = _wants_detail(text)
        req_id = _next_id()

        log(f"llm: handle req={req_id} text={text!r} detail={detail} gen={gen}", tag="LLM")
        _llm_queue.put((req_id, text, detail, gen))

        return True


register(LLMSkill())