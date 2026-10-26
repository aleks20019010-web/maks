# brain/skills/llm/skill.py
# LLM-скил — точка входа.
#
# Режимы:
#   A. Fallback (router не понял) → LLM решает: command / answer / unknown.
#   B. Явный вопрос (сессия открыта или «расскажи», «почему») → LLM отвечает.
#
# Сессия:
#   - открывается — после ответа LLM на вопрос.
#   - закрывается — явная команда / тишина 60 сек / «хватит».
#
# История — в _history.py.
# Парсинг — в _parser.py.
# Whitelist — в _whitelist.py.
# Промпт — в _prompt.py.
#
# Ответ LLM:
#   - command → router.process_command() повторно.
#   - answer → engine.process_llm_answer() → TTS.
#   - unknown → «Не понял».
#
# FIX: перед _call_llm — _respond_and_say("LLM_THINKING") — фраза «Думаю»,
# только вне диалога (session_open.flag свежий < 20 сек — не говорим).
#
# FIX: reasoning отключён (extra_body={"thinking": {"type": "disabled"}}).
#
# FIX: добавлены замеры времени (T+...).
#
# FIX: диалоговый режим — после ответа LLM пишем data/session_open.flag.
#
# ДВА РЕЖИМА ОТВЕТА:
#   - обычный: {"short": "..."} — быстро, одно предложение.
#   - подробный: {"full": "...", "short": "...", "explicit_detail": true} —
#     только если пользователь явно попросил «подробнее / расскажи / объясни».
#
# FIX: в _call_llm добавлен timeout=15.0 — иначе SDK ждёт 600 секунд
# (10 минут), и brain встаёт колом. AITUNNEL рекомендует явно задавать timeout.

import os
import time

from brain.skills.base import Skill, register
from brain.skills._helpers import respond_and_say as _respond_and_say
from shared.log import log

from brain.skills.llm import _prompt, _parser, _history, _session, _whitelist


# ==================== КЛИЕНТ ====================

_client = None
_client_error = None


def _get_client():
    """Ленивый клиент OpenAI (AITUNNEL)."""
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
        _client = OpenAI(api_key=key, base_url="https://api.aitunnel.ru/v1/")
    except Exception as e:
        _client_error = f"OpenAI init: {e}"
        log(f"llm: {_client_error}", tag="BRAIN", level="ERROR")
        return None

    return _client


# ==================== ДИАЛОГ ====================

def _in_dialog() -> bool:
    """Диалоговый режим: brain ответил < 20 сек назад."""
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
    """Пользователь явно просит подробный ответ?"""
    tl = text.lower().strip()
    if not tl:
        return False
    for trigger in _DETAIL_TRIGGERS:
        if trigger in tl:
            return True
    return False


# ==================== КОНТЕКСТ ====================

def _build_context() -> dict:
    """Собрать контекст: что играет, погода, время, mood, energy."""
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
    """Отправить запрос в LLM. detail=True — развёрнутый ответ.
    
    timeout=30.0 — критично: по умолчанию OpenAI SDK ждёт 600 секунд,
    и brain зависает на 10 минут (наблюдалось 602с в логе).
    """
    client = _get_client()
    if client is None:
        print("[LLM DEBUG] _call_llm: client is None")
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
            timeout=30.0,
            extra_body={"thinking": {"type": "disabled"}},
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


# ==================== СКИЛ ====================

