# workers/ui/avatar_widget.py
# Плазменное 3D-ядро Макса на QPainter — версия «60 FPS + максимум детализации».
#
# Что изменилось относительно прошлой версии (визуал сохранён, детализация выше):
#   - вся геометрия (филаменты, петли, частицы) считается векторно в numpy,
#     а не циклами Python -> в разы меньше CPU на кадр, освободившийся бюджет
#     потрачен на качество: 64 сегмента на филамент (было 44), 28 на петлю (было 20),
#     круглые стыки/окончания линий (RoundJoin/RoundCap) вместо «ломаных» углов;
#   - таймер 60 FPS (PreciseTimer) + реальный dt: скорость анимации не зависит от FPS;
#   - туманность рендерится в буфер под реальное разрешение экрана (учёт DPI);
#   - таймер останавливается, когда виджет скрыт/окно свёрнуто;
#   - цвета сходятся к палитре точно (round вместо int);
#   - speech_level затухает во всех состояниях.
#
# Диагностика: MaksAvatarWidget.SHOW_FPS = True рисует в углу FPS и время кадра.

import math
import random
import time

import numpy as np
from PySide6.QtCore import Qt, QTimer, QPointF, QRectF
from PySide6.QtGui import (
    QColor, QImage, QPainter, QPainterPath, QPen, QPolygonF, QRadialGradient,
)
from PySide6.QtWidgets import QWidget


# ── Настройки качества ────────────────────────────────────────────────
TARGET_FPS = 60
FIL_COUNT = 32
FIL_STEPS = 64          # сегментов на филамент
LOOP_COUNT = 14
LOOP_SEGS = 28          # сегментов на корональную петлю
PARTICLE_COUNT = 180
NEBULA_BLOBS = 18
FOV = 2.85

_STATE_PALETTES = {
    "idle":      (QColor("#2563eb"), QColor("#9333ea"), QColor("#e0f2fe")),
    "listening": (QColor("#0284c7"), QColor("#6366f1"), QColor("#ffffff")),
    "thinking":  (QColor("#4f46e5"), QColor("#c026d3"), QColor("#fae8ff")),
    "speaking":  (QColor("#0ea5e9"), QColor("#a855f7"), QColor("#ffffff")),
    "happy":     (QColor("#0d9488"), QColor("#6366f1"), QColor("#f0fdfa")),
    "error":     (QColor("#d97706"), QColor("#dc2626"), QColor("#fef3c7")),
}

_SPEED_MUL = {
    "thinking": 1.85,
    "speaking": 1.45,
    "listening": 1.25,
    "happy": 1.20,
    "error": 0.85,
    "idle": 1.0,
}

_SOLID = Qt.PenStyle.SolidLine
_ROUND_CAP = Qt.PenCapStyle.RoundCap
_ROUND_JOIN = Qt.PenJoinStyle.RoundJoin


# ── Утилиты ───────────────────────────────────────────────────────────
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


def _poly(xs: np.ndarray, ys: np.ndarray) -> QPolygonF:
    return QPolygonF([QPointF(x, y) for x, y in zip(xs.tolist(), ys.tolist())])


def _rate(rate_per_frame_40: float, dt: float) -> float:
    """Коэффициент сглаживания, не зависящий от FPS (калибровка — 40 FPS)."""
    return 1.0 - (1.0 - rate_per_frame_40) ** (dt * 40.0)


