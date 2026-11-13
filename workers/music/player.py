"""
workers/music/player.py — MusicManager, владелец VLC-плеера музыки.

Ducking = ПРИГЛУШЕНИЕ через audio_set_volume, не пауза.

Семантика duck():
  duck()            -> приглушить до cfg.DUCK_LEVEL (по умолчанию 30%)
  duck(35)          -> приглушить до 35%
  duck(0)           -> полная тишина
  duck(-1) / duck(150) -> clamp в 0..100

Семантика restore():
  Возвращает АКТУАЛЬНУЮ пользовательскую громкость self.volume.

ПЛАВНОСТЬ:
  duck()/restore() вызывают _fade_to_async() — плавное изменение
  громкости через многократный audio_set_volume() с шагом ~30 мс.

  FADE_DUCK_SEC = 0.1     — быстро, чтобы TTS не начинал поверх
                            громкой музыки.
  FADE_RESTORE_SEC = 1.0  — плавно, спешить некуда (TTS закончил).

duck_hold:
  Если True — restore() НЕ восстанавливает громкость (ждём новый трек).
  release_duck() сбрасывает hold и вызывает restore(), НО только если
  ducking не от TTS (_ducked_by_tts=False).

_ready:
  Плеер считается готовым только после mark_ready().
  duck()/restore()/get_state() без _ready — no-op (намерение
  приглушить фиксируется, применяется в mark_ready).

── FIX (v8.8, 2026-11-13): audio_set_volume(0) в clear_player ──────
  Симптом: при команде «включи X» старая песня играла ещё 0.5–1с
  на 100% после DUCK_OFF. Music._stop_old_track → music.stop() →
  clear_player() → pause() + set_media(None). На Windows с mmdevice
  VLC pause() НЕ останавливает звук мгновенно (буфер audio device
  0.5–1с). Старая песня успевала «вылезти» на 100%, когда DUCK_OFF
  снимал приглушение.

  Фикс: audio_set_volume(0) ПЕРЕД pause()/set_media(None). Простая
  VLC-операция, срабатывает мгновенно (не блокирует). Старая замолкает
  ДО того, как DUCK_OFF вернёт громкость.

  Безопасно для ducking: audio_set_volume(0) — на СТАРОМ плеере.
  MusicManager.volume (пользовательская громкость) не меняется.
  Новый плеер (set_player) ставит audio_set_volume(ducked/volume).

── FIX (этап 0.5): _apply_duck_when_playing ──────────────────────
  Симптом: после ускорения queue_wave (0.5 сек вместо 10) ducking
  перестал работать. В логе:
    20:14:53 [MUSIC] mark_ready                    ← плеер готов
    20:14:53 asr: микрофон ВЫКЛ (TTS идёт)         ← _duck_on() ОТ ASR
    20:14:53 [DUCK] volume 100 → 30 (intent) — state not ready
    20:14:53 [DUCK] volume → 100 (pre-duck=100)   ← restore() при mic_on
  Приглушения не было: duck(30) пришёл ПОСЛЕ mark_ready, но VLC ещё
  Opening (0.5–2 сек до Playing). _wait_until_ready_to_apply() ждёт
  0.2 сек, не дожидается → возвращает False → «применим при mark_ready».
  Но mark_ready УЖЕ прошёл → duck не применится никогда.

  Решение: duck() при _ready=True, но state не Playing/Paused —
  запускает фоновый поток _apply_duck_when_playing(target):
  ждёт Playing (до 3 сек) и применяет _fade_to_async(target).
  Выходит, если плеер заменён/очищен (player is not self.player).
  Если за 3 сек не дождались — выходит (VLC сломался / terminal).

── FIX (этап 0.5): print → log(tag="MUSIC") ────────────────────
  Раньше player.py логировал в stderr (print), в jarvis.log его
  не было. Отладка ducking — только по консоли.
  Теперь: log(tag="MUSIC", level=...). [DUCK] — DEBUG (шумно,
  но нужно для отладки), [VOL] — INFO (пользователь меняет),
  watchdog — WARN.

FIX (2026-11-08): clear_player и set_player не блокируют release().
FIX (2026-11-07): PCR, segfault-риски в fade, явный media.
FIX (2026-10-31): check_duck_watchdog чистит и залипший duck_hold.
"""

import threading
import time

import vlc

from shared import config as cfg
from shared.log import log


DUCK_WAIT_PLAYING_SEC = 0.2
DUCK_WAIT_STEP = 0.05

FADE_DUCK_SEC = 0.1
FADE_RESTORE_SEC = 1.0
FADE_STEP_SEC = 0.03

# FIX (этап 0.5): сколько ждать Playing в фоне после duck() при
# _ready=True, но state=Opening. VLC на Windows — 0.5–2 сек.
DUCK_WAIT_PLAYING_BG_SEC = 3.0

_STATE_TRANSITIONAL = (vlc.State.Opening, vlc.State.Buffering)
_STATE_TERMINAL = (vlc.State.Error, vlc.State.Ended, vlc.State.Stopped)


