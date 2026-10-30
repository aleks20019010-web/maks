# tests/test_intent_music.py
# Контракт: музыкальные фразы НЕ должны классифицироваться как VOLUME_UP.
# Причина: «вруби что нибудь послушать» → VOLUME_UP → FALLBACK → LLM (медленно).

import pytest
from brain.nlu import intent
from brain.nlu.types import Intent


@pytest.mark.parametrize("phrase", [
    "вруби что нибудь послушать",
    "включи что-нибудь послушать",
    "хочу что-нибудь послушать",
    "поставь что-нибудь послушать",
    "вруби музыку",
])
def test_music_phrase_not_volume_up(phrase):
    """Музыкальные фразы → MUSIC_PLAY, а не VOLUME_UP."""
    result, conf = intent.resolve(phrase)
    assert result != Intent.VOLUME_UP, (
        f"«{phrase}» → {result.value} (conf={conf:.2f}), "
        f"ожидали MUSIC_PLAY, а не VOLUME_UP"
    )