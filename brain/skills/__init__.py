# brain/skills/__init__.py
# Навыки brain.
#
# Регистрация происходит при импорте skill.py (см. base.register).
#
# ── APPS (2026-11-19) ───────────────────────────────────────────
#   Импорт brain.skills.apps нужен для авторегистрации:
#   модуль apps/skill.py в конце вызывает register(AppsSkill()).
#   Без этого import скил "apps" не попадёт в реестр, и skill_resolver
#   для APP_OPEN/APP_CLOSE уйдёт в fallback find_handler.

from brain.skills import apps