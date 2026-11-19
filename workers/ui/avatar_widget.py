# workers/ui/avatar_widget.py
# Плазменное 3D-ядро Макса на QOpenGLWidget (аппаратный рендер через ANGLE / Direct3D 11 для iGPU).

import math
import random
import time

import numpy as np
from PySide6.QtCore import Qt, QTimer, QPointF, QRectF
from PySide6.QtGui import (
    QColor, QImage, QPainter, QPainterPath, QPen, QPixmap, QPolygonF, QRadialGradient, QSurfaceFormat,
)
from PySide6.QtOpenGLWidgets import QOpenGLWidget


TARGET_FPS = 60
FIL_COUNT = 32
FIL_STEPS = 60
LOOP_COUNT = 14
LOOP_SEGS = 26
PARTICLE_COUNT = 180
NEBULA_BLOBS = 16
FOV = 2.85

_STATE_PALETTES = {
    "idle":      (QColor("#2563eb"), QColor("#9333ea"), QColor("#e0f2fe")),
    "sleep":     (QColor("#1e293b"), QColor("#312e81"), QColor("#94a3b8")),
    "listening": (QColor("#0284c7"), QColor("#38bdf8"), QColor("#ffffff")),
    "thinking":  (QColor("#4f46e5"), QColor("#c026d3"), QColor("#fae8ff")),
    "curious":   (QColor("#0891b2"), QColor("#f59e0b"), QColor("#fef3c7")),
    "speaking":  (QColor("#0ea5e9"), QColor("#a855f7"), QColor("#ffffff")),
    "music":     (QColor("#0d9488"), QColor("#8b5cf6"), QColor("#f0fdfa")),
    "happy":     (QColor("#0d9488"), QColor("#6366f1"), QColor("#f0fdfa")),
    "error":     (QColor("#d97706"), QColor("#dc2626"), QColor("#fef3c7")),
}

_EMOTION_TINTS = {
    "neutral":   None,
    "good":      (QColor("#14b8a6"), QColor("#6366f1"), QColor("#ecfeff")),
    "happy":     (QColor("#10b981"), QColor("#3b82f6"), QColor("#f0fdf4")),
    "curious":   (QColor("#06b6d4"), QColor("#f59e0b"), QColor("#fefce8")),
    "serious":   (QColor("#1d4ed8"), QColor("#475569"), QColor("#e2e8f0")),
    "tired":     (QColor("#334155"), QColor("#6d28d9"), QColor("#cbd5e1")),
}

_SPEED_MUL = {
    "sleep": 0.32,
    "idle": 0.78,
    "listening": 1.18,
    "curious": 1.55,
    "thinking": 2.15,
    "speaking": 1.45,
    "music": 1.35,
    "happy": 1.25,
    "error": 0.85,
}

_SOLID = Qt.PenStyle.SolidLine
_ROUND_CAP = Qt.PenCapStyle.RoundCap
_ROUND_JOIN = Qt.PenJoinStyle.RoundJoin


def _clamp_alpha(val: float) -> int:
    return max(0, min(255, int(val)))


def _lerp_color(c1: QColor, c2: QColor, t: float) -> QColor:
    t = max(0.0, min(1.0, t))
    return QColor(
        round(c1.red() + (c2.red() - c1.red()) * t),
        round(c1.green() + (c2.green() - c1.green()) * t),
        round(c1.blue() + (c2.blue() - c1.blue()) * t),
        _clamp_alpha(round(c1.alpha() + (c2.alpha() - c1.alpha()) * t)),
    )


def _rgba(c: QColor, a: float) -> QColor:
    return QColor(c.red(), c.green(), c.blue(), _clamp_alpha(a))


def _pen(color: QColor, width: float) -> QPen:
    return QPen(color, width, _SOLID, _ROUND_CAP, _ROUND_JOIN)


def _poly(xs_row: list[float], ys_row: list[float]) -> QPolygonF:
    return QPolygonF([QPointF(x, y) for x, y in zip(xs_row, ys_row)])


def _rate(rate_per_frame_40: float, dt: float) -> float:
    return 1.0 - (1.0 - rate_per_frame_40) ** (dt * 40.0)


def _make_glow_sprite(color: QColor, size: int = 24) -> QPixmap:
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    c = size * 0.5
    g = QRadialGradient(QPointF(c, c), c)
    g.setColorAt(0.0, QColor(color.red(), color.green(), color.blue(), 255))
    g.setColorAt(0.42, QColor(color.red(), color.green(), color.blue(), 145))
    g.setColorAt(1.0, QColor(color.red(), color.green(), color.blue(), 0))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(g)
    p.drawEllipse(QPointF(c, c), c, c)
    p.end()
    return QPixmap.fromImage(img)