class MusicManager:
    def __init__(self):
        self.player = None
        self._media = None
        self.volume = 100

        self.ducked = False
        self.duck_hold = False

        self._ducked_by_tts = False
        self._pre_duck_volume = None
        self._duck_started_at = None
        self._hold_started_at = None
        self._duck_level = self._clamp_percent(
            getattr(cfg, "DUCK_LEVEL", 30)
        )

        self._ready = False
        self.lock = threading.RLock()

        self._fade_cancel = threading.Event()
        self._fade_thread = None

    # ========================================================
    # ПЛЕЕР
    # ========================================================
    def set_player(self, new_player, new_media=None):
        with self.lock:
            self._cancel_fade()
            old_player = self.player
            old_media = self._media
            self._ready = False
            self.player = new_player
            self._media = new_media

            if self.player:
                vol = self._duck_level if self.ducked else self.volume
                try:
                    self.player.audio_set_volume(vol)
                except Exception:
                    pass

            pid = id(new_player) if new_player else None
            log(f"set_player id={pid}, vol={self.volume}, "
                f"ducked={self.ducked}, hold={self.duck_hold}, ready=False",
                tag="MUSIC", level="DEBUG")

        if old_player is not None and old_player is not new_player:
            threading.Thread(
                target=self._safe_release,
                args=(old_player, old_media),
                daemon=True,
            ).start()

    def mark_ready(self):
        with self.lock:
            self._ready = True
            pid = id(self.player) if self.player else None
            if self.player and self.ducked:
                try:
                    self.player.audio_set_volume(self._duck_level)
                    log(f"mark_ready → apply duck vol={self._duck_level}",
                        tag="MUSIC", level="DEBUG")
                except Exception:
                    pass
            if self.duck_hold and not self._ducked_by_tts:
                self.duck_hold = False
                self._hold_started_at = None
                log("hold OFF (auto on mark_ready)",
                    tag="MUSIC", level="DEBUG")
            log(f"mark_ready id={pid}", tag="MUSIC", level="DEBUG")

    def clear_player(self):
        with self.lock:
            self._cancel_fade()
            old_player = self.player
            old_media = self._media
            self.player = None
            self._media = None
            self._ready = False

        if old_player is not None:
            # v8.8: audio_set_volume(0) ПЕРЕД pause/set_media.
            # На Windows mmdevice VLC pause() не останавливает звук
            # мгновенно (буфер audio device 0.5–1с). audio_set_volume(0)
            # — простая VLC-операция, срабатывает мгновенно, не блокирует.
            # Старая песня замолкает ДО DUCK_OFF (который вернёт громкость).
            # MusicManager.volume не меняется — ducking не ломается
            # (новый плеер в set_player ставит ducked/volume).
            try:
                old_player.audio_set_volume(0)
            except Exception:
                pass
            try:
                old_player.pause()
            except Exception:
                pass
            try:
                old_player.set_media(None)
            except Exception:
                pass
            log("clear_player: volume(0) + pause + set_media(None), "
                "release в фоне", tag="MUSIC", level="DEBUG")
            threading.Thread(
                target=self._safe_release,
                args=(old_player, old_media),
                daemon=True,
            ).start()

    def has_player(self):
        with self.lock:
            return self.player is not None

    @staticmethod
    def _safe_release(player, media=None):
        try:
            player.release()
        except Exception:
            pass
        if media is not None:
            try:
                media.release()
            except Exception:
                pass

    # ========================================================
    # FADE
    # ========================================================
    def _cancel_fade(self):
        self._fade_cancel.set()
        t = self._fade_thread
        if t is not None and t.is_alive():
            try:
                t.join(timeout=FADE_STEP_SEC * 2)
            except Exception:
                pass
        self._fade_thread = None

    def _fade_to_async(self, target_percent: int,
                       duration_sec: float = FADE_RESTORE_SEC):
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
                        if player is not self.player:
                            return
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
                        if player is not self.player:
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
                    if player is not self.player:
                        return
                    try:
                        player.audio_set_volume(target_percent)
                    except Exception:
                        pass
                except Exception:
                    pass

            self._fade_thread = threading.Thread(target=runner, daemon=True)
            self._fade_thread.start()

    def _apply_duck_when_playing(self, target_percent: int):
        """FIX (этап 0.5): фон, ждёт state=Playing и применяет duck.

        Симптом: duck() пришёл после mark_ready, но VLC ещё Opening
        (0.5–2 сек до Playing). _wait_until_ready_to_apply() (0.2 сек)
        не дождался → «применим при mark_ready». Но mark_ready уже
        прошёл → duck не применится никогда.

        Этот поток ждёт Playing до DUCK_WAIT_PLAYING_BG_SEC (3 сек)
        и применяет _fade_to_async(target). Выходит при:
          - смене плеера (player is not self.player);
          - terminal state (Error/Ended/Stopped);
          - истечении 3 сек.

        Не блокирует lock: _fade_to_async сам берёт lock на мгновение.
        """
        player = self.player
        if player is None:
            return

        def runner():
            deadline = time.monotonic() + DUCK_WAIT_PLAYING_BG_SEC
            while time.monotonic() < deadline:
                if player is not self.player:
                    return
                try:
                    st = player.get_state()
                except Exception:
                    return
                if st in (vlc.State.Playing, vlc.State.Paused):
                    break
                if st in _STATE_TERMINAL:
                    log(f"apply_duck: terminal state={st}",
                        tag="MUSIC", level="DEBUG")
                    return
                time.sleep(DUCK_WAIT_STEP)
            else:
                log(f"apply_duck: не дождались Playing за "
                    f"{DUCK_WAIT_PLAYING_BG_SEC:.0f}с",
                    tag="MUSIC", level="WARN")
                return
            if player is not self.player:
                return
            log(f"apply_duck: Playing → fade to {target_percent}",
                tag="MUSIC", level="DEBUG")
            self._fade_to_async(target_percent, duration_sec=FADE_DUCK_SEC)

        threading.Thread(target=runner, daemon=True).start()

    # ========================================================
    # ГРОМКОСТЬ
    # ========================================================
    def set_volume(self, vol):
        vol = self._clamp_percent(vol)
        with self.lock:
            self.volume = vol
            if self.player and not self.ducked:
                self._cancel_fade()
                try:
                    self.player.audio_set_volume(vol)
                except Exception:
                    pass
        log(f"VOL {vol}%", tag="MUSIC")

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
                log("hold ON", tag="MUSIC", level="DEBUG")

    def release_duck(self):
        with self.lock:
            if not self.duck_hold:
                return
            self.duck_hold = False
            self._hold_started_at = None
            log("hold OFF", tag="MUSIC", level="DEBUG")
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
            log("mark_user_paused (flags cleared)",
                tag="MUSIC", level="DEBUG")

    # ========================================================
    # DUCK / RESTORE
    # ========================================================
    def duck(self, target_percent: int = None) -> bool:
        """Приглушить до target_percent (0..100). None → cfg.DUCK_LEVEL.

        Намерение фиксируется сразу (ducked=True, _ducked_by_tts=True,
        _pre_duck_volume=self.volume), даже если плеера нет или он
        Opening. Применить пробуем:
          1. прямо сейчас (fade), если state Playing/Paused;
          2. _apply_duck_when_playing — фон, ждёт Playing до 3 сек
             (FIX этап 0.5: раньше «применим при mark_ready», но
             mark_ready мог уже пройти → duck не применялся);
          3. в mark_ready() — если duck пришёл ДО mark_ready.
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
                prefix = f"{self._pre_duck_volume} → {target}"
            else:
                prefix = f"(already ducked) → {target}"

            if not self.player or not self._ready:
                log(f"volume {prefix} (intent) — применим при mark_ready",
                    tag="MUSIC", level="DEBUG")
                return True

            if not self._wait_until_ready_to_apply():
                # FIX (этап 0.5): плеер _ready, но state Opening.
                # Фон: ждать Playing и применить.
                log(f"volume {prefix} (intent) — state not ready, "
                    f"жду Playing в фоне", tag="MUSIC", level="DEBUG")
                self._apply_duck_when_playing(target)
                return True

            self._fade_to_async(target, duration_sec=FADE_DUCK_SEC)
            log(f"volume {prefix}", tag="MUSIC", level="DEBUG")
            return True

    def restore(self):
        with self.lock:
            if not self.ducked:
                return
            if self.duck_hold and not self._ducked_by_tts:
                log("hold ON — громкость сохраняется приглушённой",
                    tag="MUSIC", level="DEBUG")
                return

            self.ducked = False
            should_restore = self._ducked_by_tts
            self._ducked_by_tts = False
            pre = self._pre_duck_volume
            self._pre_duck_volume = None
            self._duck_started_at = None

            if not should_restore:
                log("restore (не мы приглушали, no-op)",
                    tag="MUSIC", level="DEBUG")
                return

            target = self.volume
            if not self.player:
                log("restore (no player, флаг сброшен)",
                    tag="MUSIC", level="DEBUG")
                return

            self._fade_to_async(target, duration_sec=FADE_RESTORE_SEC)
            log(f"volume → {target} (pre-duck={pre})",
                tag="MUSIC", level="DEBUG")

    def check_duck_watchdog(self, max_sec: float = 60.0) -> None:
        with self.lock:
            now = time.monotonic()

            if self.ducked and self._duck_started_at is not None:
                elapsed = now - self._duck_started_at
                if elapsed > max_sec:
                    log(f"watchdog: duck {elapsed:.0f}s > {max_sec:.0f}s, "
                        f"принудительный restore", tag="MUSIC", level="WARN")
                    self.restore()
                    return

            if self.duck_hold and self._hold_started_at is not None:
                elapsed = now - self._hold_started_at
                if elapsed > max_sec:
                    log(f"watchdog: hold {elapsed:.0f}s > {max_sec:.0f}s, "
                        f"принудительно снимаю", tag="MUSIC", level="WARN")
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
                log("pause", tag="MUSIC", level="DEBUG")
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
                log(f"resume (vol={vol}, ducked={self.ducked})",
                    tag="MUSIC", level="DEBUG")
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
                log(f"terminal state={state}", tag="MUSIC", level="DEBUG")
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