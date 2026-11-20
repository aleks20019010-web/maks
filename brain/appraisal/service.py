# brain/appraisal/service.py
# Оркестратор appraisal: event → history → policy → фраза.

from brain.appraisal.events import Event, KIND_SKIP
from brain.appraisal.history import History
from brain.appraisal.policy import Policy
from shared.log import log


class AppraisalService:
    def __init__(self, history: History | None = None, policy: Policy | None = None):
        self._history = history or History()
        self._policy = policy or Policy(self._history)

    def handle(self, event: Event) -> None:
        """Обработать событие. Если policy решил — сказать фразу и сбросить."""
        if event.kind == KIND_SKIP:
            try:
                from personality_core import self_state as _self_state
                _self_state.get().on_event("music_skipped")
            except Exception:
                pass

        try:
            decision = self._policy.evaluate(event)
        except Exception as e:
            log(f"appraisal: policy ошибка: {e}", tag="BRAIN", level="WARN")
            return

        log(
            f"appraisal: {event.kind} key={decision.key} "
            f"reaction={decision.reaction} set={decision.phrase_set}",
            tag="BRAIN",
        )

        if not decision.reaction or not decision.phrase_set:
            return

        try:
            from brain.skills._helpers import respond_and_say
            kwargs = {}
            if decision.artist:
                kwargs["artist"] = decision.artist
            respond_and_say(decision.phrase_set, **kwargs)
        except Exception as e:
            log(f"appraisal: не смог сказать: {e}", tag="BRAIN", level="WARN")
            return

        self._history.reset(decision.key)
        log(f"appraisal: реакция сказана, reset key={decision.key}", tag="BRAIN")

        if decision.artist:
            try:
                from memory import memory
                memory.penalize_artist(decision.artist, delta=-5.0)
                log(f"appraisal: penalize_artist({decision.artist}, -5.0)", tag="BRAIN")
            except Exception as e:
                log(f"appraisal: penalize_artist: {e}", tag="BRAIN", level="WARN")