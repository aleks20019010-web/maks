"""
personality_core/policy.py — PolicyEngine.

Читает правила из personality_core/rules/*.json.
Матчит условия против Context.
Выбирает лучшее правило по приоритету.

Правила — ДАННЫЕ, не код.
Правило НЕ содержит текст фразы — только phrase_set.

Wildcard: intent "*" матчится на любой intent.

Расширение: field "payload.X" читается из payload (kw из respond()).
"""

import os
import json
from typing import Optional, List

from personality_core.types import Context
from personality_core import state as pstate


class PolicyResult:
    """Результат работы PolicyEngine."""

    def __init__(self, rule_name: str = "", phrase_set: str = "",
                 priority: int = 0, payload: dict = None,
                 cooldown_sec: float = 0.0):
        self.rule_name = rule_name
        self.phrase_set = phrase_set
        self.priority = priority
        self.payload = payload or {}
        self.cooldown_sec = cooldown_sec


class PolicyEngine:
    def __init__(self, rules_dir: str):
        self.rules_dir = rules_dir
        self._rules: List[dict] = []
        self.load_rules()

    # --------------------------------------------------------
    # ЗАГРУЗКА
    # --------------------------------------------------------
    def load_rules(self):
        """Загрузить все *.json из rules_dir."""
        self._rules = []
        if not os.path.isdir(self.rules_dir):
            return
        for fname in sorted(os.listdir(self.rules_dir)):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(self.rules_dir, fname)
            try:
                with open(path, "r", encoding="utf-8-sig") as f:
                    data = json.load(f)
            except Exception as e:
                print(f"⚠ policy: ошибка чтения {fname}: {e}")
                continue
            if isinstance(data, list):
                self._rules.extend(data)
            elif isinstance(data, dict) and "rules" in data:
                self._rules.extend(data["rules"])

    def reload(self):
        """Горячая перезагрузка правил."""
        self.load_rules()
        print(f"♻ policy: загружено {len(self._rules)} правил")

    # --------------------------------------------------------
    # ВЫБОР ПРАВИЛА
    # --------------------------------------------------------
    def evaluate(self, ctx: Context) -> Optional[PolicyResult]:
        """Прогнать контекст по правилам, вернуть лучшее."""
        payload = getattr(ctx, "_payload", {}) or {}
        matched = []
        for rule in self._rules:
            if not self._rule_matches(rule, ctx, payload):
                continue
            name = rule.get("name", "")
            cd = float(rule.get("cooldown_sec", 0.0))
            if cd > 0 and pstate.get_rule_cooldown(name, cd):
                continue
            matched.append(rule)

        if not matched:
            return None

        matched.sort(key=lambda r: int(r.get("priority", 0)), reverse=True)
        best = matched[0]

        return PolicyResult(
            rule_name=best.get("name", ""),
            phrase_set=best.get("phrase_set", ""),
            priority=int(best.get("priority", 0)),
            payload=best.get("payload", {}) or {},
            cooldown_sec=float(best.get("cooldown_sec", 0.0)),
        )

    # --------------------------------------------------------
    # УСЛОВИЯ
    # --------------------------------------------------------
    def _rule_matches(self, rule: dict, ctx: Context, payload: dict = None) -> bool:
        if payload is None:
            payload = {}

        # Wildcard: "*" — правило матчится на любой intent
        rule_intent = rule.get("intent")
        if rule_intent and rule_intent != "*" and rule_intent != ctx.query_intent:
            return False

        for cond in rule.get("require", []):
            if not self._check_condition(cond, ctx, payload):
                return False

        for cond in rule.get("unless", []):
            if self._check_condition(cond, ctx, payload):
                return False

        return True

    def _check_condition(self, cond: dict, ctx: Context, payload: dict = None) -> bool:
        """Проверка одного условия.

        Формат:
            {"field": "time_of_day", "op": "==", "value": "night"}
            {"field": "hour", "op": ">=", "value": 23}
            {"field": "plays_last_day.{artist}", "op": ">=", "value": 3}
            {"field": "streak_artist({artist})", "op": ">=", "value": 3}
            {"field": "payload.temp_bucket", "op": "==", "value": "cold"}
        """
        if payload is None:
            payload = {}
        if not isinstance(cond, dict):
            return True

        field = cond.get("field", "")
        op = cond.get("op", "==")
        value = cond.get("value")

        # КЛЮЧЕВОЕ: подставляем плейсхолдеры прямо в путь
        field_sub = self._substitute(field, ctx, payload)
        actual = self._get_field(ctx, field_sub, payload)

        try:
            if op == "==":
                return actual == value
            if op == "!=":
                return actual != value
            if op == ">=":
                return actual >= value
            if op == "<=":
                return actual <= value
            if op == ">":
                return actual > value
            if op == "<":
                return actual < value
            if op == "in":
                return actual in value
            if op == "contains":
                return value in actual
        except Exception:
            return False
        return False

    # --------------------------------------------------------
    # ДОСТУП К ПОЛЯМ
    # --------------------------------------------------------
    def _get_field(self, ctx: Context, path: str, payload: dict = None):
        """Достать значение по пути.

        Поддерживает:
          - атрибуты: 'time_of_day', 'hour'
          - словари: 'plays_last_day.Король и Шут'
          - методы без аргументов: 'plays_last_day', 'streak_artist'
          - методы с аргументом: 'streak_artist({artist})'
          - payload.X — читаем из payload, не из ctx
        """
        if payload is None:
            payload = {}
        parts = path.split(".")

        # Особый случай: "payload.X" — читаем из payload, не из ctx
        if parts[0] == "payload":
            obj = payload
            parts = parts[1:]
        else:
            obj = ctx

        for p in parts:
            if obj is None:
                return None

            p_sub = self._substitute(p, ctx, payload)

            call = p_sub
            arg = None
            if "(" in p_sub and p_sub.endswith(")"):
                name = p_sub[:p_sub.index("(")]
                arg_str = p_sub[p_sub.index("(") + 1:-1]
                arg = self._substitute(arg_str, ctx, payload)
                call = name

            # dict — берём значение по ключу
            if isinstance(obj, dict):
                obj = obj.get(call)
                continue

            # атрибут / метод
            attr = getattr(obj, call, None)
            if attr is None:
                return None

            if callable(attr):
                if arg is not None:
                    obj = attr(arg)
                else:
                    obj = attr()
            else:
                obj = attr

        return obj

    def _substitute(self, s: str, ctx: Context, payload: dict = None):
        """Подставить {artist}, {query} и т.п.

        Приоритет:
          1. payload (kw из respond("PLAY_ARTIST", artist="..."))
          2. ctx.current_artist и т.п.
        """
        if payload is None:
            payload = {}
        if "{" not in s:
            return s.strip().strip('"\'')
        # Сначала payload
        for k, v in payload.items():
            s = s.replace("{" + k + "}", str(v) if v is not None else "")
        # Затем встроенные
        s = s.replace("{artist}", ctx.current_artist or "")
        s = s.replace("{query}", ctx.query or "")
        s = s.replace("{intent}", ctx.query_intent or "")
        return s.strip().strip('"\'')