# ── Геометрия (numpy, без Qt) ─────────────────────────────────────────
class _Geometry:
    """Векторные расчёты филаментов, петель и частиц. Qt здесь не нужен."""

    def __init__(self):
        self._build_filaments()
        self._build_loops()
        self._build_particles()
        self._build_nebula()

    # --- построение параметров (rng-семена те же, что в старой версии) ---
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
                "hot": (i % 6 == 0),
                "width": rng.uniform(0.9, 1.5),
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
        self._f_ct = np.cos(c("tilt_z"))
        self._f_st = np.sin(c("tilt_z"))
        self._f_cxt = np.cos(c("tilt_x"))
        self._f_sxt = np.sin(c("tilt_x"))

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
                "size": rng.uniform(0.9, 2.1),
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
                "dist": rng.uniform(0.12, 0.70),
                "rad": rng.uniform(0.25, 0.48),
                "speed": rng.uniform(0.2, 0.6) * (1 if i % 2 == 0 else -1),
                "phase": rng.uniform(0, math.tau),
                "is_sec": (i % 2 == 0),
            })
        self.nebula_blobs = blobs

    # --- проекция ---
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

    # --- филаменты: (FIL_COUNT, FIL_STEPS+1) ---
    def filaments(self, phase, rot_x, rot_y, cx, cy, radius):
        amp = self._f_amp
        turb = (
            np.sin(self._TF + (phase * 1.8 + self._f_phase)[:, None]) * amp
            + np.cos(self._TF17 - phase * 1.3) * (amp * 0.45)
        )
        r_local = (self._sinT + turb * self._sin2T) * self._f_scale

        x0 = r_local * self._cosT
        y0 = r_local * self._sinT
        z0 = np.sin(self._T2 + (phase + self._f_phase)[:, None]) * 0.28 + turb

        y1 = y0 * self._f_cxt - z0 * self._f_sxt
        z1 = y0 * self._f_sxt + z0 * self._f_cxt

        x2 = x0 * self._f_ct - y1 * self._f_st
        y2 = x0 * self._f_st + y1 * self._f_ct

        rot = phase * self._f_speed + self._f_phase
        ca = np.cos(rot)[:, None]
        sa = np.sin(rot)[:, None]
        x3 = x2 * ca + z1 * sa
        z3 = -x2 * sa + z1 * ca

        sx, sy, _ = self.project(x3, y2, z3, rot_x, rot_y, cx, cy, radius)
        return sx, sy

    # --- петли: (LOOP_COUNT, LOOP_SEGS+1) + pulse (LOOP_COUNT,) ---
    def loops(self, phase, energy, cx, cy, radius):
        ang = self._l_angle + phase * 0.13 * self._l_dir
        pulse = np.maximum(0.0, 0.55 + 0.45 * np.sin(phase * self._l_speed + self._l_phase))
        arch_h = radius * self._l_height * (0.65 + 0.65 * pulse + energy * 0.48)

        theta = ang[:, None] + (self._u - 0.5) * self._l_span
        ripple = (
            np.sin(self._u * (math.pi * 5.0) + phase * 2.2 + self._l_phase[:, None])
            * (self._l_kink * radius) * self._arch
        )
        r = radius * 0.95 + arch_h[:, None] * self._arch_pow + ripple
        return cx + np.cos(theta) * r, cy + np.sin(theta) * r, pulse

    # --- частицы: (PARTICLE_COUNT,) ---
    def particles(self, phase, rot_x, rot_y, cx, cy, radius):
        t = phase * self._p_speed + self._p_toff
        r_local = np.sin(t) * self._p_rk
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
        alpha = np.clip(190.0 * depth * twinkle, 0.0, 255.0).astype(np.int32)
        rad = self._p_size * (0.60 + 0.55 * depth)
        return sx, sy, alpha, rad


