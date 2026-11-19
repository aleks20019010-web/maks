# shared/ui_bridge.py
# Мост между воркерами (music, brain, tts) и окном UI.
#
# ── AVATAR TELEMETRY (2026-11-19) ──────────────────────────────
#   update_avatar_telemetry(**fields) — атомарно обновляет общий JSON
#   (mic_rms, tts_rms, emotion, music_bands) в data/avatar_telemetry.json.
#   read_avatar_telemetry() — читает его для UI-виджета аватара.
#   Поля:
#     mic_rms     — RMS микрофона (0..1), пишет asr worker
#     tts_rms     — RMS текущего TTS-аудио (0..1), пишет tts worker
#     emotion     — текущая эмоция ("idle"|"listening"|"speaking"|"music"|"sleep"|"curious")
#     music_bands — [low, mid, high, beat] (0..1), пишет music worker
#   Любая запись/чтение молча игнорирует ошибки — телеметрия не критична.

import json
import os
import re
import threading
import time
from collections import deque

from shared import config as cfg


MUSIC_UI_FILE = os.path.join(cfg.DATA_DIR, "music_ui.json")
CHAT_LOG_FILE = os.path.join(cfg.DATA_DIR, "ui_chat.jsonl")

_TELEMETRY_FILE = cfg.RUNTIME_DIR / "avatar_telemetry.json"

_chat_lock = threading.Lock()

# Паузы в конце фразы → точка
_SIL_END_RE = re.compile(r"\s*\bsil\s*<\s*\[\s*(?:100|150)\s*\]\s*>\s*[.!?…]*\s*$", re.IGNORECASE)
# Паузы в середине фразы
_SIL_150_MID_RE = re.compile(r"\s*\bsil\s*<\s*\[\s*150\s*\]\s*>\s*", re.IGNORECASE)
_SIL_100_MID_RE = re.compile(r"\s*\bsil\s*<\s*\[\s*100\s*\]\s*>\s*", re.IGNORECASE)
# Прочие sil<...> и технические теги
_SIL_ANY_RE = re.compile(r"\s*\bsil\s*<\s*\[\s*\d+\s*\]\s*>\s*", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_STRESS_RE = re.compile(r"\+([аеёиоуыэюяАЕЁИОУЫЭЮЯ])")


def clean_tts_text(text: str) -> str:
    """Преобразовать TTS-разметку в читаемый текст для чата:
      - sil<[150]> / sil<[100]> в конце фразы → '.'
      - sil<[150]> в середине фразы → '...'
      - sil<[100]> в середине фразы → ','
      - убрать знаки ударения (+) и прочие теги.
    """
    s = (text or "").strip()
    if not s:
        return ""

    # 1. Финальная пауза в конце фразы → точка
    s = _SIL_END_RE.sub(".", s)
    # 2. Паузы в середине фразы
    s = _SIL_150_MID_RE.sub("... ", s)
    s = _SIL_100_MID_RE.sub(", ", s)
    # 3. Остальные нестандартные sil и XML-теги
    s = _SIL_ANY_RE.sub(" ", s)
    s = _TAG_RE.sub(" ", s)
    # 4. Знаки ударения перед гласными
    s = _STRESS_RE.sub(r"\1", s)
    # 5. Нормализация пробелов и пунктуации
    s = re.sub(r"\s+([,.!?;:…])", r"\1", s)
    s = re.sub(r"([,.!?;:])([^\s\d.…])", r"\1 \2", s)
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s


def _atomic_write_json(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        for _ in range(5):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                time.sleep(0.005)
    except Exception:
        pass
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass


def write_music_ui(state_dict: dict) -> None:
    """Записать актуальное состояние плеера для UI."""
    payload = dict(state_dict)
    payload["ts"] = time.time()
    _atomic_write_json(MUSIC_UI_FILE, payload)


def read_music_ui() -> dict:
    """Прочитать состояние плеера для UI."""
    if not os.path.exists(MUSIC_UI_FILE):
        return {}
    try:
        with open(MUSIC_UI_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def append_chat(role: str, text: str, meta: str = "") -> None:
    """Добавить сообщение в ленту диалога UI."""
    clean = clean_tts_text(text) if role == "assistant" else (text or "").strip()
    if not clean:
        return

    entry = {
        "ts": time.time(),
        "role": role,
        "text": clean,
        "meta": meta,
    }
    line = json.dumps(entry, ensure_ascii=False) + "\n"

    with _chat_lock:
        try:
            os.makedirs(os.path.dirname(CHAT_LOG_FILE), exist_ok=True)
            with open(CHAT_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass


def read_chat_history(limit: int = 100) -> list[dict]:
    """Прочитать последние N сообщений чата."""
    if not os.path.exists(CHAT_LOG_FILE):
        return []
    out = deque(maxlen=limit)
    try:
        with open(CHAT_LOG_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        return []
    return list(out)


# ── AVATAR TELEMETRY (2026-11-19) ──────────────────────────────
def update_avatar_telemetry(**fields) -> None:
    """Атомарно обновить поля телеметрии аватара.

    Поля:
      mic_rms     (0..1) — RMS микрофона, пишет asr worker
      tts_rms     (0..1) — RMS текущего TTS-аудио, пишет tts worker
      emotion     (str)  — "idle"|"listening"|"speaking"|"music"|"sleep"|"curious"
      music_bands (list) — [low, mid, high, beat], пишет music worker
    """
    data = {}
    try:
        if _TELEMETRY_FILE.exists():
            with open(_TELEMETRY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
    except Exception:
        data = {}

    data.update(fields)
    data["ts"] = time.time()
    tmp = _TELEMETRY_FILE.with_suffix(".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        tmp.replace(_TELEMETRY_FILE)
    except Exception:
        pass


def read_avatar_telemetry() -> dict:
    """Прочитать телеметрию аватара. {} — если файла ещё нет или ошибка."""
    try:
        if _TELEMETRY_FILE.exists():
            with open(_TELEMETRY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}