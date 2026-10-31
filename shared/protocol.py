"""
shared/protocol.py — протокол общения между brain и workers.

Формат: JSON.
Транспорт: файловая шина (см. shared/ipc.py).

Топики:
    brain_inbox     — команды и события → brain
    tts_inbox       — что сказать → tts
    music_inbox     — команды управления музыкой → music
    asr_control     — управление ASR (старт/стоп, режим)

FIX (день 29, вечер): удалены мёртвые обёртки music_hold_duck(),
music_release_duck(), music_duck_on(), music_duck_off().
  - hold_duck/release_duck — внутренние команды modes.py (загрузка
    данных), из brain их не шлём.
  - duck_on/duck_off теперь шлёт ASR при mic_off/mic_on. TTS worker
    про музыку не знает. Обёртки в protocol.py больше не нужны.
  - music_control("hold_duck"/"release_duck"/"duck_on"/"duck_off")
    остаются как валидные control-имена (обработка в music/main.py).
"""

import json
from dataclasses import dataclass, asdict, field


# Топики
BRAIN_INBOX = "brain_inbox"
TTS_INBOX = "tts_inbox"
MUSIC_INBOX = "music_inbox"
ASR_CONTROL = "asr_control"


@dataclass
class Message:
    """Сообщение между процессами."""
    type: str            # "command" | "event" | "say" | "play" | "control"
    name: str = ""       # intent | event_name | control_name
    text: str = ""       # для say/command
    data: dict = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @staticmethod
    def from_json(raw: str) -> "Message":
        d = json.loads(raw)
        return Message(**d)


# ── Общие конструкторы ────────────────────────────────────────

def command(text: str, source: str = "voice", **kw) -> Message:
    """Команда от пользователя (голос / клавиатура)."""
    return Message(type="command", text=text, data={"source": source, **kw})


def event(name: str, **kw) -> Message:
    """Событие (wake, track_started, track_finished, ...)."""
    return Message(type="event", name=name, data=kw)


def say(text: str, sync: bool = True, **kw) -> Message:
    """Сказать фразу через TTS."""
    return Message(type="say", text=text, data={"sync": sync, **kw})


# ── Music: play-команды (brain → music) ───────────────────────

def play_artist(artist: str, **kw) -> Message:
    """Включить артиста."""
    return Message(type="play", name="artist", data={"artist": artist, **kw})


def play_track(track: str, **kw) -> Message:
    """Включить трек."""
    return Message(type="play", name="track", data={"track": track, **kw})


def play_genre(genre: str, station: str = "", **kw) -> Message:
    """Включить жанр. station — id станции ('genre:rusrock'), если известен."""
    return Message(type="play", name="genre", data={"genre": genre, "station": station, **kw})


def play_mood(mood_tag: str, **kw) -> Message:
    """Включить настроение. mood_tag — tag метатега («Энергичное»).

    Music worker: client.metatag(mood_tag) → station_id (mood:energetic) →
    запуск станции Яндекса (см. workers/music/main.py::_handle_play, name="mood").
    """
    return Message(type="play", name="mood", data={"mood": mood_tag, **kw})


def play_wave(language: str | None = None, **kw) -> Message:
    """Включить «Мою волну». language='russian' — русская волна."""
    return Message(type="play", name="wave", data={"language": language, **kw})


def play_liked(**kw) -> Message:
    """Включить «Мне нравится»."""
    return Message(type="play", name="liked", data=kw)


def play_radio(station: str, **kw) -> Message:
    """Включить радиостанцию по ключу."""
    return Message(type="play", name="radio", data={"station": station, **kw})


# ── Music: управление (brain → music) ─────────────────────────

def music_control(name: str, **kw) -> Message:
    """Управление музыкой: next, prev, pause, resume, stop,
    volume_up, volume_down, volume_set,
    duck_on, duck_off, dislike, now_playing_request.

    hold_duck/release_duck — внутренние команды modes.py (загрузка
    данных), из brain их не шлём. duck_on/duck_off шлёт ASR при
    mic_off/mic_on (привязка ducking к микрофону).
    """
    return Message(type="control", name=name, data=kw)


def music_next() -> Message:
    return music_control("next")


def music_prev() -> Message:
    return music_control("prev")


def music_pause() -> Message:
    return music_control("pause")


def music_resume() -> Message:
    return music_control("resume")


def music_stop() -> Message:
    return music_control("stop")


def music_volume_up() -> Message:
    return music_control("volume_up")


def music_volume_down() -> Message:
    return music_control("volume_down")


def music_volume_set(value: int) -> Message:
    return music_control("volume_set", value=value)


def music_dislike(**kw) -> Message:
    """Blacklist текущего трека (не предлагать больше в автовыборе)."""
    return music_control("dislike", **kw)


def music_now_playing_request() -> Message:
    """Запрос: что играет? Music ответит event: now_playing."""
    return music_control("now_playing_request")


# ── Music: события (music → brain) ────────────────────────────

def event_now_playing(artist: str, title: str, **kw) -> Message:
    """Music сообщает: сейчас играет X — Y."""
    return Message(type="event", name="now_playing",
                   data={"artist": artist, "title": title, **kw})


def event_music_started(artist: str = "", title: str = "", **kw) -> Message:
    """Music сообщает: трек начал играть. artist/title — что именно."""
    return Message(type="event", name="music_started",
                   data={"artist": artist, "title": title, **kw})


def event_music_stopped(**kw) -> Message:
    return Message(type="event", name="music_stopped", data=kw)


def event_track_finished(artist: str = "", title: str = "", **kw) -> Message:
    return Message(type="event", name="track_finished",
                   data={"artist": artist, "title": title, **kw})


# ── ASR ───────────────────────────────────────────────────────

def asr_control(name: str, **kw) -> Message:
    """Управление ASR: wake_mode, command_mode, pause, resume."""
    return Message(type="control", name=name, data=kw)