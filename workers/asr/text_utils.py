# workers/asr/text_utils.py
# Грамматика Vosk, выделение wake-слова, контекстный матчинг hot words,
# защита от эха TTS и дублей команд.

import difflib
import json
import re
import threading
import time

from shared import runtime_state as rt
from shared.log import log


ECHO_SIMILARITY_THRESHOLD = 0.85
WAKE_FUZZY = 0.7
HOT_CONTEXT_MAX_WORDS = 3
DUP_CMD_WINDOW_SEC = 4.0

YES_NO_WORDS = {
    "да", "нет", "ок", "окей", "подтверждаю",
    "не надо", "неважно", "не важно", "не хочу", "отмена",
}
CONTROL_WORDS = {"дальше", "назад", "громче", "тише"}


def load_hot_words() -> set[str]:
    try:
        from brain.skills.controls import ControlsSkill
        return set(ControlsSkill.HOT_WORDS)
    except Exception:
        return set()


def build_grammar_words(hot_words) -> list[str]:
    words = {"макс"} | CONTROL_WORDS | YES_NO_WORDS
    for w in hot_words or ():
        if isinstance(w, str):
            words.add(w)
    ok = set()
    for w in words:
        w = w.strip().lower()
        if w and re.fullmatch(r"[а-яё\- ]+", w):
            ok.add(w)
    return sorted(ok) + ["[unk]"]


def build_grammar_json(hot_words) -> str:
    return json.dumps(build_grammar_words(hot_words), ensure_ascii=False)


def after_wake(text: str) -> str | None:
    """Найти обращение «Макс» (с нечётким сравнением) и вернуть хвост команды."""
    tl = text.lower()
    for m in re.finditer(r"[а-яёa-z]+", tl):
        w = m.group(0)
        if w == "макс" or (
            abs(len(w) - 4) <= 1
            and difflib.SequenceMatcher(None, w, "макс").ratio() >= WAKE_FUZZY
        ):
            return text[m.end():].lstrip(" ,.!?:;-—").strip()
    return None


def hot_word_appropriate(text_lower: str) -> bool:
    if text_lower in YES_NO_WORDS:
        return rt.exists(rt.FLAG_EXPECTING_INPUT)
    return True


class CommandFilter:
    """Кэш hot words, анти-эхо и анти-дубль."""

    def __init__(self):
        self._hot: set[str] = set()
        self._hot_lock = threading.Lock()
        self._last_cmd_text = ""
        self._last_cmd_at = 0.0
        self._cmd_lock = threading.Lock()

    def update_hot_words(self, words: set[str]):
        with self._hot_lock:
            self._hot = set(words)

    def is_command_word(self, text: str) -> bool:
        with self._hot_lock:
            hot = self._hot
        if not hot:
            return False
        tl = text.lower().strip()
        return bool(tl) and tl in hot

    def match_hot_word_context(self, gigaam_text: str, hint_word: str) -> str | None:
        clean = re.sub(r"[^\w\s\-]", "", (gigaam_text or "").lower())
        clean = re.sub(r"\s+", " ", clean).strip()
        if not clean:
            return None

        if self.is_command_word(clean):
            return clean

        hint = (hint_word or "").lower().strip()
        if not hint or hint in YES_NO_WORDS or not self.is_command_word(hint):
            return None

        words = clean.split()
        if 1 <= len(words) <= HOT_CONTEXT_MAX_WORDS:
            pattern = rf"(?:^|\s){re.escape(hint)}(?:$|\s)"
            if re.search(pattern, clean):
                return hint
        return None

    def is_echo(self, text: str) -> bool:
        if self.is_command_word(text):
            return False
        try:
            from workers.tts.state_file import get_state
            last = get_state().get("last_text", "") or ""
        except Exception:
            return False
        if not last or not text or len(last) > 500 or len(text) > 500:
            return False
        ratio = difflib.SequenceMatcher(None, last.lower().strip(), text.lower().strip()).ratio()
        if ratio >= ECHO_SIMILARITY_THRESHOLD:
            log(f"asr: echo '{text}' ~ '{last}' ({ratio:.2f})", tag="ASR")
            return True
        return False

    def is_dup_command(self, text: str) -> bool:
        norm = text.lower().strip()
        with self._cmd_lock:
            now = time.time()
            if norm and norm == self._last_cmd_text and (now - self._last_cmd_at) < DUP_CMD_WINDOW_SEC:
                return True
            self._last_cmd_text = norm
            self._last_cmd_at = now
            return False