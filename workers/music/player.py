"""
workers/music/player.py — MusicManager, владелец VLC-плеера музыки.

Ducking = ПРИГЛУШЕНИЕ через audio_set_volume, не пауза.

Семантика duck():
  duck()            -> приглушить до cfg.DUCK_LEVEL (по умолчанию 20%)
  duck(35)          -> приглушить до 35%
  duck(0)           -> полная тишина
  duck(-1) / duck(150) -> ValueError

Семантика restore():
  Возвращает АКТУАЛЬНУЮ пользовательскую громкость self.volume.

ПЛАВНОСТЬ (день 29):
  duck()/restore() вызывают _fade_to_async() — плавное изменение
  громкости через многократный audio_set_volume() с шагом ~30 мс.
  VLC не имеет встроенного fade в python-vlc, поэтому шагаем вручную
  в отдельном потоке (не блокируем lock).
  - Отмена: новый fade отменяет предыдущий (Event _fade_cancel).
  - При смене плеера (set_player) fade старого плеера отменяется.
  - Если fade не удался (player=None, VLC ещё Opening) — fallback
    на резкий audio_set_volume(target).

  Разная длительность для duck и restore:
    FADE_DUCK_SEC = 0.1     — быстро, чтобы TTS не начинал поверх
                              громкой музыки.
    FADE_RESTORE_SEC = 0.5  — плавно, спешить некуда (TTS закончил).

duck_hold:
  Если True — restore() НЕ восстанавливает громкость (ждём новый трек).
  release_duck() сбрасывает hold и вызывает restore(), НО только если
  ducking не от TTS (_ducked_by_tts=False).
  mark_ready() автоматически снимает hold после set_player, если
  ducking не от TTS.

_ready:
  Плеер считается готовым только после mark_ready().
  duck()/restore()/get_state() без _ready — no-op (намерение
  приглушить фиксируется, применяется в mark_ready).

FIX (день 29, вечер):
  - duck() ставит намерение приглушить ДО проверки player/_ready.
  - mark_ready(): применяет _duck_level при ducked=True; снимает
    залипший hold (если ducking не от TTS).
  - restore(): hold учитывается только если _ducked_by_tts=False.
  - release_duck(): при _ducked_by_tts=True НЕ вызывает restore().
  - _wait_until_ready_to_apply(): таймаут 0.2 сек.
  - check_duck_watchdog(max_sec): принудительный restore() при
    залипшем ducking (TTS/ASR упал между duck_on и duck_off).

FIX (2026-10-31):
  - check_duck_watchdog() теперь чистит и залипший duck_hold,
    даже если ducked=False (hold без duck — как в modes.py).
    Раньше такой hold висел вечно, если mark_ready() не вызывался.
  - _hold_started_at — время первого hold_duck() (для watchdog).
"""

import threading
import time

import vlc

from shared import config as cfg


DUCK_WAIT_PLAYING_SEC = 0.2
DUCK_WAIT_STEP = 0.05

FADE_DUCK_SEC = 0.1        # приглушение перед TTS — быстро
FADE_RESTORE_SEC = 1.0     # возврат после TTS — плавно
FADE_STEP_SEC = 0.03       # шаг между audio_set_volume

_STATE_TRANSITIONAL = (vlc.State.Opening, vlc.State.Buffering)
_STATE_TERMINAL = (vlc.State.Error, vlc.State.Ended, vlc.State.Stopped)


