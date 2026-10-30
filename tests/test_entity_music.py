# Контракт: extract_candidate возвращает None ТОЛЬКО при ABSENT.
def test_extract_absent_for_vklyuchi_muzyku():
    from brain.nlu.entity.music import get
    from brain.nlu.types import Intent
    assert get().extract_candidate("включи музыку", Intent.MUSIC_PLAY) is None

def test_extract_candidate_for_orion():
    # «включи Орион» → "орион" (не None! пользователь назвал параметр)
    from brain.nlu.entity.music import get
    from brain.nlu.types import Intent
    assert get().extract_candidate("включи орион", Intent.MUSIC_PLAY) == "орион"

def test_match_unresolved_for_unknown_artist():
    # "орион" нет в aliases.json → UNRESOLVED (не ABSENT, не FOUND)
    from brain.nlu.entity.music import get
    from brain.nlu.types import EntityState, Intent
    entity = get().match("орион", Intent.MUSIC_PLAY)
    assert entity.state == EntityState.UNRESOLVED
    assert entity.value == "орион"

def test_match_found_for_ariya():
    from brain.nlu.entity.music import get
    from brain.nlu.types import EntityState, Intent
    entity = get().match("ария", Intent.MUSIC_PLAY)
    assert entity.state == EntityState.FOUND
    assert entity.value == "Ария"
    assert entity.type == "artist"