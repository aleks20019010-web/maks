# brain/skills/_helpers.py
# Общие хелперы для навыков: запрос фразы у Personality + отправка в TTS через IPC.
#
# ── UI_BRIDGE (2026-11-18) ──────────────────────────────────────
#   respond_and_say: после ipc.put(TTS_INBOX, msg) ответ Макса
#   дублируется в UI-чат через shared.ui_bridge.append_chat("assistant", ...).
#   Дублируются ОБА пути: Путь 1 (Personality) и Путь 2 (fallback-фраза).
#   Путь 2 — аварийная фраза без привязки к Personality, но пользователь
#   её слышит, значит она должна быть и в чате — иначе история в UI
#   расходится с тем, что реально сказано.

from shared import ipc
from shared import protocol
from shared import metrics
from shared.log import log


_engine = None


def set_engine(engine):
    global _engine
    _engine = engine


def phrase(key, **kw):
    if _engine is None:
        return None
    try:
        result = _engine.pick_full(key, **kw)
        if isinstance(result, dict):
            return result.get("text")
        return result
    except Exception as e:
        from brain.errors import handle_error
        handle_error(e, context=f"helpers.phrase({key})", speak=False)
    return None


def _extract_text(response) -> str:
    if response is None:
        return ""
    text = getattr(response, "text", "")
    if isinstance(text, dict):
        return text.get("text", "") or ""
    if isinstance(text, str):
        return text
    return str(text) if text else ""


def _extract_attr(response, name, default=None):
    val = getattr(response, name, None)
    if val is not None:
        return val
    text = getattr(response, "text", None)
    if isinstance(text, dict):
        return text.get(name, default)
    return default


def _current_tts_seq() -> int:
    try:
        from workers.tts.state_file import get_state
        return int(get_state().get("phrase_seq", 0))
    except Exception:
        return 0


def respond_and_say(intent, sync=True, is_event=False, **kw) -> int | None:
    """Спросить Personality и отправить ответ в TTS через IPC.

    Возвращает base_seq (значение phrase_seq до отправки в TTS_INBOX) —
    когда TTS начнёт эту фразу, state_file["phrase_seq"] станет > base_seq.
    Если фраза не отправлена, возвращает None.

    UI_BRIDGE: после успешной отправки в TTS ответ дублируется в UI-чат
    (оба пути: Personality и fallback).
    """
    if _engine is None:
        log(f"helpers.respond_and_say({intent}): engine не установлен", tag="BRAIN", level="WARN")
        return None

    # Путь 1 — основной (command или event)
    try:
        if is_event:
            response = _engine.respond_event(intent, **kw)
        else:
            response = _engine.respond(intent, **kw)
        text = _extract_text(response)
        if text:
            base_seq = _current_tts_seq()
            msg = protocol.say(
                text=text,
                sync=sync,
                rate=_extract_attr(response, "rate"),
                pitch=_extract_attr(response, "pitch"),
                emotion=_extract_attr(response, "emotion"),
            )
            ipc.put(protocol.TTS_INBOX, msg)

            # ── UI_BRIDGE (2026-11-18): чат-лог ответа Макса ──
            from shared.ui_bridge import append_chat
            append_chat("assistant", text, meta=intent)

            metrics.inc("tts_said")
            explain = getattr(response, "explain", None)
            if explain:
                log(f"🎭 {explain}", tag="BRAIN")
            return base_seq
    except Exception as e:
        from brain.errors import handle_error
        handle_error(e, context=f"helpers.respond_and_say({intent})", speak=False)

    # Путь 2 — fallback
    try:
        text = phrase(intent, **kw)
        if text:
            base_seq = _current_tts_seq()
            ipc.put(protocol.TTS_INBOX, protocol.say(text, sync=sync))

            # ── UI_BRIDGE (2026-11-18): fallback тоже дублируем в чат ──
            from shared.ui_bridge import append_chat
            append_chat("assistant", text, meta=intent)

            metrics.inc("tts_said")
            return base_seq
    except Exception as e:
        from brain.errors import handle_error
        handle_error(e, context=f"helpers.respond_and_say fallback({intent})", speak=False)

    return None