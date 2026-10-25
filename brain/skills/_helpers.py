# brain/skills/_helpers.py
# Общие хелперы для навыков: запрос фразы у Personality + отправка в TTS через IPC.

from shared import ipc
from shared import protocol
from shared import metrics
from shared.log import log


_engine = None  # ссылка на PersonalityEngine (ставится из brain/main.py)


def set_engine(engine):
    """Установить ссылку на PersonalityEngine (вызывается из brain/main.py)."""
    global _engine
    _engine = engine


def phrase(key, **kw):
    """Запросить фразу у PersonalityEngine.pick_full. Текст или None."""
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
    """Достать строку из JarvisResponse.text (может быть str или dict)."""
    if response is None:
        return ""
    text = getattr(response, "text", "")
    if isinstance(text, dict):
        return text.get("text", "") or ""
    if isinstance(text, str):
        return text
    return str(text) if text else ""


def _extract_attr(response, name, default=None):
    """Достать поле из response или из dict-фразы внутри text."""
    val = getattr(response, name, None)
    if val is not None:
        return val
    text = getattr(response, "text", None)
    if isinstance(text, dict):
        return text.get(name, default)
    return default


def respond_and_say(intent, sync=True, **kw):
    """Спросить Personality и отправить ответ в TTS через IPC.

    Путь 1: engine.respond(intent) → если есть текст → ipc.put(TTS_INBOX, say).
    Путь 2 (fallback): engine.pick_full(intent) → если фраза → ipc.put.
    Путь 3 (тишина): ничего.
    """
    if _engine is None:
        log(f"helpers.respond_and_say({intent}): engine не установлен", tag="BRAIN", level="WARN")
        return

    # Путь 1 — новый
    try:
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