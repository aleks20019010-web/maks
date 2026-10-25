"""
shared/protocol.py — протокол общения между brain и workers.

Формат: JSON.
Транспорт: файловая шина (см. shared/ipc.py).

Топики:
    brain_inbox     — команды и события → brain
    tts_inbox       — что сказать → tts
    music_inbox     — команды управления музыкой → music
    asr_control     — управление ASR (старт/стоп, режим)
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
    volume_up, volume_down, volume_set, hold_duck, release_duck,
    duck_on, duck_off, dislike, now_playing_request."""
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


def music_hold_duck() -> Message:
    """Держать паузу (brain ищет трек — музыка не должна играть)."""
    return music_control("hold_duck")


def music_release_duck() -> Message:
    """Отпустить паузу (brain нашёл — музыка может играть)."""
    return music_control("release_duck")


def music_duck_on(level: float = 0.2) -> Message:
    """Приглушить музыку до level (0.0–1.0). Не пауза — volume down."""
    return music_control("duck_on", level=level)


def music_duck_off() -> Message:
    """Вернуть громкость после duck."""
    return music_control("duck_off")


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