"""
personality_core/response.py — ResponseBuilder.

Собирает финальный JarvisResponse из:
    - PolicyResult  (что решили)
    - Context       (в каком контексте)
    - PhrasePicker  (что сказать)

Обрабатывает:
    - молчание (policy_result=None или phrase_set=None → text=None)
    - паузы before/after
    - actions (что выполнить после TTS)
    - explain (какое правило)
"""

from personality_core.policy import PolicyResult
from personality_core.phrase import PhrasePicker
from personality_core.types import Context, JarvisResponse


class ResponseBuilder:
    def __init__(self, phrase_picker: PhrasePicker):
        self.phrase_picker = phrase_picker

    def build(self, policy_result: PolicyResult,
              ctx: Context) -> JarvisResponse:
        # Нет правила — молчим
        if policy_result is None:
            return JarvisResponse(text=None, explain="no rule matched")

        # Правило с phrase_set=None → осознанное молчание
        if not policy_result.phrase_set:
            return JarvisResponse(
                text=None,
                explain=f"rule {policy_result.rule_name}: silent",
                actions=(policy_result.payload or {}).get("actions", []),
            )

        # Объединяем payload из правила и из запроса пользователя
        subs = {}
        if hasattr(ctx, "_payload"):
            subs.update(ctx._payload or {})
        if policy_result.payload:
            subs.update(policy_result.payload)

         # Динамические подстановки из identity
        try:
            subs.setdefault("identity_name", ctx.identity_name())
            subs.setdefault("identity_about", ctx.identity_about())
            subs.setdefault("identity_can", ctx.identity_can())
            subs.setdefault("identity_wont", ctx.identity_wont())
        except Exception:
            pass
        phrase = self.phrase_picker.pick(
            policy_result.phrase_set,
            **subs,
        )

        if phrase is None:
            return JarvisResponse(
                text=None,
                explain=f"rule {policy_result.rule_name}: no phrase for '{policy_result.phrase_set}'",
            )

        return JarvisResponse(
            text=phrase.get("text"),
            rate=phrase.get("rate"),
            pitch=phrase.get("pitch"),
            emotion=phrase.get("emotion"),
            pause_before=float(phrase.get("pause_before", 0.0)),
            pause_after=float(phrase.get("pause_after", 0.0)),
            actions=policy_result.payload.get("actions", []),
            explain=f"rule {policy_result.rule_name} → {policy_result.phrase_set}",
        )
