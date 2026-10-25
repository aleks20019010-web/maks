# workers/tts/player.py
# VLC-плеер для TTS + ducking музыки через IPC (pause/resume).

import os
import time

import vlc

from shared import ipc
from shared import protocol
from shared.log import log


TTS_VOLUME = 100


def play_tts_file(file_path: str) -> bool:
    """Проиграть mp3. Останавливает после окончания."""
    if not os.path.exists(file_path):
        return False
    try:
        p = vlc.MediaPlayer(file_path)
        try:
            p.audio_set_volume(TTS_VOLUME)
        except Exception:
            pass
        p.play()
        time.sleep(0.4)
        waited = 0.0
        while waited < 30:
            try:
                s = p.get_state()
                if s in (vlc.State.Ended, vlc.State.Stopped, vlc.State.Error):
                    break
            except Exception:
                break
            time.sleep(0.15)
            waited += 0.15
        try:
            p.stop()
        except Exception:
            pass
        return True
    except Exception as e:
        log(f"tts: play error: {e}", tag="TTS", level="ERROR")
        return False


def duck_music_on():
    """Сказать Music worker: пауза."""
    try:
        ipc.put(protocol.MUSIC_INBOX, protocol.music_control("duck_on"))
    except Exception as e:
        log(f"tts: duck_on error: {e}", tag="TTS", level="WARN")


def duck_music_off():
    """Сказать Music worker: resume."""
    try:
        ipc.put(protocol.MUSIC_INBOX, protocol.music_control("duck_off"))
    except Exception as e:
        log(f"tts: duck_off error: {e}", tag="TTS", level="WARN")