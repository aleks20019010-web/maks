# brain/nlu/entity/base.py
# Общий интерфейс извлечения и поиска сущностей.
#
# Два метода — два разных вопроса:
#   extract_candidate(text, intent) -> str | None
#       «Что пользователь назвал?» — правила, вычитание триггеров.
#       None только при РЕАЛЬНОМ ABSENT («вруби чего-нибудь»).
#       «включи Орион» → "орион" (не None!) — потому что пользователь
#       назвал параметр, просто в каталоге его может не быть.
#
#   match(candidate, intent) -> Entity
#       «Есть ли это в каталоге?» — fuzzy (rapidfuzz, если есть) + pymorphy3
#       (если есть). Возвращает Entity с state FOUND / UNRESOLVED / AMBIGUOUS.
#
# ПОЧЕМУ ДВА МЕТОДА, А НЕ ОДИН:
#   «вруби чего-нибудь» и «включи Орион» — семантически разные.
#   В первом пользователь НЕ назвал параметр → ABSENT → play_wave.
#   Во втором назвал, но в каталоге нет → UNRESOLVED → CLARIFY.
#   Если смешать в один метод — теряем это различие и включаем волну
#   вместо «не нашёл Орион, может, Кипелов?».
#
# РЕАЛИЗАЦИЯ:
#   EntityExtractor — протокол (duck typing). Наследники — MusicEntityExtractor,
#   WeatherEntityExtractor (потом), ReminderEntityExtractor (потом).
#   Не ABC — не хотим заставлять наследников регистрироваться.
#   isinstance(x, EntityExtractor) не работает, но pipeline и не проверяет —
#   он зовёт методы, и всё.

from typing import Optional, Protocol

from brain.nlu.types import Entity, Intent


class EntityExtractor(Protocol):
    """Протокол экстрактора сущностей.

    Наследники переопределяют поддерживаемые intent'ы и методы.
    """

    #: Intent'ы, которые этот экстрактор умеет обрабатывать.
    #: Например, MUSIC_PLAY / MUSIC_WAVE для MusicEntityExtractor.
    supported_intents: tuple[Intent, ...]

    def supports(self, intent: Intent) -> bool:
        """Умеет ли экстрактор обрабатывать этот intent."""
        ...

    def extract_candidate(self, text: str, intent: Intent) -> Optional[str]:
        """Извлечь кандидата из текста (правила, вычитание триггеров).

        Возвращает:
          - строку-кандидата («орион», «ария», «рок») — если пользователь
            что-то назвал. Даже если этого нет в каталоге.
          - None — если пользователь НЕ назвал параметр («вруби чего-нибудь»,
            «включи музыку» без уточнения).

        Не бросает. При внутренней ошибке — None (как ABSENT).
        """
        ...

    def match(self, candidate: str, intent: Intent) -> Entity:
        """Найти candidate в каталоге.

        Возвращает Entity:
          - FOUND — нашёл однозначно
          - UNRESOLVED — не нашёл
          - AMBIGUOUS — 2+ кандидата с близким confidence

        Не бросает. При ошибке — Entity.unresolved(candidate).
        """
        ...


def supports(extractor: EntityExtractor, intent: Intent) -> bool:
    """Helper: поддерживает ли экстрактор intent.

    Дублирует extractor.supports() для случаев, когда экстрактор — не наш
    класс, а duck-typed объект без метода supports.
    """
    if hasattr(extractor, "supports"):
        try:
            return bool(extractor.supports(intent))
        except Exception:
            return False
    supported = getattr(extractor, "supported_intents", ())
    return intent in supported