class MusicManager:
    def __init__(self):
        self.player = None
        self.volume = 100

        self.ducked = False
        self.duck_hold = False

        self._ducked_by_tts = False
        self._pre_duck_volume = None
        self._duck_started_at = None
        self._hold_started_at = None
        self._duck_level = self._clamp_percent(
            getattr(cfg, "DUCK_LEVEL", 20)
        )

        self._ready = False
        self.lock = threading.RLock()

        # Fade-отмена: новый fade ставит _fade_cancel.set(),
        # старый поток видит это и выходит.
        self._fade_cancel = threading.Event()
        self._fade_thread = None

    # ========================================================
    # ПЛЕЕР
    # ========================================================
    def set_player(self, new_player):
        with self.lock:
            self._cancel_fade()
            if self.player and self.player is not new_player:
                self._safe_release(self.player)

            self._ready = False
            self.player = new_player

            if self.player:
                vol = self._duck_level if self.ducked else self.volume
                try:
                    self.player.audio_set_volume(vol)
                except Exception:
                    pass

            pid = id(new_player) if new_player else None
            print(f"   [MUSIC] set_player id={pid}, vol={self.volume}, "
                  f"ducked={self.ducked}, hold={self.duck_hold}, ready=False")

    def mark_ready(self):
        with self.lock:
            self._ready = True
            pid = id(self.player) if self.player else None
            if self.player and self.ducked:
                try:
                    self.player.audio_set_volume(self._duck_level)
                    print(f"   [MUSIC] mark_ready → apply duck vol={self._duck_level}")
                except Exception:
                    pass
            if self.duck_hold and not self._ducked_by_tts:
                self.duck_hold = False
                self._hold_started_at = None
                print("   [DUCK] hold OFF (auto on mark_ready)")
            print(f"   [MUSIC] mark_ready id={pid}")

    def clear_player(self):
        with self.lock:
            self._cancel_fade()
            if self.player:
                self._safe_release(self.player)
            self.player = None
            self._ready = False

    def has_player(self):
        with self.lock:
            return self.player is not None

    @staticmethod
    def _safe_release(player):
        try:
            player.stop()
        except Exception:
            pass
        try:
            player.release()
        except Exception:
            pass

    # ========================================================
    # FADE
    # ========================================================
    def _cancel_fade(self):
        """Отменить текущий fade. Вызывать под self.lock."""
        self._fade_cancel.set()
        self._fade_thread = None

    def _fade_to_async(self, target_percent: int,
                       duration_sec: float = FADE_RESTORE_SEC):
        """Плавно изменить громкость в отдельном потоке.

        Не блокирует lock. Новый fade отменяет предыдущий.
        При player=None или VLC не готов — fallback на резкий set_volume.
        """
        target_percent = self._clamp_percent(target_percent)
        with self.lock:
            self._cancel_fade()
            cancel = threading.Event()
            self._fade_cancel = cancel
            player = self.player
            if player is None:
                return
            try:
                current = player.audio_get_volume()
            except Exception:
                current = None

            def runner():
                try:
                    if cancel.is_set():
                        return
                    if current is None or current < 0:
                        try:
                            player.audio_set_volume(target_percent)
                        except Exception:
                            pass
                        return
                    if current == target_percent:
                        return
                    steps = max(1, int(duration_sec / FADE_STEP_SEC))
                    delta = (target_percent - current) / steps
                    for i in range(steps):
                        if cancel.is_set():
                            return
                        vol = int(round(current + delta * (i + 1)))
                        vol = max(0, min(100, vol))
                        try:
                            player.audio_set_volume(vol)
                        except Exception:
                            return
                        time.sleep(FADE_STEP_SEC)
                    if cancel.is_set():
                        return
                    try:
                        player.audio_set_volume(target_percent)
                    except Exception:
                        pass
                except Exception:
                    pass

            self._fade_thread = threading.Thread(target=runner, daemon=True)
            self._fade_thread.start()

    # ========================================================
    # ГРОМКОСТЬ
    # ========================================================
    def set_volume(self, vol):
        vol = self._clamp_percent(vol)
        with self.lock:
            self.volume = vol
            if self.player and not self.ducked:
                # Пользователь меняет громкость — резко, без fade
                self._cancel_fade()
                try:
                    self.player.audio_set_volume(vol)
                except Exception:
                    pass
        print(f"   [VOL] {vol}%")

    def get_volume(self):
        with self.lock:
            return self.volume

    def adjust_volume(self, delta):
        with self.lock:
            new_vol = self._clamp_percent(self.volume + int(delta))
        self.set_volume(new_vol)

    # ========================================================
    # DUCK_HOLD
    # ========================================================
    def hold_duck(self):
        with self.lock:
            if not self.duck_hold:
                self.duck_hold = True
                self._hold_started_at = time.monotonic()
                print("   [DUCK] hold ON")

    def release_duck(self):
        with self.lock:
            if not self.duck_hold:
                return
            self.duck_hold = False
            self._hold_started_at = None
            print("   [DUCK] hold OFF")
            if self.ducked and not self._ducked_by_tts:
                self.restore()

    def mark_user_paused(self):
        with self.lock:
            self._cancel_fade()
            self.duck_hold = False
            self._hold_started_at = None
            self.ducked = False
            self._ducked_by_tts = False
            self._pre_duck_volume = None
            self._duck_started_at = None
            print("   [DUCK] mark_user_paused (flags cleared)")

    # ========================================================
    # DUCK / RESTORE
    # ========================================================
    def duck(self, target_percent: int = None) -> bool:
        """Приглушить до target_percent (0..100). None → cfg.DUCK_LEVEL.

        Намерение приглушить фиксируется сразу (ducked=True,
        _pre_duck_volume=self.volume), даже если плеера нет или он
        ещё в Opening. Применить к плееру попробуем:
          1. прямо сейчас (fade), если состояние Playing/Paused;
          2. в mark_ready(), когда плеер станет готов.
        Возвращает True, если намерение зафиксировано.
        """
        with self.lock:
            target = self._clamp_percent(
                self._duck_level if target_percent is None else target_percent
            )

            if not self.ducked:
                self._pre_duck_volume = self.volume
                self._ducked_by_tts = True
                self.ducked = True
                self._duck_started_at = time.monotonic()
                prefix = f"{self._pre_duck_volume} → {target} (intent)"
            else:
                prefix = f"(already ducked) → {target}"

            if not self.player or not self._ready:
                print(f"   [DUCK] volume {prefix} — применим при mark_ready")
                return True

            if not self._wait_until_ready_to_apply():
                print(f"   [DUCK] volume {prefix} — state not ready, "
                      f"применим при mark_ready")
                return True

            # Применяем плавно (в отдельном потоке). Намерение уже
            # зафиксировано, fade не блокирует lock.
            self._fade_to_async(target, duration_sec=FADE_DUCK_SEC)
            print(f"   [DUCK] volume {prefix}")
            return True

    def restore(self):
        with self.lock:
            if not self.ducked:
                return
            if self.duck_hold and not self._ducked_by_tts:
                print("   [DUCK] hold ON — громкость сохраняется приглушённой")
                return

            self.ducked = False
            should_restore = self._ducked_by_tts
            self._ducked_by_tts = False
            pre = self._pre_duck_volume
            self._pre_duck_volume = None
            self._duck_started_at = None

            if not should_restore:
                print("   [DUCK] restore (не мы приглушали, no-op)")
                return

            target = self.volume
            if not self.player:
                print("   [DUCK] restore (no player, флаг сброшен)")
                return

            # Плавно возвращаем.
            self._fade_to_async(target, duration_sec=FADE_RESTORE_SEC)
            print(f"   [DUCK] volume → {target} (pre-duck={pre})")

    def check_duck_watchdog(self, max_sec: float = 60.0) -> None:
        """Снять залипший ducking или duck_hold, если он висит дольше max_sec.

        Две независимые ветки:
          1) ducked=True дольше max_sec — mic/TTS упал между on и off.
          2) duck_hold=True дольше max_sec — loading упал, release_duck
             не был вызван (например, из-за отсутствия try/finally).
             Срабатывает, даже если ducked=False.
        """
        with self.lock:
            now = time.monotonic()

            # 1) залипший ducking
            if self.ducked and self._duck_started_at is not None:
                elapsed = now - self._duck_started_at
                if elapsed > max_sec:
                    print(f"   [DUCK] watchdog: duck {elapsed:.0f}s > {max_sec:.0f}s, "
                          f"принудительный restore")
                    self.restore()
                    return

            # 2) залипший hold (даже без duck)
            if self.duck_hold and self._hold_started_at is not None:
                elapsed = now - self._hold_started_at
                if elapsed > max_sec:
                    print(f"   [DUCK] watchdog: hold {elapsed:.0f}s > {max_sec:.0f}s, "
                          f"принудительно снимаю")
                    self.duck_hold = False
                    self._hold_started_at = None
                    if self.ducked and not self._ducked_by_tts:
                        self.restore()

    # ========================================================
    # PAUSE / RESUME / STOP / STATE
    # ========================================================
    def pause(self):
        with self.lock:
            if not self.player:
                return
            try:
                self.player.pause()
                print("   [MUSIC] pause")
            except Exception:
                pass

    def resume(self):
        with self.lock:
            if not self.player:
                return
            try:
                self._cancel_fade()
                self.player.play()
                vol = self._duck_level if self.ducked else self.volume
                self.player.audio_set_volume(vol)
                print(f"   [MUSIC] resume (vol={vol}, ducked={self.ducked})")
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

    # ========================================================
    # ВНУТРЕННЕЕ
    # ========================================================
    def _wait_until_ready_to_apply(self) -> bool:
        deadline = time.monotonic() + DUCK_WAIT_PLAYING_SEC
        while True:
            try:
                state = self.player.get_state()
            except Exception:
                return False
            if state in (vlc.State.Playing, vlc.State.Paused):
                return True
            if state in _STATE_TERMINAL:
                print(f"   [DUCK] terminal state={state}")
                return False
            if state not in _STATE_TRANSITIONAL:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(DUCK_WAIT_STEP)

    @staticmethod
    def _clamp_percent(value) -> int:
        return max(0, min(100, int(value)))


# Синглтон
music = MusicManager()