# brain/skills/apps/skill.py
# AppsSkill: запуск и закрытие программ через NLU (APP_OPEN, APP_CLOSE).
# Скил получает готовый Command с извлечённым Entity (app_key из config/apps.json).

from brain.nlu.entity.apps import load_apps_catalog
from brain.nlu.types import Command, EntityState, Intent
from brain.skills import base as skills_base
from brain.skills._helpers import respond_and_say
from brain.skills.apps import launcher
from shared.log import log


class AppsSkill(skills_base.Skill):
    """Скил управления программами. NLU-путь, can_handle → False."""

    name = "apps"
    priority = 35
    accepts_command = True

    def can_handle(self, text: str) -> bool:
        return False

    def handle(self, command) -> None:
        if not isinstance(command, Command):
            log(f"apps: handle получил {type(command).__name__}, ожидался Command",
                tag="BRAIN", level="WARN")
            return

        if command.action == "app_unknown":
            raw_name = (command.entity.value if command.entity else "") or ""
            respond_and_say("APP_UNKNOWN", app=raw_name)
            return

        ent = command.entity
        if ent is None or ent.state != EntityState.FOUND or not ent.value:
            respond_and_say("APP_UNKNOWN", app="")
            return

        app_key = ent.value
        catalog = load_apps_catalog()
        info = catalog.get(app_key)
        if not info:
            respond_and_say("APP_UNKNOWN", app=app_key)
            return

        display = info.get("display_name", app_key)

        if command.intent == Intent.APP_OPEN:
            log(f"apps: открываю {display} ({app_key})", tag="BRAIN")
            if launcher.launch_app(info):
                respond_and_say("APP_OPENED", app=display)
            else:
                respond_and_say("APP_OPEN_FAIL", app=display)
            return

        if command.intent == Intent.APP_CLOSE:
            log(f"apps: закрываю {display} ({app_key})", tag="BRAIN")
            status = launcher.close_app(info)
            if status == "closed":
                respond_and_say("APP_CLOSED", app=display)
            elif status == "not_running":
                respond_and_say("APP_NOT_RUNNING", app=display)
            else:
                respond_and_say("APP_CLOSE_FAIL", app=display)
            return

        log(f"apps: неизвестный intent {command.intent}", tag="BRAIN", level="WARN")


skills_base.register(AppsSkill())