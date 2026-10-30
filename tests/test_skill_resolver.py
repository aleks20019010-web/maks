# Контракт: MUSIC_PLAY → music, MUSIC_STOP → controls, UNKNOWN → None.
def test_music_play_resolves_to_music():
    from brain.nlu.skill_resolver import skill_name_for_intent
    from brain.nlu.types import Intent
    assert skill_name_for_intent(Intent.MUSIC_PLAY) == "music"

def test_unknown_returns_none():
    from brain.nlu.skill_resolver import skill_name_for_intent
    from brain.nlu.types import Intent
    assert skill_name_for_intent(Intent.UNKNOWN) is None

def test_resolve_falls_back_to_find_handler(monkeypatch):
    # Если скил по intent не зарегистрирован — resolve пробует find_handler.
    from brain.nlu import skill_resolver
    from brain.nlu.types import Command, Intent
    from brain.skills.base import Skill
    class FakeSkill(Skill):
        name = "fake"
        def can_handle(self, text): return True
    monkeypatch.setattr(skill_resolver.skills_base, "find_by_name", lambda n: None)
    monkeypatch.setattr(skill_resolver.skills_base, "find_handler", lambda t: FakeSkill())
    cmd = Command(intent=Intent.MUSIC_PLAY, entity=None, skill_name=None,
                  action=None, confidence=0.9, normalized_text="x", original_text="x")
    assert skill_resolver.resolve(cmd).name == "fake"