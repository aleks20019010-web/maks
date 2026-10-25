# brain/skills/controls.py
# Управление плеером через IPC (music_inbox).
# Hot words работают без wake word.
#
# VLC в workers/music/. Здесь — только команды в music_inbox.
#
# Голос:
#   - next / prev / volume_*: рандом 15% фразы. 5-е за 30 сек — принудительно NEXT_OFTEN.
#   - pause / resume / stop: всегда фраза (важно подтвердить).
#   - now_playing: шлёт запрос в music_inbox, ответ — event_now_playing в brain_inbox.
#
# Extra hot words: «давай», «да», «ок» — только для ASR, без действия в controls.
#   Нужно для ответов на pending_music_ask («давай рок») и cleanup («да»).
#
# appraisal: на next публикуем SKIP в reaction (до переключения),
# чтобы реакция знала, что играло сейчас.

import random
import time

from brain.skills.base import Skill, register
from brain.skills._helpers import respond_and_say as _respond_and_say
from shared import ipc
from shared import protocol
from shared import config as cfg
from shared.log import log


VOICE_RANDOM_CHANCE = 0.15


class ControlsSkill(Skill):
    name = "controls"
    priority = 20

    SIMPLE = {
        "дальше": "next", "next": "next", "следующий": "next",
        "пропусти": "next", "пропустить": "next",
        "назад": "prev", "предыдущий": "prev", "предыдущая": "prev",
        "вернись": "prev", "верни": "prev",
        "пауза": "pause", "стоп пауза": "pause",
        "продолжить": "resume", "продолжи": "resume",
        "играть": "resume", "плей": "resume", "играй": "resume",
        "стоп": "stop", "останови": "stop", "остановись": "stop",
        "стоп музыка": "stop",
        "громче": "volume_up", "погромче": "volume_up",
        "тише": "volume_down", "потише": "volume_down",
        "что играет": "now_playing", "что за песня": "now_playing",
        "что за трек": "now_playing", "что сейчас": "now_playing",
    }

    # Extra hot words — только для ASR, без действия в controls.
    # Нужны для ответов на pending_music_ask («давай рок») и cleanup («да»).
    EXTRA_HOT_WORDS = {
        "давай",
        "да",
        "yes",
        "ок",
        "окей",
        "подтверждаю",
        "нет",
        "no",
        "не надо",
        "неважно",
        "не важно",
        "не хочу",
        "отмена",
    }

    HOT_WORDS = set(SIMPLE.keys()) | EXTRA_HOT_WORDS

    PHRASE_MAP = {
        "next": "NEXT_TRACK",
        "prev": "PREV_TRACK",
        "pause": "PAUSE",
        "resume": "RESUME",
        "volume_up": "VOLUME_UP",
        "volume_down": "VOLUME_DOWN",
    }

    def __init__(self):
        self._next_count = 0
        self._next_window_start = 0.0

    def can_handle(self, text: str) -> bool:
        t = text.lower().strip()
        return t in self.SIMPLE

    def handle(self, text: str) -> str | None:
        t = text.lower().strip()
        action = self.SIMPLE.get(t)
        if action is None:
            return None

        log(f"controls: {t} → {action}", tag="BRAIN")

        if action == "now_playing":
            ipc.put(protocol.MUSIC_INBOX, protocol.music_now_playing_request())
            return None

        if action == "stop":
            ipc.put(protocol.MUSIC_INBOX, protocol.music_stop())
            _respond_and_say('STOP')
            return None

        if action == "pause":
            ipc.put(protocol.MUSIC_INBOX, protocol.music_pause())
            _respond_and_say('PAUSE')
            return None

        if action == "resume":
            ipc.put(protocol.MUSIC_INBOX, protocol.music_resume())
            _respond_and_say('RESUME')
            return None

        if action == "next":
            self._handle_next()
            return None

        if action == "prev":
            ipc.put(protocol.MUSIC_INBOX, protocol.music_prev())
            if random.random() < VOICE_RANDOM_CHANCE:
                _respond_and_say(self.PHRASE_MAP[action])
            return None

        if action == "volume_up":
            ipc.put(protocol.MUSIC_INBOX, protocol.music_volume_up())
            if random.random() < VOICE_RANDOM_CHANCE:
                _respond_and_say(self.PHRASE_MAP[action])
            return None

        if action == "volume_down":
            ipc.put(protocol.MUSIC_INBOX, protocol.music_volume_down())
            if random.random() < VOICE_RANDOM_CHANCE:
                _respond_and_say(self.PHRASE_MAP[action])
            return None

        return None

    def _handle_next(self):
        """Next с логикой спама: 5-е за 30 сек — принудительно NEXT_OFTEN."""
        try:
            from brain import reaction
            from brain.appraisal.events import Event, KIND_SKIP
            from shared import state
            cur = state.get_current() or {}
            reaction.publish(Event(
                kind=KIND_SKIP,
                artist=cur.get("artist", ""),
                track=cur.get("title", ""),
                source="controls",
            ))
        except Exception as e:
            log(f"controls: appraisal publish ошибка: {e}", tag="BRAIN", level="WARN")

        ipc.put(protocol.MUSIC_INBOX, protocol.music_next())

        now = time.time()
        if now - self._next_window_start > cfg.NEXT_SPAM_WINDOW_SEC:
            self._next_count = 0
            self._next_window_start = now

        self._next_count += 1
        log(f"controls: next count={self._next_count}/{cfg.NEXT_SPAM_THRESHOLD}", tag="BRAIN")

        if self._next_count >= cfg.NEXT_SPAM_THRESHOLD:
            self._next_count = 0
            self._next_window_start = now
            _respond_and_say('NEXT_OFTEN')
            return

        if random.random() < VOICE_RANDOM_CHANCE:
            _respond_and_say('NEXT_TRACK')


register(ControlsSkill())