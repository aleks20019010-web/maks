"""
personality_core/engine.py — PersonalityEngine, оркестратор.

Поток:
    Event → ContextBuilder → PolicyEngine → Composer → JarvisResponse
    (fallback: → ResponseBuilder → PhrasePicker)

Engine сам ничего не решает — он склеивает слои.
Плюс: обновляет self_state (mood, energy, valence, arousal).
Плюс: обновляет mind_stream (контекст сессии).
Плюс: отмечает сработавшие правила для cooldown.
Плюс: process_llm_answer — сборка ответа LLM через composer (opening + core + closing)
      + prosody на финальный текст (паузы + ударения).
Плюс: prosody для обычных фраз — тон по mood/energy + style.

СОВМЕСТИМОСТЬ:
    pick(event)       → str
    pick_full(event)  → dict
    respond(...)      → JarvisResponse
    process_llm_answer(text, explicit_detail) → JarvisResponse
"""

import os
from typing import Optional

from personality_core.types import Event, Context, JarvisResponse
from personality_core.context import ContextBuilder
from personality_core.policy import PolicyEngine
from personality_core.phrase import PhrasePicker
from personality_core.response import ResponseBuilder


class PersonalityEngine:
    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        rules_dir = os.path.join(base_dir, "personality_core", "rules")
        responses_dir = os.path.join(base_dir, "responses")

        from personality_core import character as _character
        self.character = _character.get(base_dir)

        self._context_builder = ContextBuilder()
        self._policy = PolicyEngine(rules_dir)
        self._phrase = PhrasePicker(
            responses_dir,
            temperature=self.character.temperature,
        )
        self._response_builder = ResponseBuilder(self._phrase)

        from brain.composer import Composer
        self._composer = Composer(base_dir, self._phrase)

    def respond(self, intent: str, **kw) -> JarvisResponse:
        event = Event(type="command", name=intent, data=kw)
        return self._process(event)

    def respond_event(self, name: str, **kw) -> JarvisResponse:
        event = Event(type="event", name=name, data=kw)
        return self._process(event)

    def respond_tick(self, **kw) -> JarvisResponse:
        event = Event(type="tick", name="tick", data=kw)
        return self._process(event)

    def process_llm_answer(
        self,
        text: str,
        explicit_detail: bool = False,
        context: Optional[Context] = None,
    ) -> JarvisResponse:
        """Собрать ответ LLM через composer + prosody.

        LLM даёт смысл (text). Composer оборачивает в личность Макса:
        OPENING_LLM + LLM_CORE({answer}) + CLOSING_LLM.
        Затем prosody применяется ко ВСЕМУ финальному тексту:
        запятые/точки/тире → sil<[...]>, ударения из stress_words.json.
        """
        from personality_core import self_state as _self_state
        from personality_core import prosody as _prosody
        from personality_core import style as _style

        st = _self_state.get()

        # 1. Определить style
        if context is not None:
            current_style = _style.pick_style(context)
        else:
            current_style = _style.pick_style_from_state(st)

        # 2. Выбрать правило composer по style
        if current_style == "night":
            rule_name = "llm_answer_night"
        elif current_style == "friendly":
            rule_name = "llm_answer_friendly"
        else:
            rule_name = "llm_answer"

        # 3. Payload: answer — текст LLM (сырой, prosody применим в конце)
        payload = {
            "answer": text,
            "explicit_detail": explicit_detail,
            "style": current_style,
        }

        # 4. Composer
        composed = None
        try:
            if context is not None:
                ctx = context
            else:
                from personality_core.types import Event
                event = Event(type="command", name="llm_answer", data=payload)
                ctx = self._context_builder.build(event)

            if not hasattr(ctx, "_payload") or ctx._payload is None:
                ctx._payload = {}
            ctx._payload.update(payload)

            from personality_core.policy import PolicyResult
            policy = PolicyResult(
                rule_name=rule_name,
                phrase_set=rule_name,
                payload=payload,
            )
            composed = self._composer.compose(policy, ctx)
        except Exception as e:
            print(f"⚠ engine: composer llm_answer: {e}")
            composed = None

        # 5. Prosody ко всему финальному тексту (composer + LLM)
        if composed is not None and composed.text:
            composed.text = _prosody.apply_prosody(composed.text)

            rpe = _prosody.get_rate_pitch_emotion(st, current_style)
            if composed.rate is None and rpe.get("rate"):
                composed.rate = rpe["rate"]
            if composed.pitch is None and rpe.get("pitch"):
                composed.pitch = rpe["pitch"]
            if composed.emotion is None and rpe.get("emotion"):
                composed.emotion = rpe["emotion"]
            composed.explain = f"llm_answer style={current_style} (composer+prosody)"
            return composed

        # Fallback: prosody поверх текста (как было)
        marked = _prosody.apply_prosody(text)
        rpe = _prosody.get_rate_pitch_emotion(st, current_style)
        return JarvisResponse(
            text=marked,
            rate=rpe.get("rate"),
            pitch=rpe.get("pitch"),
            emotion=rpe.get("emotion"),
            explain=f"llm_answer style={current_style} (fallback)",
        )

    def pick(self, event: str, **kw) -> Optional[str]:
        result = self.respond(event, **kw)
        return result.text

    def pick_full(self, event: str, **kw) -> Optional[dict]:
        phrase = self._phrase.pick(event, **kw)
        if phrase is None:
            return None
        return {
            "text": phrase.get("text", ""),
            "style": phrase.get("style", "normal"),
            "humor": float(phrase.get("humor", 0.0)),
            "night": bool(phrase.get("night", False)),
            "rate": phrase.get("rate"),
            "pitch": phrase.get("pitch"),
            "emotion": phrase.get("emotion"),
        }

    def _process(self, event: Event) -> JarvisResponse:
        st = None

        try:
            from personality_core import self_state as _self_state
            st = _self_state.get()
            if event.type == "command":
                st.on_command(intent=event.name, **event.data)
            elif event.type == "tick":
                st.on_tick(**event.data)
            print(f"   [SELF] {st.summary()}")
        except Exception as e:
            print(f"⚠ engine: self_state update: {e}")

        try:
            from personality_core import mind_stream as _mind
            ms = _mind.get()
            if event.type == "command":
                ms.on_command(intent=event.name, **event.data)
            elif event.type == "tick":
                mood = st.mood if st is not None else ""
                ms.on_tick(mood=mood)
            print(f"   [MIND] {ms.summary()}")
        except Exception as e:
            print(f"⚠ engine: mind_stream update: {e}")

        ctx = self._context_builder.build(event)
        ctx._payload = event.data or {}

        policy_result = self._policy.evaluate(ctx)

        if policy_result is not None and policy_result.rule_name:
            try:
                from personality_core import state as pstate
                pstate.mark_rule_fired(policy_result.rule_name)
            except Exception as e:
                print(f"⚠ engine: mark_rule_fired: {e}")

        # 4. Composer (сборка из слотов), fallback — ResponseBuilder
        response = self._composer.compose(policy_result, ctx)
        if response is None:
            response = self._response_builder.build(policy_result, ctx)

        # 4b. Prosody для обычных фраз — тон по mood/energy + style
        if response and response.text and st is not None:
            try:
                from personality_core import prosody as _prosody
                from personality_core import style as _style
                current_style = _style.pick_style(ctx)
                rpe = _prosody.get_rate_pitch_emotion(st, style=current_style)
                if response.rate is None and rpe.get("rate"):
                    response.rate = rpe["rate"]
                if response.pitch is None and rpe.get("pitch"):
                    response.pitch = rpe["pitch"]
                if response.emotion is None and rpe.get("emotion"):
                    response.emotion = rpe["emotion"]
            except Exception as e:
                print(f"⚠ engine: prosody для обычной фразы: {e}")

        try:
            from personality_core import self_state as _self_state
            st = _self_state.get()
            if response and response.text:
                st.on_response(response.text)
        except Exception:
            pass

        return response

    def reload(self):
        from personality_core import character as _character
        _character.reset(self.base_dir)
        self.character = _character.get(self.base_dir)

        self._phrase.temperature = self.character.temperature

        self._policy.reload()
        self._phrase.reload()
        self._composer.reload()