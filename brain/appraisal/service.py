# brain/appraisal/service.py
# Оркестратор appraisal: event → history → policy → фраза.
#
# Знает про History, Policy, PhrasePicker (через _respond_and_say).
# Не знает про очередь и потоки — это reaction.py.
# Передаёт artist в фразу, чтобы {artist} подставился.
#
# I (relationship): после реакции понижаем вес артиста (penalize_artist),
# чтобы «запомнил» стало правдой.

from brain.appraisal.events import Event
from brain.appraisal.history import History
from brain.appraisal.policy import Policy
from shared.log import log


class AppraisalService:
    def __init__(self, history: History | None = None, policy: Policy | None = None):
        self._history = history or History()
        self._policy = policy or Policy(self._history)

    def handle(self, event: Event) -> None:
        """Обработать событие. Если policy решил — сказать фразу и сбросить."""
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

        # I: понизить вес артиста (запомнил)
        if decision.artist:
            try:
                from memory import memory
                memory.penalize_artist(decision.artist, delta=-5.0)
                log(f"appraisal: penalize_artist({decision.artist}, -5.0)", tag="BRAIN")
            except Exception as e:
                log(f"appraisal: penalize_artist: {e}", tag="BRAIN", level="WARN")