# ── Виджет ────────────────────────────────────────────────────────────
class MaksAvatarWidget(QWidget):
    """Плазменное 3D-ядро ассистента Макса."""

    SHOW_FPS = False

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setMinimumHeight(280)

        self._state = "idle"
        self._phase = 0.0
        self._speech_level = 0.0
        self._smooth_energy = 0.0
        self._frame_idx = 0

        self._rot_y = 0.0
        self._rot_x = 0.24
        self._mouse_norm: tuple[float, float] | None = None

        self._c_pri = QColor(_STATE_PALETTES["idle"][0])
        self._c_sec = QColor(_STATE_PALETTES["idle"][1])
        self._c_core = QColor(_STATE_PALETTES["idle"][2])

        self._geo = _Geometry()

        self._nebula_buf: QImage | None = None
        self._last_t = time.perf_counter()
        self._paint_ms = 0.0

        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(max(1, int(round(1000.0 / TARGET_FPS))))
        self._timer.timeout.connect(self._tick)

    # ── Публичный API ─────────────────────────────────────────────────
    def set_state(self, state: str):
        if state in _STATE_PALETTES:
            self._state = state

    def set_speech_level(self, level: float):
        self._speech_level = max(0.0, min(1.0, float(level)))

    # ── События Qt ────────────────────────────────────────────────────
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

    # ── Анимационный шаг ──────────────────────────────────────────────
    def _tick(self):
        now = time.perf_counter()
        dt = min(0.05, now - self._last_t)
        self._last_t = now

        if not self.isVisible() or self.window().isMinimized():
            return
        if dt <= 0.0:
            return

        speed_mul = _SPEED_MUL.get(self._state, 1.0)

        self._phase += dt * 1.85 * speed_mul
        self._frame_idx += 1

        t_pri, t_sec, t_core = _STATE_PALETTES.get(self._state, _STATE_PALETTES["idle"])
        k_col = _rate(0.10, dt)
        self._c_pri = _lerp_color(self._c_pri, t_pri, k_col)
        self._c_sec = _lerp_color(self._c_sec, t_sec, k_col)
        self._c_core = _lerp_color(self._c_core, t_core, k_col)

        if self._state == "speaking":
            mod = 0.45 + 0.55 * abs(math.sin(self._phase * 4.6))
            target_energy = min(1.0, (self._speech_level * 1.25 + 0.25) * mod)
        elif self._state == "thinking":
            target_energy = 0.50 + 0.20 * math.sin(self._phase * 2.6)
        elif self._state == "listening":
            target_energy = 0.36
        else:
            target_energy = 0.12 + 0.06 * math.sin(self._phase * 1.2)

        # уровень речи затухает всегда: если TTS перестал слать значения — не «залипнет»
        self._speech_level *= 0.92 ** (dt * 40.0)

        self._smooth_energy += (target_energy - self._smooth_energy) * _rate(0.28, dt)

        frame_scale = dt * 40.0
        self._rot_y += dt * 0.42 * speed_mul
        if self._mouse_norm is not None:
            mx, my = self._mouse_norm
            self._rot_y += mx * 0.032 * frame_scale
            self._rot_x += (my * 0.55 - self._rot_x) * _rate(0.08, dt)
        else:
            goal = 0.24 + math.sin(self._phase * 0.28) * 0.12
            self._rot_x += (goal - self._rot_x) * _rate(0.05, dt)

        # туманность обновляем через кадр (она медленная), рендер — здесь, не в paintEvent
        if self._frame_idx % 2 == 0:
            self._render_nebula_layer()

        self.update()

    # ── Туманность ────────────────────────────────────────────────────
    def _nebula_side(self) -> int:
        base_r = min(self.width(), self.height()) * 0.375
        dpr = self.devicePixelRatioF()
        side = int(round(base_r * 2.12 * dpr / 32.0)) * 32
        return max(128, min(320, side))

    def _render_nebula_layer(self):
        side = self._nebula_side()
        if self._nebula_buf is None or self._nebula_buf.width() != side:
            self._nebula_buf = QImage(side, side, QImage.Format.Format_ARGB32_Premultiplied)
        buf = self._nebula_buf
        buf.fill(Qt.GlobalColor.transparent)

        bp = QPainter(buf)
        bp.setRenderHint(QPainter.RenderHint.Antialiasing)
        bp.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)

        bc = side * 0.5
        br = side * (70.0 / 150.0)

        clip = QPainterPath()
        clip.addEllipse(QRectF(bc - br, bc - br, br * 2.0, br * 2.0))
        bp.setClipPath(clip)
        bp.setPen(Qt.PenStyle.NoPen)

        energy = self._smooth_energy
        alpha_in = _clamp_alpha(62 + energy * 32)
        for b in self._geo.nebula_blobs:
            ang = b["ang"] + self._phase * b["speed"]
            dist = br * b["dist"] * (0.88 + 0.12 * math.sin(self._phase + b["phase"]))
            bx = bc + math.cos(ang) * dist
            by = bc + math.sin(ang) * dist * 0.88
            rad = br * b["rad"] * (1.0 + energy * 0.18)

            base = self._c_sec if b["is_sec"] else self._c_pri
            c_in = _rgba(base, alpha_in)
            c_out = _rgba(base, 0)

            g = QRadialGradient(QPointF(bx, by), rad)
            g.setColorAt(0.0, c_in)
            g.setColorAt(1.0, c_out)
            bp.setBrush(g)
            bp.drawEllipse(QPointF(bx, by), rad, rad)

        bp.end()

    # ── Отрисовка ─────────────────────────────────────────────────────
    def paintEvent(self, event):
        t0 = time.perf_counter()

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        w = float(self.width())
        h = float(self.height())
        cx = w * 0.5
        cy = h * 0.5
        base_r = min(w, h) * 0.375
        radius = base_r * (1.0 + self._smooth_energy * 0.055)

        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)

        # 1. Внешнее гало
        halo_r = radius * (1.48 + self._smooth_energy * 0.16)
        halo = QRadialGradient(QPointF(cx, cy), halo_r)
        halo.setColorAt(0.0, _rgba(self._c_pri, 72))
        halo.setColorAt(0.52, _rgba(self._c_sec, 38))
        halo.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(halo)
        p.drawEllipse(QPointF(cx, cy), halo_r, halo_r)

        # 2. Энергетические волны при разговоре
        if self._state == "speaking" or self._smooth_energy > 0.28:
            self._draw_energy_ripples(p, cx, cy, radius)

        # 3. Корональные магнитные петли
        self._draw_prominence_loops(p, cx, cy, radius)

        # 4. Туманность
        if self._nebula_buf is not None:
            target_rect = QRectF(cx - radius * 1.06, cy - radius * 1.06,
                                 radius * 2.12, radius * 2.12)
            p.drawImage(target_rect, self._nebula_buf)

        # 5. Сферическая оболочка Френеля
        shell = QRadialGradient(QPointF(cx, cy), radius)
        shell.setColorAt(0.0, _rgba(self._c_pri, 12))
        shell.setColorAt(0.74, _rgba(self._c_sec, 42))
        shell.setColorAt(0.955, _rgba(self._c_core, 175))
        shell.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setBrush(shell)
        p.drawEllipse(QPointF(cx, cy), radius, radius)

        # 6. Турбулентные 3D-филаменты
        self._draw_plasma_filaments(p, cx, cy, radius)

        # 7. Потоки микрочастиц
        self._draw_stream_particles(p, cx, cy, radius)

        # 8. Двойная сингулярность в центре
        self._draw_core_singularity(p, cx, cy, radius)

        if self.SHOW_FPS:
            self._draw_fps(p)

        p.end()

        ms = (time.perf_counter() - t0) * 1000.0
        self._paint_ms += (ms - self._paint_ms) * 0.1

    def _draw_fps(self, p: QPainter):
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        p.setPen(QColor(255, 255, 255, 200))
        p.drawText(8, 16, f"paint {self._paint_ms:.1f} ms  (~{1000.0 / max(self._paint_ms, 1e-3):.0f} fps max)")

    def _draw_energy_ripples(self, p: QPainter, cx: float, cy: float, radius: float):
        p.setBrush(Qt.BrushStyle.NoBrush)
        for k in range(3):
            prog = (self._phase * 0.55 + k * 0.33) % 1.0
            r = radius * (0.96 + prog * 0.52)
            alpha = _clamp_alpha((1.0 - prog) * 105 * self._smooth_energy)
            if alpha <= 2:
                continue
            col = _lerp_color(self._c_pri, self._c_sec, prog)
            col.setAlpha(alpha)
            p.setPen(QPen(col, 1.4))
            p.drawEllipse(QPointF(cx, cy), r, r)

    def _draw_prominence_loops(self, p: QPainter, cx: float, cy: float, radius: float):
        p.setBrush(Qt.BrushStyle.NoBrush)
        geo = self._geo
        xs, ys, pulse = geo.loops(self._phase, self._smooth_energy, cx, cy, radius)
        pulse_l = pulse.tolist()

        for i in range(LOOP_COUNT):
            pl = pulse_l[i]
            poly = _poly(xs[i], ys[i])
            base_col = self._c_sec if geo.loop_is_sec[i] else self._c_pri

            p.setPen(_pen(_rgba(base_col, 44 * pl), 4.2))
            p.drawPolyline(poly)

            c_mid = _lerp_color(base_col, self._c_core, 0.40)
            c_mid.setAlpha(_clamp_alpha(135 * pl))
            p.setPen(_pen(c_mid, 1.4))
            p.drawPolyline(poly)

    def _draw_plasma_filaments(self, p: QPainter, cx: float, cy: float, radius: float):
        p.setBrush(Qt.BrushStyle.NoBrush)
        geo = self._geo
        xs, ys = geo.filaments(self._phase, self._rot_x, self._rot_y, cx, cy, radius)

        energy = self._smooth_energy
        energy_boost = 0.25 + energy * 0.45

        # цвета одинаковы для всех филаментов одного типа — считаем один раз за кадр
        aura = {
            False: _rgba(self._c_pri, 38 + energy * 28),
            True: _rgba(self._c_sec, 38 + energy * 28),
        }
        main = {}
        for is_sec, base in ((False, self._c_pri), (True, self._c_sec)):
            c = _lerp_color(base, self._c_core, energy_boost)
            c.setAlpha(_clamp_alpha(105 + energy * 75))
            main[is_sec] = c
        hot_col = _rgba(self._c_core, 150 + energy * 85)

        for i in range(FIL_COUNT):
            poly = _poly(xs[i], ys[i])
            is_sec = geo.fil_is_sec[i]
            width = geo.fil_width[i]

            p.setPen(_pen(aura[is_sec], width * 2.6))
            p.drawPolyline(poly)

            p.setPen(_pen(main[is_sec], width))
            p.drawPolyline(poly)

            if geo.fil_hot[i]:
                p.setPen(_pen(hot_col, 0.75))
                p.drawPolyline(poly)

    def _draw_stream_particles(self, p: QPainter, cx: float, cy: float, radius: float):
        p.setPen(Qt.PenStyle.NoPen)
        geo = self._geo
        sx, sy, alpha, rad = geo.particles(self._phase, self._rot_x, self._rot_y, cx, cy, radius)

        sec = self._c_sec
        core = self._c_core
        rgb_sec = (sec.red(), sec.green(), sec.blue())
        rgb_core = (core.red(), core.green(), core.blue())

        is_sec = geo.p_is_sec
        for i, (x, y, a, r) in enumerate(zip(sx.tolist(), sy.tolist(), alpha.tolist(), rad.tolist())):
            rr, gg, bb = rgb_sec if is_sec[i] else rgb_core
            p.setBrush(QColor(rr, gg, bb, a))
            p.drawEllipse(QPointF(x, y), r, r)

    def _draw_core_singularity(self, p: QPainter, cx: float, cy: float, radius: float):
        pulse = 0.88 + 0.26 * math.sin(self._phase * 2.9) + self._smooth_energy * 0.52
        core_r = radius * 0.36 * pulse

        c_w = _rgba(self._c_core, 245)
        c_s = _rgba(self._c_sec, 155)
        c_p = _rgba(self._c_pri, 55)
        transparent = QColor(0, 0, 0, 0)

        p.setPen(Qt.PenStyle.NoPen)
        for sign in (-1.0, 1.0):
            ang = self._phase * 0.85 + (0.0 if sign > 0 else math.pi)
            ox = cx + math.cos(ang) * radius * 0.085
            oy = cy + math.sin(ang) * radius * 0.048

            g = QRadialGradient(QPointF(ox, oy), core_r)
            g.setColorAt(0.0, c_w)
            g.setColorAt(0.24, c_s)
            g.setColorAt(0.62, c_p)
            g.setColorAt(1.0, transparent)
            p.setBrush(g)
            p.drawEllipse(QPointF(ox, oy), core_r, core_r)

        pin_r = radius * 0.14 * pulse
        pin = QRadialGradient(QPointF(cx, cy), pin_r)
        pin.setColorAt(0.0, QColor(255, 255, 255, 250))
        pin.setColorAt(0.45, _rgba(self._c_core, 160))
        pin.setColorAt(1.0, transparent)
        p.setBrush(pin)
        p.drawEllipse(QPointF(cx, cy), pin_r, pin_r)