class LLMSkill(Skill):
    name = "llm"
    priority = 100  # самый низкий — fallback

    def can_handle(self, text: str) -> bool:
        return True

    def handle(self, text: str) -> bool:
        """Обработать запрос через LLM."""
        t_start = time.time()
        print(f"[LLM DEBUG] handle: text={text!r}")

        detail = _wants_detail(text)
        print(f"[LLM DEBUG] handle: detail={detail}")

        # Фраза перед LLM — только вне диалога
        if not _in_dialog():
            t0 = time.time()
            _respond_and_say("LLM_THINKING")
            t1 = time.time()
            print(f"[LLM T] _respond_and_say(LLM_THINKING): {t1-t0:.2f}s")
        else:
            print("[LLM T] _respond_and_say(LLM_THINKING): skipped (dialog)")

        t2 = time.time()
        raw = _call_llm(text, detail=detail)
        t3 = time.time()
        print(f"[LLM T] _call_llm: {t3-t2:.2f}s")

        if raw is None:
            print("[LLM DEBUG] handle: raw is None → LLM_ERROR")
            _respond_and_say("LLM_ERROR")
            return True

        t4 = time.time()
        parsed = _parser.parse_response(raw)
        t5 = time.time()
        print(f"[LLM T] _parser.parse_response: {t5-t4:.2f}s")

        if parsed is None:
            print("[LLM DEBUG] handle: parsed is None → LLM_ERROR")
            _respond_and_say("LLM_ERROR")
            return True

        ptype = parsed.get("type")
        print(f"[LLM DEBUG] handle: ptype={ptype!r}")

        if ptype == "command":
            cmd = parsed.get("text", "")
            log(f"llm: command → {cmd!r}", tag="BRAIN")
            from brain.router import process_command
            try:
                t6 = time.time()
                process_command(cmd)
                t7 = time.time()
                print(f"[LLM T] process_command: {t7-t6:.2f}s")
            except Exception as e:
                log(f"llm: process_command({cmd!r}): {e}", tag="BRAIN", level="WARN")
                _respond_and_say("LLM_ERROR")
            return True

        if ptype == "answer":
            self._handle_answer(parsed, text, detail=detail)
            t_end = time.time()
            print(f"[LLM T] handle total: {t_end-t_start:.2f}s")
            return True

        log(f"llm: unknown for {text!r}", tag="BRAIN")
        _respond_and_say("NOT_UNDERSTOOD")
        return True

    def _handle_answer(self, parsed: dict, user_text: str, detail: bool = False) -> None:
        """Ответ LLM → engine.process_llm_answer → TTS."""
        t_start = time.time()
        print(f"[LLM DEBUG] _handle_answer: parsed={parsed} detail={detail}")
        try:
            t0 = time.time()
            from brain.skills._helpers import _engine
            t1 = time.time()
            print(f"[LLM T] import _engine: {t1-t0:.2f}s")
            print(f"[LLM DEBUG] _handle_answer: _engine={_engine is not None}")
            if _engine is None:
                _respond_and_say("LLM_ERROR")
                return

            t2 = time.time()
            from personality_core import self_state as _ss
            from personality_core import prosody as _prosody
            st = _ss.get()
            style = _prosody.get_answer_style(st, parsed.get("explicit_detail", False))
            t3 = time.time()
            print(f"[LLM T] self_state + get_answer_style: {t3-t2:.2f}s")
            print(f"[LLM DEBUG] _handle_answer: style={style}")

            # detail → full (fallback short), иначе short (fallback full/minimal)
            if detail:
                text = parsed.get("full") or parsed.get("short") or parsed.get("minimal")
            else:
                text = parsed.get("short") or parsed.get("full") or parsed.get("minimal")

            print(f"[LLM DEBUG] _handle_answer: text={text!r}")
            if not text:
                _respond_and_say("LLM_ERROR")
                return

            t4 = time.time()
            response = _engine.process_llm_answer(text, parsed.get("explicit_detail", False))
            t5 = time.time()
            print(f"[LLM T] process_llm_answer: {t5-t4:.2f}s")
            print(f"[LLM DEBUG] _handle_answer: response={response}")

            if response and response.text:
                from shared import ipc, protocol
                t6 = time.time()
                ipc.put(protocol.TTS_INBOX, protocol.say(
                    text=response.text,
                    sync=True,
                    rate=response.rate,
                    pitch=response.pitch,
                    emotion=response.emotion,
                ))
                t7 = time.time()
                print(f"[LLM T] ipc.put(sync=True): {t7-t6:.2f}s")

            t8 = time.time()
            sess = _session.get()
            sess.open()
            sess.touch()
            t9 = time.time()
            print(f"[LLM T] session.open+touch: {t9-t8:.2f}s")

            t10 = time.time()
            _history.get().add("user", user_text)
            _history.get().add("assistant", text)
            t11 = time.time()
            print(f"[LLM T] history.add x2: {t11-t10:.2f}s")

            # Диалоговый режим: 20 сек ASR пропускает без wake word
            try:
                import pathlib
                flag = pathlib.Path("data/session_open.flag")
                flag.parent.mkdir(exist_ok=True)
                flag.write_text(str(time.time()))
            except Exception:
                pass

            log(f"llm: answer (style={style}, detail={detail})", tag="BRAIN")
            t_end = time.time()
            print(f"[LLM T] _handle_answer total: {t_end-t_start:.2f}s")

        except Exception as e:
            print(f"[LLM DEBUG] _handle_answer: EXCEPTION: {e}")
            log(f"llm: _handle_answer: {e}", tag="BRAIN", level="WARN")
            _respond_and_say("LLM_ERROR")


register(LLMSkill())