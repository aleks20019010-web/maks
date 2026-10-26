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
# ОТМЕНА:
#   threading.Event _cancelled. «Стоп»/«хватит» → set(). llm_worker и
#   tts_worker проверяют is_set() перед работой и после _call_llm.
#
# ПРОВАЙДЕР:
#   provider: {"sort": "latency"} — AITUNNEL выбирает провайдера с минимальной
#   задержкой до первого токена. Без этого — балансировка по загрузке,
#   которая может направить запрос к медленному провайдеру (наблюдалось 32с).
#   Документация: https://aitunnel.ru/docs/provider [citation:1]

import os
import queue
import threading
import time

from brain.skills.base import Skill, register
from brain.skills._helpers import respond_and_say as _respond_and_say
from shared.log import log

from brain.skills.llm import _prompt, _parser, _history, _session, _whitelist


# ==================== ГЛОБАЛЬНЫЕ ОЧЕРЕДИ И ФЛАГИ ====================

_llm_queue: "queue.Queue[tuple[int, str, bool]]" = queue.Queue()
_tts_queue: "queue.Queue[tuple[int, object]]" = queue.Queue()
_cancelled = threading.Event()

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
    _cancelled.set()
    while not _llm_queue.empty():
        try:
            _llm_queue.get_nowait()
        except queue.Empty:
            break
    while not _tts_queue.empty():
        try:
            _tts_queue.get_nowait()
        except queue.Empty:
            break
    log("llm: cancelled all", tag="BRAIN")


def reset_cancel():
    _cancelled.clear()


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

    key_file = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))), "config", "llm_key.txt")
    if not os.path.exists(key_file):
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
        import pathlib
        flag = pathlib.Path("data/session_open.flag")
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
    print(f"[LLM T] build_messages: {t1-t0:.2f}s (detail={detail})")

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
        print(f"[LLM T] chat.completions.create: {t3-t2:.2f}s")
        raw = resp.choices[0].message.content
        print(f"[LLM DEBUG] _call_llm: raw={raw!r}")
        return raw
    except Exception as e:
        print(f"[LLM DEBUG] _call_llm: EXCEPTION: {e}")
        log(f"llm: request failed: {e}", tag="BRAIN", level="WARN")
        return None


# ==================== ПОТОКИ ====================

def _llm_worker():
    log("llm: llm_worker запущен", tag="BRAIN")
    while True:
        try:
            req_id, user_text, detail = _llm_queue.get(timeout=1.0)
        except queue.Empty:
            continue

        if _cancelled.is_set():
            log(f"llm: req {req_id} cancelled before start", tag="BRAIN")
            continue

        print(f"[LLM DEBUG] llm_worker: req={req_id} text={user_text!r} detail={detail}")

        if not _in_dialog():
            _respond_and_say("LLM_THINKING")
        else:
            print("[LLM T] _respond_and_say(LLM_THINKING): skipped (dialog)")

        raw = _call_llm(user_text, detail=detail)

        if _cancelled.is_set():
            log(f"llm: req {req_id} cancelled after llm", tag="BRAIN")
            continue

        if raw is None:
            _tts_queue.put((req_id, None))
            continue

        parsed = _parser.parse_response(raw)
        if parsed is None:
            _tts_queue.put((req_id, None))
            continue

        ptype = parsed.get("type")
        print(f"[LLM DEBUG] llm_worker: req={req_id} ptype={ptype!r}")

        if ptype == "command":
            cmd = parsed.get("text", "")
            log(f"llm: command → {cmd!r}", tag="BRAIN")
            from brain.router import process_command
            try:
                process_command(cmd)
            except Exception as e:
                log(f"llm: process_command({cmd!r}): {e}", tag="BRAIN", level="WARN")
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
                    import pathlib
                    flag = pathlib.Path("data/session_open.flag")
                    flag.parent.mkdir(exist_ok=True)
                    flag.write_text(str(time.time()))
                except Exception:
                    pass

                _tts_queue.put((req_id, response))

            except Exception as e:
                print(f"[LLM DEBUG] llm_worker answer: EXCEPTION: {e}")
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

            if _cancelled.is_set():
                log(f"llm: tts req {next_id} cancelled", tag="BRAIN")
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
        reset_cancel()

        detail = _wants_detail(text)
        req_id = _next_id()

        print(f"[LLM DEBUG] handle: req={req_id} text={text!r} detail={detail}")
        _llm_queue.put((req_id, text, detail))

        return True


register(LLMSkill())