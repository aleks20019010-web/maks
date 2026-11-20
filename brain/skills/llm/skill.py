# brain/skills/llm/skill.py
# LLM-скил — точка входа.

import queue
import threading
import time

from brain.skills.base import Skill, register
from brain.skills._helpers import respond_and_say as _respond_and_say
from shared import runtime_state as rt
from shared.log import log
from shared import config as cfg
from shared.sources import CommandSource
from shared.ui_bridge import append_chat

from brain.skills.llm import _prompt, _parser, _history, _whitelist


_llm_queue: "queue.Queue[tuple[str, bool, int]]" = queue.Queue()

_generation = 0
_generation_lock = threading.Lock()

_history_lock = threading.Lock()
_client_lock = threading.Lock()

_workers_started = False
_workers_lock = threading.Lock()

_NET_COOLDOWN_SEC = 30.0
_net_blocked_until = 0.0

_client = None
_client_error = None


def cancel_all():
    """Отменить всё: увеличить generation. Очистить историю."""
    global _generation
    with _generation_lock:
        _generation += 1
        gen = _generation

    try:
        with _history_lock:
            _history.get().clear()
    except Exception as e:
        log(f"llm: cancel_all history.clear: {e}", tag="BRAIN", level="WARN")

    log(f"llm: cancelled all (gen={gen})", tag="BRAIN")


def _current_gen() -> int:
    with _generation_lock:
        return _generation


def _is_cancelled(gen: int) -> bool:
    with _generation_lock:
        return _generation != gen


def _get_client():
    global _client, _client_error
    if _client is not None:
        return _client

    with _client_lock:
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


def _in_dialog() -> bool:
    return rt.exists(rt.FLAG_SESSION_OPEN)


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


def _call_llm(user_text: str, detail: bool = False) -> str | None:
    global _net_blocked_until
    if time.time() < _net_blocked_until:
        return None

    client = _get_client()
    if client is None:
        return None

    t0 = time.time()
    with _history_lock:
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
        _net_blocked_until = 0.0
        log(f"llm: create {t3-t2:.2f}s", tag="LLM")
        raw = resp.choices[0].message.content
        log(f"llm: raw_len={len(raw) if raw else 0}", tag="LLM")
        return raw
    except Exception as e:
        err_name = type(e).__name__
        if "Connection" in err_name or "Timeout" in err_name:
            _net_blocked_until = time.time() + _NET_COOLDOWN_SEC
            log(f"llm: нет связи с API ({err_name}) — пауза {_NET_COOLDOWN_SEC:.0f}с",
                tag="BRAIN", level="WARN")
        else:
            log(f"llm: request failed: {e}", tag="BRAIN", level="WARN")
        return None


def _llm_worker():
    log("llm: llm_worker запущен", tag="BRAIN")
    while True:
        try:
            user_text, detail, gen = _llm_queue.get(timeout=1.0)
        except queue.Empty:
            continue

        try:
            if _is_cancelled(gen):
                log(f"llm: cancelled before start (gen={gen})", tag="BRAIN")
                continue

            if time.time() < _net_blocked_until:
                _respond_and_say("LLM_ERROR")
                continue

            log(f"llm: worker text={user_text!r} detail={detail}", tag="LLM")

            if not _in_dialog():
                _respond_and_say("LLM_THINKING")
            else:
                log("llm: LLM_THINKING skipped (dialog)", tag="LLM")

            raw = _call_llm(user_text, detail=detail)

            if _is_cancelled(gen):
                log(f"llm: cancelled after llm (gen={gen})", tag="BRAIN")
                continue

            if raw is None:
                _respond_and_say("LLM_ERROR")
                continue

            parsed = _parser.parse_response(raw)
            if parsed is None:
                _respond_and_say("LLM_ERROR")
                continue

            ptype = parsed.get("type")
            log(f"llm: worker ptype={ptype!r}", tag="LLM")

            if ptype == "command":
                cmd = parsed.get("text", "")
                log(f"llm: command → {cmd!r}", tag="BRAIN")

                if cmd.strip().lower() == user_text.strip().lower():
                    log("llm: command == user_text, не выполняем повторно",
                        tag="BRAIN", level="WARN")
                    _respond_and_say("NOT_UNDERSTOOD")
                    continue

                if not _whitelist.is_command_allowed(cmd):
                    log(f"llm: command blocked by whitelist: {cmd!r}",
                        tag="BRAIN", level="WARN")
                    _respond_and_say("NOT_UNDERSTOOD")
                    continue

                from brain.router import process_command
                try:
                    process_command(cmd, source=CommandSource.SYSTEM)
                except Exception as e:
                    log(f"llm: process_command({cmd!r}): {e}",
                        tag="BRAIN", level="WARN")
                continue

            if ptype == "answer":
                try:
                    from brain.skills._helpers import _engine
                    if _engine is None:
                        _respond_and_say("LLM_ERROR")
                        continue

                    if detail:
                        text = parsed.get("full") or parsed.get("short") or parsed.get("minimal")
                    else:
                        text = parsed.get("short") or parsed.get("full") or parsed.get("minimal")

                    if not text:
                        _respond_and_say("LLM_ERROR")
                        continue

                    response = _engine.process_llm_answer(text, parsed.get("explicit_detail", False))

                    with _history_lock:
                        _history.get().add("user", user_text)
                        _history.get().add("assistant", text)

                    rt.set(rt.FLAG_SESSION_OPEN, True, ttl=rt.TTL_SESSION_OPEN)

                    if response is not None:
                        _send_response(response)
                except Exception as e:
                    log(f"llm: answer: {e}", tag="BRAIN", level="WARN")
                    _respond_and_say("LLM_ERROR")
                continue

            log(f"llm: unknown for {user_text!r}", tag="BRAIN")
            _respond_and_say("NOT_UNDERSTOOD")

        except Exception as e:
            try:
                log(f"llm: worker crash: {e}", tag="BRAIN", level="ERROR")
            except Exception:
                pass
            try:
                _respond_and_say("LLM_ERROR")
            except Exception:
                pass


def _send_response(response):
    if response is None:
        _respond_and_say("LLM_ERROR")
        return
    try:
        append_chat("assistant", response.text, meta="llm")
    except Exception:
        pass
    from shared import ipc, protocol
    ipc.put(protocol.TTS_INBOX, protocol.say(
        text=response.text,
        sync=True,
        rate=response.rate,
        pitch=response.pitch,
        emotion=response.emotion,
    ))


def _start_workers():
    global _workers_started
    with _workers_lock:
        if _workers_started:
            return
        _workers_started = True

    t1 = threading.Thread(target=_llm_worker, daemon=True, name="llm_worker")
    t1.start()

    log("llm: воркер запущен (llm_worker, без tts_worker)", tag="BRAIN")


class LLMSkill(Skill):
    name = "llm"
    priority = 100

    def can_handle(self, text: str) -> bool:
        return False

    def handle(self, text_or_command) -> bool:
        from brain.nlu.types import Command
        if isinstance(text_or_command, Command):
            text = text_or_command.original_text or text_or_command.normalized_text
        else:
            text = text_or_command

        if not text or not isinstance(text, str):
            return False

        _start_workers()

        gen = _current_gen()
        detail = _wants_detail(text)

        log(f"llm: handle text={text!r} detail={detail} gen={gen}", tag="LLM")
        _llm_queue.put((text, detail, gen))

        return True


register(LLMSkill())