# brain/skills/_helpers.py
# Общие хелперы для навыков: запрос фразы у Personality + отправка в TTS через IPC.
#
# v6.4: respond_and_say(..., is_event=False) — для событий (discovery, wake,
# now_playing) вызывается engine.respond_event (event.type="event"), а не
# engine.respond (event.type="command"). SELF-реакции (behavior_*) срабатывают
# только на command, не на event. Discovery не должен перехватываться
# behavior_energy_low.

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


def respond_and_say(intent, sync=True, is_event=False, **kw):
    """Спросить Personality и отправить ответ в TTS через IPC.

    is_event=True — событие (discovery, wake, now_playing):
        engine.respond_event → event.type="event" → SELF-реакции (behavior_*)
        НЕ срабатывают, composer собирает фразу напрямую.
    is_event=False (по умолчанию) — команда: engine.respond → event.type="command"
        → SELF-реакции (behavior_*) срабатывают.

    Путь 1: engine.respond/respond_event → если текст → ipc.put(TTS_INBOX, say).
    Путь 2 (fallback): engine.pick_full → если фраза → ipc.put.
    Путь 3 (тишина): ничего.
    """
    if _engine is None:
        log(f"helpers.respond_and_say({intent}): engine не установлен", tag="BRAIN", level="WARN")
        return

    # Путь 1 — новый (command или event)
    try:
        if is_event:
            response = _engine.respond_event(intent, **kw)
        else:
            response = _engine.respond(intent, **kw)
        text = _extract_text(response)
        if text:
            msg = protocol.say(
                text=text,
                sync=sync,
                rate=_extract_attr(response, "rate"),
                pitch=_extract_attr(response, "pitch"),
                emotion=_extract_attr(response, "emotion"),
            )
            ipc.put(protocol.TTS_INBOX, msg)
            metrics.inc("tts_said")
            explain = getattr(response, "explain", None)
            if explain:
                log(f"🎭 {explain}", tag="BRAIN")
            return
    except Exception as e:
        from brain.errors import handle_error
        handle_error(e, context=f"helpers.respond_and_say({intent})", speak=True)

    # Путь 2 — fallback
    try:
        text = phrase(intent, **kw)
        if text:
            ipc.put(protocol.TTS_INBOX, protocol.say(text, sync=sync))
            metrics.inc("tts_said")
    except Exception as e:
        from brain.errors import handle_error
        handle_error(e, context=f"helpers.respond_and_say fallback({intent})", speak=False)