class _Geometry:
    def __init__(self):
        self._build_filaments()
        self._build_loops()
        self._build_particles()
        self._build_nebula()

    def _build_filaments(self):
        rng = random.Random(101)
        rows = []
        for i in range(FIL_COUNT):
            rows.append({
                "tilt_x": rng.uniform(-math.pi, math.pi),
                "tilt_z": rng.uniform(-math.pi, math.pi),
                "speed": rng.uniform(0.20, 0.65) * (1 if i % 2 == 0 else -1),
                "phase": rng.uniform(0, math.tau),
                "scale_r": rng.uniform(0.76, 0.99),
                "turb_amp": rng.uniform(0.035, 0.085),
                "turb_freq": rng.choice([5.0, 7.0, 9.0]),
                "is_sec": (i % 5 in (1, 3)),
                "hot": (i % 5 == 0),
                "width": rng.uniform(0.95, 1.55),
            })
        col = lambda k: np.array([r[k] for r in rows], dtype=np.float64)
        c = lambda k: col(k)[:, None]

        self.fil_is_sec = [r["is_sec"] for r in rows]
        self.fil_hot = [r["hot"] for r in rows]
        self.fil_width = [r["width"] for r in rows]

        self._f_phase = col("phase")
        self._f_speed = col("speed")
        self._f_amp = c("turb_amp")
        self._f_scale = c("scale_r")
        self._f_tilt_z = col("tilt_z")
        self._f_tilt_x = col("tilt_x")

        T = np.linspace(0.0, math.tau, FIL_STEPS + 1)[None, :]
        self._sinT = np.sin(T)
        self._cosT = np.cos(T)
        self._sin2T = np.sin(2.0 * T)
        self._T2 = 2.0 * T
        self._TF = c("turb_freq") * T
        self._TF17 = self._TF * 1.7

    def _build_loops(self):
        rng = random.Random(202)
        rows = []
        for i in range(LOOP_COUNT):
            rows.append({
                "angle": (i / float(LOOP_COUNT)) * math.tau + rng.uniform(-0.20, 0.20),
                "span": rng.uniform(0.30, 0.65),
                "height": rng.uniform(0.12, 0.29),
                "speed": rng.uniform(0.32, 0.80),
                "phase": rng.uniform(0, math.tau),
                "kink": rng.uniform(0.02, 0.05),
                "is_sec": (i % 3 == 1),
            })
        col = lambda k: np.array([r[k] for r in rows], dtype=np.float64)
        self.loop_is_sec = [r["is_sec"] for r in rows]
        self._l_angle = col("angle")
        self._l_span = col("span")[:, None]
        self._l_height = col("height")
        self._l_speed = col("speed")
        self._l_phase = col("phase")
        self._l_kink = col("kink")[:, None]
        self._l_dir = np.array([1.0 if r["is_sec"] else -1.0 for r in rows])

        u = np.linspace(0.0, 1.0, LOOP_SEGS + 1)[None, :]
        self._u = u
        arch = np.maximum(0.0, np.sin(u * math.pi))
        self._arch = arch
        self._arch_pow = arch ** 0.85

    def _build_particles(self):
        rng = random.Random(303)
        rows = []
        for i in range(PARTICLE_COUNT):
            rows.append({
                "t_offset": rng.uniform(0, math.tau),
                "speed": rng.uniform(0.35, 1.10),
                "tilt_z": rng.uniform(-math.pi, math.pi),
                "rot_y": rng.uniform(-math.pi, math.pi),
                "radius_k": rng.uniform(0.45, 1.02),
                "size": rng.uniform(1.0, 2.2),
                "phase": rng.uniform(0, math.tau),
                "is_sec": (i % 3 == 0),
            })
        col = lambda k: np.array([r[k] for r in rows], dtype=np.float64)
        self.p_is_sec = [r["is_sec"] for r in rows]
        self._p_toff = col("t_offset")
        self._p_speed = col("speed")
        self._p_rk = col("radius_k")
        self._p_size = col("size")
        self._p_phase = col("phase")
        self._p_ct = np.cos(col("tilt_z"))
        self._p_st = np.sin(col("tilt_z"))
        self._p_ca = np.cos(col("rot_y"))
        self._p_sa = np.sin(col("rot_y"))

    def _build_nebula(self):
        rng = random.Random(55)
        blobs = []
        for i in range(NEBULA_BLOBS):
            blobs.append({
                "ang": rng.uniform(0, math.tau),
                "dist": rng.uniform(0.12, 0.68),
                "rad": rng.uniform(0.25, 0.46),
                "speed": rng.uniform(0.2, 0.6) * (1 if i % 2 == 0 else -1),
                "phase": rng.uniform(0, math.tau),
                "is_sec": (i % 2 == 0),
            })
        self.nebula_blobs = blobs

    @staticmethod
    def project(x, y, z, rot_x, rot_y, cx, cy, radius):
        cos_y, sin_y = math.cos(rot_y), math.sin(rot_y)
        x1 = x * cos_y + z * sin_y
        z1 = -x * sin_y + z * cos_y

        cos_x, sin_x = math.cos(rot_x), math.sin(rot_x)
        y2 = y * cos_x - z1 * sin_x
        z2 = y * sin_x + z1 * cos_x

        factor = FOV / np.maximum(1.1, FOV - z2)
        return cx + x1 * radius * factor, cy + y2 * radius * factor, z2

    def filaments(self, phase, rot_x, rot_y, cx, cy, radius, think_morph: float = 0.0, curious_morph: float = 0.0):
        amp = self._f_amp * (1.0 + think_morph * 1.35 + curious_morph * 0.55)
        reconfig = (
            think_morph * 0.85 * np.sin(phase * 1.4 + self._f_phase)
            + curious_morph * 0.45 * np.cos(phase * 1.1 + self._f_phase)
        )

        turb = (
            np.sin(self._TF + (phase * (1.8 + think_morph * 1.4) + self._f_phase)[:, None]) * amp
            + np.cos(self._TF17 - phase * 1.3) * (amp * 0.45)
        )
        r_local = (self._sinT + turb * self._sin2T) * self._f_scale

        x0 = r_local * self._cosT
        y0 = r_local * self._sinT
        z0 = np.sin(self._T2 + (phase + self._f_phase)[:, None]) * (0.28 + think_morph * 0.22) + turb

        tx = (self._f_tilt_x + reconfig)[:, None]
        tz = (self._f_tilt_z - reconfig * 0.7)[:, None]
        cxt, sxt = np.cos(tx), np.sin(tx)
        ct, st = np.cos(tz), np.sin(tz)

        y1 = y0 * cxt - z0 * sxt
        z1 = y0 * sxt + z0 * cxt

        x2 = x0 * ct - y1 * st
        y2 = x0 * st + y1 * ct

        rot = phase * self._f_speed + self._f_phase
        ca = np.cos(rot)[:, None]
        sa = np.sin(rot)[:, None]
        x3 = x2 * ca + z1 * sa
        z3 = -x2 * sa + z1 * ca

        sx, sy, _ = self.project(x3, y2, z3, rot_x, rot_y, cx, cy, radius)
        return sx, sy

    def loops(self, phase, energy, cx, cy, radius, music_bands=(0.0, 0.0, 0.0), mood_drive: float = 0.5):
        bass, mid, high = music_bands
        ang = self._l_angle + phase * (0.10 + 0.08 * mood_drive) * self._l_dir
        pulse = np.maximum(0.0, 0.55 + 0.45 * np.sin(phase * self._l_speed + self._l_phase))

        freq_boost = (
            bass * 0.95 * np.cos(self._l_angle * 2.0 + phase)
            + mid * 0.75 * np.sin(self._l_angle * 4.0 - phase * 1.7)
            + high * 0.65 * np.sin(self._l_angle * 7.0 + phase * 2.5)
        )
        arch_h = radius * self._l_height * (0.60 + 0.65 * pulse + energy * 0.55 + np.maximum(0.0, freq_boost))

        theta = ang[:, None] + (self._u - 0.5) * self._l_span
        ripple = (
            np.sin(self._u * (math.pi * 5.0) + phase * (1.8 + mood_drive * 1.4) + self._l_phase[:, None])
            * (self._l_kink * radius * (1.0 + high * 2.4)) * self._arch
        )
        r = radius * 0.95 + arch_h[:, None] * self._arch_pow + ripple
        return cx + np.cos(theta) * r, cy + np.sin(theta) * r, pulse

    def particles(self, phase, rot_x, rot_y, cx, cy, radius, spread: float = 1.0, alpha_mul: float = 1.0):
        t = phase * self._p_speed + self._p_toff
        r_local = np.sin(t) * self._p_rk * spread
        x0 = r_local * np.cos(t)
        y0 = r_local * np.sin(t)
        z0 = np.sin(t * 2.0 + self._p_phase) * 0.25

        x1 = x0 * self._p_ct - y0 * self._p_st
        y1 = x0 * self._p_st + y0 * self._p_ct

        x2 = x1 * self._p_ca + z0 * self._p_sa
        z2 = -x1 * self._p_sa + z0 * self._p_ca

        sx, sy, z = self.project(x2, y1, z2, rot_x, rot_y, cx, cy, radius)
        depth = np.clip((z + 1.0) * 0.5, 0.22, 1.0)
        twinkle = 0.5 + 0.5 * np.sin(phase * 2.4 + self._p_phase)
        alpha = np.clip(0.88 * alpha_mul * depth * twinkle, 0.0, 1.0)
        rad = self._p_size * (0.70 + 0.60 * depth)
        return sx, sy, alpha, rad


