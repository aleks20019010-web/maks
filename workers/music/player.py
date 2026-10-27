"""
workers/music/player.py — MusicManager, владелец VLC-плеера музыки.

Ducking = ПАУЗА (VLC не даёт надёжный audio_set_volume при двух плеерах).
duck_hold: если True — restore НЕ снимает паузу (ждём новый трек).
release_duck: сбрасывает hold + ducked + paused_by_tts.

_ready: плеер считается готовым только после mark_ready().

FIX (день 28, баг из лога): duck() ждёт State.Playing (таймаут 1 сек).
  Раньше: если стейт State.Opening — пауза не ставилась, TTS говорил
  поверх музыки. Лог: [DUCK] not playing (state=State.Opening).
  Теперь: ждём до Playing, потом pause. Если не дождались — лог,
  пауза не поставлена. Это страховка: play_index тоже ждёт Playing
  перед mark_ready(), но если что-то пошло не так — duck() не сдаётся
  сразу.
"""

import threading
import time

import vlc


DUCK_WAIT_PLAYING_SEC = 1.0
DUCK_WAIT_STEP = 0.1


class MusicManager:
    def __init__(self):
        self.player = None
        self.volume = 100

        self.ducked = False
        self.paused_by_tts = False
        self.duck_hold = False

        self._ready = False

        self.lock = threading.RLock()

    # ========================================================
    # ПЛЕЕР
    # ========================================================
    def set_player(self, new_player):
        with self.lock:
            if self.player and self.player is not new_player:
                try:
                    self.player.stop()
                except Exception:
                    pass
                try:
                    self.player.release()
                except Exception:
                    pass
            self.player = new_player
            self._ready = False
            if self.player:
                try:
                    self.player.audio_set_volume(self.volume)
                except Exception:
                    pass
            print(f"   [MUSIC] set_player id={id(new_player)}, vol={self.volume}, "
                  f"ducked={self.ducked}, hold={self.duck_hold}, ready=False")

    def mark_ready(self):
        with self.lock:
            self._ready = True
            pid = id(self.player) if self.player else None
            print(f"   [MUSIC] mark_ready id={pid}")

    def clear_player(self):
        with self.lock:
            if self.player:
                try:
                    self.player.stop()
                except Exception:
                    pass
                try:
                    self.player.release()
                except Exception:
                    pass
            self.player = None
            self._ready = False

    def has_player(self):
        with self.lock:
            return self.player is not None

    # ========================================================
    # ГРОМКОСТЬ
    # ========================================================
    def set_volume(self, vol):
        vol = max(0, min(100, int(vol)))
        with self.lock:
            self.volume = vol
            if self.player:
                try:
                    self.player.audio_set_volume(vol)
                except Exception:
                    pass
        print(f"🔊 {vol}%")

    def get_volume(self):
        with self.lock:
            return self.volume

    def adjust_volume(self, delta):
        with self.lock:
            new_vol = max(0, min(100, self.volume + int(delta)))
        self.set_volume(new_vol)

    # ========================================================
    # DUCK_HOLD
    # ========================================================
    def hold_duck(self):
        with self.lock:
            if not self.duck_hold:
                self.duck_hold = True
                print(f"   [DUCK] hold ON")

    def release_duck(self):
        with self.lock:
            if self.duck_hold:
                self.duck_hold = False
            self.ducked = False
            self.paused_by_tts = False
            print(f"   [DUCK] release (hold OFF, ducked=False)")

    def mark_user_paused(self):
        with self.lock:
            self.duck_hold = False
            self.ducked = False
            self.paused_by_tts = False
            print(f"   [DUCK] mark_user_paused (flags cleared)")

    # ========================================================
    # DUCK / RESTORE (через pause/resume)
    # ========================================================
    def duck(self, level: float = 0.0):
        """Поставить паузу (ducking через pause).

        FIX (день 28): если стейт State.Opening — ждём State.Playing
        (таймаут 1 сек). Иначе пауза не ставится, TTS поверх музыки.
        """
        with self.lock:
            if not self.player:
                print("   [DUCK] no player")
                return False
            if not self._ready:
                print("   [DUCK] player not ready (пропуск)")
                return False
            if self.ducked:
                print(f"   [DUCK] already ducked")
                return True

            try:
                st = self.player.get_state()
            except Exception as e:
                print(f"   [DUCK] get_state error: {e}")
                return False

            # FIX: Opening → ждём Playing
            if st == vlc.State.Opening:
                print(f"   [DUCK] state=Opening, жду Playing...")
                waited = 0.0
                while waited < DUCK_WAIT_PLAYING_SEC:
                    time.sleep(DUCK_WAIT_STEP)
                    waited += DUCK_WAIT_STEP
                    try:
                        st = self.player.get_state()
                    except Exception:
                        break
                    if st == vlc.State.Playing:
                        break
                    if st in (vlc.State.Ended, vlc.State.Stopped, vlc.State.Error):
                        print(f"   [DUCK] state={st} пока ждал — пауза не ставлю")
                        return False

            if st == vlc.State.Playing:
                try:
                    self.player.pause()
                    self.paused_by_tts = True
                    self.ducked = True
                    print(f"   [DUCK] pause (player_id={id(self.player)})")
                except Exception as e:
                    print(f"   [DUCK] pause error: {e}")
            else:
                print(f"   [DUCK] not playing (state={st}), пауза НЕ поставлена")
        return True

    def restore(self, fade_sec=0.0):
        with self.lock:
            if not self.ducked:
                return
            if self.duck_hold:
                print(f"   [DUCK] hold ON — пауза сохраняется")
                return
            self.ducked = False
            should_resume = self.paused_by_tts
            self.paused_by_tts = False

        if should_resume:
            with self.lock:
                if self.player:
                    try:
                        self.player.play()
                        self.player.audio_set_volume(self.volume)
                        print(f"   [DUCK] resume (vol={self.volume})")
                    except Exception as e:
                        print(f"   [DUCK] resume error: {e}")
        else:
            print(f"   [DUCK] restore (без resume — не мы паузили)")

    # ========================================================
    # PAUSE / RESUME / STOP / STATE
    # ========================================================
    def pause(self):
        with self.lock:
            if self.player:
                try:
                    self.player.pause()
                    print("⏸")
                except Exception:
                    pass

    def resume(self):
        with self.lock:
            if self.player:
                try:
                    self.player.play()
                    self.player.audio_set_volume(self.volume)
                    print("▶")
                except Exception:
                    pass

    def stop(self):
        self.clear_player()

    def get_state(self):
        with self.lock:
            if not self.player or not self._ready:
                return None
            try:
                return self.player.get_state()
            except Exception:
                return None


# Синглтон
music = MusicManager()