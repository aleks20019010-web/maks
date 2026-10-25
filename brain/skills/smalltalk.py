# brain/skills/smalltalk.py
# Привет, спасибо, ты кто, что умеешь, как дела.
# Все фразы через Personality (respond_and_say → IPC → TTS).
#
# Утром (5–12), первый раз за день, при «привет»:
#   - Спросить «Доброе утро, сэр. Не желаете послушать музыку?»
#   - Поставить pending("music_offer", timeout=10, wait_tts=True).
#   - Ответ обрабатывает router.py.
# Если уже спрашивали сегодня — просто HELLO.

import time

from brain.skills.base import Skill, register
from brain.skills._helpers import respond_and_say as _respond_and_say
from brain import pending
from brain import relationship
from shared.log import log


PENDING_KEY = "music_offer"


class SmallTalkSkill(Skill):
    name = "smalltalk"
    priority = 30

    def can_handle(self, text: str) -> bool:
        t = text.lower().strip()
        return t in {
            "привет", "здравствуй", "здравствуйте",
            "спасибо", "благодарю",
            "ты кто", "кто ты",
            "что ты умеешь", "что умеешь",
            "как дела",
            "помолчи", "замолчи", "тихо",
            "говори", "вернись", "не молчи",
        }

    def handle(self, text: str) -> str | None:
        t = text.lower().strip()

        if t in ("привет", "здравствуй", "здравствуйте"):
            self._handle_hello()
        elif t in ("спасибо", "благодарю"):
            _respond_and_say('THANKS')
        elif t in ("ты кто", "кто ты"):
            _respond_and_say('WHO_ARE_YOU')
        elif t in ("что ты умеешь", "что умеешь"):
            _respond_and_say('WHAT_CAN_YOU_DO')
        elif t == "как дела":
            _respond_and_say('HOW_ARE_YOU')
        elif t in ("помолчи", "замолчи", "тихо"):
            _respond_and_say('SILENCE')
        elif t in ("говори", "вернись", "не молчи"):
            _respond_and_say('SILENCE_END')
        return None

    # ────────────────────────────────────────────────────────
    # ПРИВЕТ + ПРЕДЛОЖЕНИЕ МУЗЫКИ (утром, первый раз за день)
    # ────────────────────────────────────────────────────────
    def _handle_hello(self):
        hour = time.localtime().tm_hour
        is_morning = 5 <= hour < 12

        if not is_morning:
            _respond_and_say('HELLO')
            return

        if relationship.was_asked_today("music_offer"):
            _respond_and_say('HELLO')
            return

        log("smalltalk: утро, предлагаю музыку", tag="BRAIN")

        def _on_timeout(_data):
            log("smalltalk: music_offer timeout — молчание", tag="BRAIN")

        pending.set(
            PENDING_KEY,
            timeout=10.0,
            on_timeout=_on_timeout,
            data={"kind": "music_offer"},
            wait_tts=True,
        )
        relationship.mark_asked_today("music_offer")
        _respond_and_say('SMALLTALK_MUSIC_OFFER')


register(SmallTalkSkill())