# tests/test_music_classify.py
def test_classify_play_artist():
    from brain.skills.music.classify import classify
    r = classify("включи арию")
    assert r is not None
    assert r["action"] == "play_artist"

def test_classify_play_wave():
    from brain.skills.music.classify import classify
    r = classify("включи музыку")
    assert r is not None
    assert r["action"] == "play_wave"

def test_classify_none_for_stop_word():
    from brain.skills.music.classify import classify
    assert classify("стоп") is None
    assert classify("") is None