class MaksAvatarWidget(QOpenGLWidget):
    """Плазменное 3D-ядро Макса (iGPU ANGLE / Direct3D 11, 60 FPS)."""

    SHOW_FPS = True

    def __init__(self, parent=None):
        super().__init__(parent)

        # Без FBO MSAA — iGPU работает на максимальной скорости, а линии сглаживаются шейдером QPainter
        fmt = QSurfaceFormat()
        fmt.setSamples(0)
        fmt.setSwapInterval(1)
        self.setFormat(fmt)
        self.setUpdateBehavior(QOpenGLWidget.UpdateBehavior.NoPartialUpdate)

        self.setMouseTracking(True)
        self.setMinimumHeight(280)

        self._state = "idle"
        self._emotion = "neutral"
        self._phase = 0.0

        self._mic_level = 0.0
        self._smooth_mic = 0.0
        self._speech_level = 0.0
        self._smooth_speech = 0.0

        self._smooth_energy = 0.0
        self._frame_idx = 0

        self._w_sleep = 0.0
        self._w_listen = 0.0
        self._w_think = 0.0
        self._w_curious = 0.0
        self._w_speak = 0.0
        self._w_music = 0.0

        self._music_bass = 0.0
        self._music_mid = 0.0
        self._music_high = 0.0
        self._music_beat = 0.0
        self._music_mood = 0.45

        self._smooth_bass = 0.0
        self._smooth_mid = 0.0
        self._smooth_high = 0.0
        self._smooth_beat = 0.0

        self._rot_y = 0.0
        self._rot_x = 0.24
        self._mouse_norm: tuple[float, float] | None = None

        self._c_pri = QColor(_STATE_PALETTES["idle"][0])
        self._c_sec = QColor(_STATE_PALETTES["idle"][1])
        self._c_core = QColor(_STATE_PALETTES["idle"][2])

        self._sprite_sec: QPixmap | None = None
        self._sprite_core: QPixmap | None = None
        self._sprite_key: tuple | None = None
        self._nebula_buf: QImage | None = None

        self._geo = _Geometry()

        self._last_t = time.perf_counter()
        self._paint_ms = 0.0

        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(max(1, int(round(1000.0 / TARGET_FPS))))
        self._timer.timeout.connect(self._tick)

    def set_state(self, state: str):
        if state in _STATE_PALETTES:
            self._state = state

    def set_emotion(self, emotion: str):
        e = (emotion or "neutral").strip().lower()
        if e in _EMOTION_TINTS:
            self._emotion = e

    def set_mic_level(self, level: float):
        self._mic_level = max(0.0, min(1.0, float(level)))

    def set_speech_level(self, level: float):
        self._speech_level = max(0.0, min(1.0, float(level)))

    def set_music_spectrum(self, bass: float, mid: float, high: float, beat: float = 0.0, mood: float | None = None):
        self._music_bass = max(0.0, min(1.0, float(bass)))
        self._music_mid = max(0.0, min(1.0, float(mid)))
        self._music_high = max(0.0, min(1.0, float(high)))
        self._music_beat = max(0.0, min(1.0, float(beat)))
        if mood is not None:
            self._music_mood += (max(0.0, min(1.0, float(mood))) - self._music_mood) * 0.15

    def showEvent(self, event):
        self._last_t = time.perf_counter()
        self._timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    def mouseMoveEvent(self, event):
        w = max(1, self.width())
        h = max(1, self.height())
        nx = (event.position().x() / w - 0.5) * 2.0
        ny = (event.position().y() / h - 0.5) * 2.0
        self._mouse_norm = (max(-1.0, min(1.0, nx)), max(-1.0, min(1.0, ny)))
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self._mouse_norm = None
        super().leaveEvent(event)

    def _ensure_sprites(self):
        key = (
            self._c_sec.red() >> 2, self._c_sec.green() >> 2, self._c_sec.blue() >> 2,
            self._c_core.red() >> 2, self._c_core.green() >> 2, self._c_core.blue() >> 2,
        )
        if key != self._sprite_key or self._sprite_sec is None:
            self._sprite_key = key
            self._sprite_sec = _make_glow_sprite(self._c_sec, 24)
            self._sprite_core = _make_glow_sprite(self._c_core, 24)

    def _render_nebula_layer(self):
        side = 192
        if self._nebula_buf is None:
            self._nebula_buf = QImage(side, side, QImage.Format.Format_ARGB32_Premultiplied)
        buf = self._nebula_buf
        buf.fill(Qt.GlobalColor.transparent)

        bp = QPainter(buf)
        bp.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)

        bc = side * 0.5
        br = side * (70.0 / 150.0)

        clip = QPainterPath()
        clip.addEllipse(QRectF(bc - br, bc - br, br * 2.0, br * 2.0))
        bp.setClipPath(clip)
        bp.setPen(Qt.PenStyle.NoPen)

        energy = self._smooth_energy
        sleep_dim = 1.0 - self._w_sleep * 0.55
        alpha_in = _clamp_alpha((62 + energy * 38) * sleep_dim)
        for b in self._geo.nebula_blobs:
            ang = b["ang"] + self._phase * b["speed"]
            dist = br * b["dist"] * (0.88 + 0.12 * math.sin(self._phase + b["phase"]))
            bx = bc + math.cos(ang) * dist
            by = bc + math.sin(ang) * dist * 0.88
            rad = br * b["rad"] * (1.0 + energy * 0.22)

            base = self._c_sec if b["is_sec"] else self._c_pri
            g = QRadialGradient(QPointF(bx, by), rad)
            g.setColorAt(0.0, _rgba(base, alpha_in))
            g.setColorAt(1.0, _rgba(base, 0))
            bp.setBrush(g)
            bp.drawEllipse(QPointF(bx, by), rad, rad)

        bp.end()

    def _tick(self):
        now = time.perf_counter()
        dt = min(0.05, now - self._last_t)
        self._last_t = now

        if not self.isVisible() or self.window().isMinimized():
            return
        if dt <= 0.0:
            return

        music_speed_adj = 1.0 + self._w_music * (self._music_mood - 0.45) * 0.85
        speed_mul = _SPEED_MUL.get(self._state, 1.0) * music_speed_adj

        self._phase += dt * 1.85 * speed_mul
        self._frame_idx += 1

        k_morph = _rate(0.12, dt)
        self._w_sleep += ((1.0 if self._state == "sleep" else 0.0) - self._w_sleep) * k_morph
        self._w_listen += ((1.0 if self._state == "listening" else 0.0) - self._w_listen) * k_morph
        self._w_think += ((1.0 if self._state == "thinking" else 0.0) - self._w_think) * k_morph
        self._w_curious += ((1.0 if self._state == "curious" else 0.0) - self._w_curious) * k_morph
        self._w_speak += ((1.0 if self._state == "speaking" else 0.0) - self._w_speak) * k_morph
        self._w_music += ((1.0 if self._state == "music" else 0.0) - self._w_music) * k_morph

        t_pri, t_sec, t_core = _STATE_PALETTES.get(self._state, _STATE_PALETTES["idle"])
        emo_tint = _EMOTION_TINTS.get(self._emotion)
        if emo_tint is not None and self._state not in ("sleep", "error"):
            t_pri = _lerp_color(t_pri, emo_tint[0], 0.45)
            t_sec = _lerp_color(t_sec, emo_tint[1], 0.45)
            t_core = _lerp_color(t_core, emo_tint[2], 0.35)

        k_col = _rate(0.08, dt)
        self._c_pri = _lerp_color(self._c_pri, t_pri, k_col)
        self._c_sec = _lerp_color(self._c_sec, t_sec, k_col)
        self._c_core = _lerp_color(self._c_core, t_core, k_col)

        self._smooth_mic += (self._mic_level - self._smooth_mic) * _rate(0.45, dt)
        self._smooth_speech += (self._speech_level - self._smooth_speech) * _rate(0.50, dt)

        k_mus = _rate(0.55, dt)
        self._smooth_bass += (self._music_bass - self._smooth_bass) * k_mus
        self._smooth_mid += (self._music_mid - self._smooth_mid) * k_mus
        self._smooth_high += (self._music_high - self._smooth_high) * k_mus
        self._smooth_beat += (self._music_beat - self._smooth_beat) * _rate(0.65, dt)

        if self._state == "speaking":
            fallback_env = 0.22 + 0.25 * abs(math.sin(self._phase * 5.0))
            speech_drive = max(self._smooth_speech, fallback_env * 0.55)
            target_energy = min(1.0, speech_drive * 1.25)
        elif self._state == "listening":
            target_energy = min(1.0, 0.26 + self._smooth_mic * 0.85)
        elif self._state == "music":
            target_energy = min(1.0, 0.16 + self._smooth_bass * 0.65 + self._smooth_beat * 0.50)
        elif self._state == "thinking":
            target_energy = 0.52 + 0.22 * math.sin(self._phase * 3.1)
        elif self._state == "curious":
            target_energy = 0.42 + 0.16 * math.sin(self._phase * 2.4)
        elif self._state == "sleep":
            target_energy = 0.04 + 0.03 * math.sin(self._phase * 0.7)
        else:
            target_energy = 0.10 + 0.06 * math.sin(self._phase * 1.1)

        decay = 0.88 ** (dt * 40.0)
        self._mic_level *= decay
        self._speech_level *= decay
        self._music_bass *= 0.85 ** (dt * 40.0)
        self._music_mid *= 0.85 ** (dt * 40.0)
        self._music_high *= 0.85 ** (dt * 40.0)
        self._music_beat *= 0.80 ** (dt * 40.0)

        self._smooth_energy += (target_energy - self._smooth_energy) * _rate(0.32, dt)

        frame_scale = dt * 40.0
        self._rot_y += dt * 0.42 * speed_mul
        if self._mouse_norm is not None:
            mx, my = self._mouse_norm
            self._rot_y += mx * 0.032 * frame_scale
            self._rot_x += (my * 0.55 - self._rot_x) * _rate(0.08, dt)
        else:
            curious_tilt = self._w_curious * (0.22 * math.sin(self._phase * 0.9) - 0.14)
            goal = 0.24 + math.sin(self._phase * 0.28) * 0.12 + curious_tilt
            self._rot_x += (goal - self._rot_x) * _rate(0.06, dt)

        if self._frame_idx % 3 == 0:
            self._render_nebula_layer()

        self.update()

    def paintGL(self):
        t0 = time.perf_counter()

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        w = float(self.width())
        h = float(self.height())

        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        p.fillRect(0, 0, int(w) + 1, int(h) + 1, QColor(22, 30, 46, 255))

        cx = w * 0.5 + self._w_curious * math.sin(self._phase * 0.8) * (w * 0.025)
        cy = h * 0.5 - self._w_curious * (h * 0.015)
        base_r = min(w, h) * 0.375

        shell_expand = (
            1.0
            - self._w_sleep * 0.12
            + self._w_listen * (0.14 + self._smooth_mic * 0.14)
            + self._w_curious * 0.05
            + self._w_music * (self._smooth_bass * 0.15 + self._smooth_beat * 0.18)
        )
        radius = base_r * (1.0 + self._smooth_energy * 0.06) * shell_expand

        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        sleep_dim = 1.0 - self._w_sleep * 0.50

        # 1. Внешнее гало
        halo_r = radius * (1.45 + self._smooth_energy * 0.18 + self._w_listen * 0.15 + self._w_music * self._smooth_beat * 0.22)
        halo = QRadialGradient(QPointF(cx, cy), halo_r)
        halo.setColorAt(0.0, _rgba(self._c_pri, (72 + self._w_music * self._smooth_beat * 55) * sleep_dim))
        halo.setColorAt(0.52, _rgba(self._c_sec, (38 + self._w_music * self._smooth_mid * 35) * sleep_dim))
        halo.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(halo)
        p.drawEllipse(QPointF(cx, cy), halo_r, halo_r)

        # 2. Волны
        if self._w_listen > 0.05 or self._w_speak > 0.05 or (self._w_music > 0.05 and self._smooth_beat > 0.15):
            self._draw_wave_rings(p, cx, cy, radius)

        # 3. Частотный контур сферы в режиме music
        if self._w_music > 0.05:
            self._draw_music_contour(p, cx, cy, radius)

        # 4. Корональные магнитные петли
        self._draw_prominence_loops(p, cx, cy, radius, sleep_dim)

        # 5. Туманность (аппаратная текстура в OpenGL)
        if self._nebula_buf is not None:
            target_rect = QRectF(cx - radius * 1.06, cy - radius * 1.06, radius * 2.12, radius * 2.12)
            p.drawImage(target_rect, self._nebula_buf)

        # 6. Сферическая оболочка Френеля
        shell_edge_alpha = (175 + self._w_listen * (45 + self._smooth_mic * 35) + self._w_music * self._smooth_beat * 65) * sleep_dim
        shell = QRadialGradient(QPointF(cx, cy), radius)
        shell.setColorAt(0.0, _rgba(self._c_pri, 12 * sleep_dim))
        shell.setColorAt(0.74 - self._w_listen * 0.08, _rgba(self._c_sec, (42 + self._w_listen * 24) * sleep_dim))
        shell.setColorAt(0.955, _rgba(self._c_core, shell_edge_alpha))
        shell.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setBrush(shell)
        p.drawEllipse(QPointF(cx, cy), radius, radius)

        # 7. Турбулентные 3D-филаменты
        self._draw_plasma_filaments(p, cx, cy, radius, sleep_dim)

        # 8. Микрочастицы (аппаратные текстурные спрайты в GL)
        self._draw_stream_particles(p, cx, cy, radius, sleep_dim)

        # 9. Ядро
        self._draw_core_singularity(p, cx, cy, radius, sleep_dim)

        if self.SHOW_FPS:
            self._draw_fps(p)

        p.end()

        ms = (time.perf_counter() - t0) * 1000.0
        self._paint_ms += (ms - self._paint_ms) * 0.1

    def _draw_fps(self, p: QPainter):
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        p.setPen(QColor(255, 255, 255, 220))
        p.drawText(8, 16, f"iGPU paint {self._paint_ms:.2f} ms  (~{min(60.0, 1000.0 / max(self._paint_ms, 1e-3)):.0f} fps)")

    def _draw_wave_rings(self, p: QPainter, cx: float, cy: float, radius: float):
        p.setBrush(Qt.BrushStyle.NoBrush)
        active_w = max(self._w_listen, self._w_speak, self._w_music * self._smooth_beat)
        speed = 0.45 if self._w_listen >= self._w_speak else 0.85
        audio_drive = (
            self._w_listen * (0.25 + self._smooth_mic * 1.15)
            + self._w_speak * (0.20 + self._smooth_speech * 1.30)
            + self._w_music * (self._smooth_beat * 1.40)
        )

        for k in range(3):
            prog = (self._phase * speed + k * 0.33) % 1.0
            r = radius * (0.94 + prog * (0.58 * self._w_listen + 0.46 * self._w_speak + 0.40 * self._w_music))
            alpha = _clamp_alpha((1.0 - prog) * 135 * active_w * min(1.3, audio_drive))
            if alpha <= 2:
                continue
            col = _lerp_color(self._c_pri, self._c_core, prog * 0.80)
            col.setAlpha(alpha)
            width = 1.2 + audio_drive * 1.5
            p.setPen(QPen(col, width))
            p.drawEllipse(QPointF(cx, cy), r, r)

    def _draw_music_contour(self, p: QPainter, cx: float, cy: float, radius: float):
        steps = 84
        angles = np.linspace(0.0, math.tau, steps + 1)
        b = self._smooth_bass
        m = self._smooth_mid
        h = self._smooth_high
        mood = self._music_mood

        n_mid = 6.0 if mood > 0.5 else 4.0
        n_high = 12.0 if mood > 0.5 else 7.0
        wave = (
            b * 0.16 * np.sin(angles * 3.0 + self._phase * 2.2)
            + m * 0.11 * np.cos(angles * n_mid - self._phase * 3.5)
            + h * 0.075 * np.sin(angles * n_high + self._phase * 5.8)
        )
        rr = radius * (1.02 + wave)
        xs = (cx + np.cos(angles) * rr).tolist()
        ys = (cy + np.sin(angles) * rr).tolist()
        poly = _poly(xs, ys)

        p.setBrush(Qt.BrushStyle.NoBrush)
        outer_col = _rgba(self._c_sec, 95 * self._w_music * (0.30 + b))
        p.setPen(_pen(outer_col, 4.4))
        p.drawPolyline(poly)

        inner_col = _rgba(self._c_core, 215 * self._w_music * (0.30 + m + h * 0.6))
        p.setPen(_pen(inner_col, 1.7))
        p.drawPolyline(poly)

    def _draw_prominence_loops(self, p: QPainter, cx: float, cy: float, radius: float, sleep_dim: float):
        p.setBrush(Qt.BrushStyle.NoBrush)
        geo = self._geo
        bands = (
            self._smooth_bass * self._w_music,
            self._smooth_mid * self._w_music,
            self._smooth_high * self._w_music,
        )
        xs, ys, pulse = geo.loops(
            self._phase, self._smooth_energy, cx, cy, radius,
            music_bands=bands, mood_drive=self._music_mood,
        )
        xs_l = xs.tolist()
        ys_l = ys.tolist()
        pulse_l = pulse.tolist()

        for i in range(LOOP_COUNT):
            pl = pulse_l[i] * sleep_dim
            if pl <= 0.04:
                continue
            poly = _poly(xs_l[i], ys_l[i])
            base_col = self._c_sec if geo.loop_is_sec[i] else self._c_pri

            p.setPen(_pen(_rgba(base_col, 44 * pl), 3.8))
            p.drawPolyline(poly)

            c_mid = _lerp_color(base_col, self._c_core, 0.40)
            c_mid.setAlpha(_clamp_alpha(145 * pl))
            p.setPen(_pen(c_mid, 1.4))
            p.drawPolyline(poly)

    def _draw_plasma_filaments(self, p: QPainter, cx: float, cy: float, radius: float, sleep_dim: float):
        p.setBrush(Qt.BrushStyle.NoBrush)
        geo = self._geo
        xs, ys = geo.filaments(
            self._phase, self._rot_x, self._rot_y, cx, cy, radius,
            think_morph=self._w_think,
            curious_morph=self._w_curious,
        )
        xs_l = xs.tolist()
        ys_l = ys.tolist()

        energy = self._smooth_energy
        energy_boost = 0.25 + energy * 0.45 + self._w_think * 0.20 + self._w_curious * 0.15

        aura_pri = _pen(_rgba(self._c_pri, (38 + energy * 28) * sleep_dim), 3.0)
        aura_sec = _pen(_rgba(self._c_sec, (38 + energy * 28) * sleep_dim), 3.0)

        c_pri_main = _lerp_color(self._c_pri, self._c_core, energy_boost)
        c_pri_main.setAlpha(_clamp_alpha((115 + energy * 75 + self._w_think * 35) * sleep_dim))
        pen_pri = _pen(c_pri_main, 1.25)

        c_sec_main = _lerp_color(self._c_sec, self._c_core, energy_boost)
        c_sec_main.setAlpha(_clamp_alpha((115 + energy * 75 + self._w_think * 35) * sleep_dim))
        pen_sec = _pen(c_sec_main, 1.25)

        pen_hot = _pen(_rgba(self._c_core, (170 + energy * 80) * sleep_dim), 1.55)

        for i in range(FIL_COUNT):
            poly = _poly(xs_l[i], ys_l[i])
            is_sec = geo.fil_is_sec[i]

            p.setPen(aura_sec if is_sec else aura_pri)
            p.drawPolyline(poly)

            if geo.fil_hot[i]:
                p.setPen(pen_hot)
            else:
                p.setPen(pen_sec if is_sec else pen_pri)
            p.drawPolyline(poly)

    def _draw_stream_particles(self, p: QPainter, cx: float, cy: float, radius: float, sleep_dim: float):
        self._ensure_sprites()
        geo = self._geo
        spread = (
            1.0
            + self._w_listen * (0.10 + self._smooth_mic * 0.12)
            + self._w_music * (self._smooth_beat * 0.14)
            - self._w_think * 0.08
            - self._w_sleep * 0.15
        )
        sx, sy, alpha, rad = geo.particles(
            self._phase, self._rot_x, self._rot_y, cx, cy, radius,
            spread=spread, alpha_mul=sleep_dim,
        )

        is_sec = geo.p_is_sec
        sp_sec = self._sprite_sec
        sp_core = self._sprite_core
        for i, (x, y, a, r) in enumerate(zip(sx.tolist(), sy.tolist(), alpha.tolist(), rad.tolist())):
            if a <= 0.03:
                continue
            p.setOpacity(a)
            d = r * 3.4
            half = d * 0.5
            p.drawPixmap(QRectF(x - half, y - half, d, d), sp_sec if is_sec[i] else sp_core, QRectF(0.0, 0.0, 24.0, 24.0))
        p.setOpacity(1.0)

    def _draw_core_singularity(self, p: QPainter, cx: float, cy: float, radius: float, sleep_dim: float):
        idle_breath = 0.92 + 0.12 * math.sin(self._phase * 1.4)
        speak_imp = self._w_speak * self._smooth_speech * 0.85
        mic_imp = self._w_listen * self._smooth_mic * 0.45
        music_imp = self._w_music * (self._smooth_bass * 0.35 + self._smooth_beat * 0.65)
        pulse = (idle_breath + speak_imp + mic_imp + music_imp + self._smooth_energy * 0.20) * (1.0 - self._w_sleep * 0.25)

        core_r = radius * 0.34 * pulse

        c_w = _rgba(self._c_core, 245 * sleep_dim)
        c_s = _rgba(self._c_sec, 155 * sleep_dim)
        c_p = _rgba(self._c_pri, 55 * sleep_dim)
        transparent = QColor(0, 0, 0, 0)

        p.setPen(Qt.PenStyle.NoPen)
        for sign in (-1.0, 1.0):
            ang = self._phase * 0.85 + (0.0 if sign > 0 else math.pi)
            orbit_k = 0.085 * (1.0 - self._w_speak * 0.4)
            ox = cx + math.cos(ang) * radius * orbit_k
            oy = cy + math.sin(ang) * radius * (orbit_k * 0.56)

            g = QRadialGradient(QPointF(ox, oy), core_r)
            g.setColorAt(0.0, c_w)
            g.setColorAt(0.24, c_s)
            g.setColorAt(0.62, c_p)
            g.setColorAt(1.0, transparent)
            p.setBrush(g)
            p.drawEllipse(QPointF(ox, oy), core_r, core_r)

        pin_r = radius * (0.13 + self._w_speak * self._smooth_speech * 0.18 + self._w_music * self._smooth_beat * 0.14) * pulse
        pin = QRadialGradient(QPointF(cx, cy), pin_r)
        pin.setColorAt(0.0, QColor(255, 255, 255, _clamp_alpha(252 * sleep_dim)))
        pin.setColorAt(0.45, _rgba(self._c_core, 175 * sleep_dim))
        pin.setColorAt(1.0, transparent)
        p.setBrush(pin)
        p.drawEllipse(QPointF(cx, cy), pin_r, pin_r)