# brain/skills/jellyfin/skill.py
# Голосовой скил Jellyfin. Использует jellyfin_api.py из D:\Scripts.
#
# SIGNATURE = "— Макс"
# «Сэр» убран везде.

import os
import sys

from brain.skills.base import Skill, register
from brain.skills._helpers import respond_and_say as _respond_and_say
from shared.log import log

# Добавить D:\Scripts в sys.path, чтобы импортировать jellyfin_api
_SCRIPTS_DIR = r"D:\Scripts"
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)


def _jf():
    """Ленивый импорт jellyfin_api (может быть не готов на старте)."""
    try:
        import jellyfin_api as jf
        return jf
    except Exception as e:
        log(f"jellyfin: не удалось импортировать jellyfin_api: {e}", tag="BRAIN", level="WARN")
        return None


class JellyfinSkill(Skill):
    name = "jellyfin"
    priority = 3  # выше system (5)

    def can_handle(self, text: str) -> bool:
        tl = text.lower().strip()
        # Триггеры
        triggers = (
            "jellyfin", "желлифин", "джеллифин",
            "сервер", "торрсервер", "торрентсервер",
            "фильм", "сериал", "кино",
            "младшие", "старшие", "малыши",
            "что играет в", "что смотрят",
            "статус сервера", "статус jellyfin",
        )
        return any(t in tl for t in triggers)

    def handle(self, text: str) -> bool:
        jf = _jf()
        if jf is None:
            _respond_and_say("JELLYFIN_API_ERROR")
            return True

        tl = text.lower().strip()

        # Статус сервера
        if any(t in tl for t in ("статус сервера", "статус jellyfin", "как jellyfin", "что с сервером", "статус торрсервер", "статус торрентсервер")):
            try:
                result = jf.cmd_status()
                _respond_and_say("JELLYFIN_STATUS", text=result)
            except Exception as e:
                log(f"jellyfin: cmd_status: {e}", tag="BRAIN", level="WARN")
                _respond_and_say("JELLYFIN_API_ERROR")
            return True

        # Что играет
        if "что играет в" in tl or "что смотрят" in tl:
            try:
                lines = jf.cmd_now_playing()
                if lines:
                    _respond_and_say("JELLYFIN_NOW_PLAYING", text="\n".join(lines))
                else:
                    _respond_and_say("JELLYFIN_NO_SESSIONS")
            except Exception as e:
                log(f"jellyfin: cmd_now_playing: {e}", tag="BRAIN", level="WARN")
                _respond_and_say("JELLYFIN_API_ERROR")
            return True

        # Что у младших / старших
        if "что у младших" in tl or "что смотрят младшие" in tl:
            try:
                result = jf.cmd_whats_playing_for("младшие")
                _respond_and_say("JELLYFIN_WHATS_PLAYING", text=result)
            except Exception as e:
                log(f"jellyfin: cmd_whats_playing_for: {e}", tag="BRAIN", level="WARN")
                _respond_and_say("JELLYFIN_API_ERROR")
            return True
        if "что у старших" in tl or "что смотрят старшие" in tl:
            try:
                result = jf.cmd_whats_playing_for("старшие")
                _respond_and_say("JELLYFIN_WHATS_PLAYING", text=result)
            except Exception as e:
                log(f"jellyfin: cmd_whats_playing_for: {e}", tag="BRAIN", level="WARN")
                _respond_and_say("JELLYFIN_API_ERROR")
            return True

        # Пауза
        if tl.startswith("пауза jellyfin") or tl.startswith("пауза желлифин"):
            try:
                result = jf.cmd_pause("алексей")
                _respond_and_say("JELLYFIN_PAUSE", text=result)
            except Exception as e:
                log(f"jellyfin: cmd_pause: {e}", tag="BRAIN", level="WARN")
                _respond_and_say("JELLYFIN_API_ERROR")
            return True

        # Продолжить
        if tl.startswith("продолжи jellyfin") or tl.startswith("продолжи желлифин"):
            try:
                result = jf.cmd_resume("алексей")
                _respond_and_say("JELLYFIN_RESUME", text=result)
            except Exception as e:
                log(f"jellyfin: cmd_resume: {e}", tag="BRAIN", level="WARN")
                _respond_and_say("JELLYFIN_API_ERROR")
            return True

        # Стоп
        if tl.startswith("стоп jellyfin") or tl.startswith("стоп желлифин"):
            try:
                result = jf.cmd_stop("алексей")
                _respond_and_say("JELLYFIN_STOP", text=result)
            except Exception as e:
                log(f"jellyfin: cmd_stop: {e}", tag="BRAIN", level="WARN")
                _respond_and_say("JELLYFIN_API_ERROR")
            return True

        # Найди X в jellyfin
        if "найди" in tl and ("jellyfin" in tl or "желлифин" in tl):
            for c in ("найди", "в jellyfin", "в желлифин"):
                tl = tl.replace(c, "").strip()
            if tl:
                try:
                    result = jf.cmd_find(tl)
                    _respond_and_say("JELLYFIN_FIND", text=result)
                except Exception as e:
                    log(f"jellyfin: cmd_find: {e}", tag="BRAIN", level="WARN")
                    _respond_and_say("JELLYFIN_API_ERROR")
                return True

        # Включи фильм X
        if ("включи фильм" in tl or "включи сериал" in tl or "включи кино" in tl):
            title = tl
            for c in ("включи фильм", "включи сериал", "включи кино", "в jellyfin", "в желлифин"):
                title = title.replace(c, "").strip()
            if title:
                try:
                    result = jf.cmd_play("алексей", title)
                    _respond_and_say("JELLYFIN_PLAY", text=result)
                except Exception as e:
                    log(f"jellyfin: cmd_play: {e}", tag="BRAIN", level="WARN")
                    _respond_and_say("JELLYFIN_API_ERROR")
                return True

        return False


register(JellyfinSkill())