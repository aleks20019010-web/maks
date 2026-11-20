# workers/ui/widgets.py
# Неоново-космические виджеты: стеклянные карточки со шлейфами и скрипичным ключом,
# карточки пресетов EQ с подсветкой, сегментированный 24-полосный спектроанализатор и баблы чата.

import math
import random
import threading
from datetime import datetime

from PySide6.QtCore import Qt, QTimer, QPointF, QRectF, Signal
from PySide6.QtGui import (
    QColor, QFont, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient,
)
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget


class EqPresetTile(QWidget):
    """Стеклянная неоновая карточка пресета эквалайзера (иконка сверху + подпись снизу)."""

    clicked = Signal(int)

    def __init__(self, title: str, icon_kind: str, preset_id: int, parent=None):
        super().__init__(parent)
        self._title = title
        self._icon_kind = icon_kind
        self._preset_id = preset_id
        self._active = False
        self._hovered = False

        self.setMinimumHeight(52)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"Пресет эквалайзера: {title}")

        from workers.ui.theme import make_vector_icon
        self._pix_normal = make_vector_icon(icon_kind, 20, "#bae6fd").pixmap(20, 20)
        self._pix_active = make_vector_icon(icon_kind, 20, "#ffffff").pixmap(20, 20)

    @property
    def preset_id(self) -> int:
        return self._preset_id

    def set_active(self, active: bool):
        if self._active != active:
            self._active = active
            self.update()

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._preset_id)
        super().mousePressEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
        path = QPainterPath()
        path.addRoundedRect(rect, 13, 13)
        p.setClipPath(path)

        if self._active:
            grad = QLinearGradient(0, rect.top(), 0, rect.bottom())
            grad.setColorAt(0.0, QColor(56, 189, 248, 175))
            grad.setColorAt(0.55, QColor(37, 99, 235, 165))
            grad.setColorAt(1.0, QColor(79, 70, 229, 175))
            p.fillRect(rect, grad)

            glow = QRadialGradient(QPointF(rect.center().x(), rect.top() + 14.0), rect.width() * 0.65)
            glow.setColorAt(0.0, QColor(224, 242, 254, 95))
            glow.setColorAt(1.0, QColor(56, 189, 248, 0))
            p.fillRect(rect, glow)
        elif self._hovered:
            p.fillRect(rect, QColor(24, 52, 112, 195))
        else:
            p.fillRect(rect, QColor(12, 25, 56, 178))

        p.setClipping(False)

        if self._active:
            p.setPen(QPen(QColor(186, 230, 253, 240), 1.6))
        elif self._hovered:
            p.setPen(QPen(QColor(125, 211, 252, 190), 1.2))
        else:
            p.setPen(QPen(QColor(56, 189, 248, 95), 1.0))
        p.drawRoundedRect(rect, 13, 13)

        # Иконка по центру верхней половины
        pix = self._pix_active if self._active else self._pix_normal
        ix = round((self.width() - 20) / 2.0)
        iy = 7
        p.drawPixmap(ix, iy, pix)

        # Подпись снизу
        font = QFont("Segoe UI", 8)
        font.setBold(self._active)
        p.setFont(font)
        p.setPen(QColor("#ffffff") if self._active else QColor("#cbd5e1"))
        text_rect = QRectF(2.0, 29.0, self.width() - 4.0, 18.0)
        p.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, self._title)

        p.end()


class ActivityWaveWidget(QWidget):
    """Неоновая мини-волна статуса ассистента в шапке окна."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(52, 20)
        self._phase = 0.0
        self._mode = "idle"
        self._color = QColor("#38bdf8")

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._step)
        self._timer.start(45)

    def set_state(self, mode: str, color: str):
        self._mode = mode
        self._color = QColor(color)

    def _step(self):
        speed = {
            "speaking": 0.42,
            "listening": 0.34,
            "curious": 0.30,
            "music": 0.28,
            "idle": 0.12,
        }.get(self._mode, 0.14)
        self._phase += speed
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        bars = 9
        bar_w = 2.8
        gap = 2.4
        total_w = bars * bar_w + (bars - 1) * gap
        start_x = (self.width() - total_w) / 2.0
        mid_y = self.height() / 2.0

        amp_scale = {
            "speaking": 1.0,
            "listening": 0.85,
            "curious": 0.78,
            "music": 0.75,
            "idle": 0.32,
        }.get(self._mode, 0.32)

        p.setBrush(self._color)
        p.setPen(Qt.PenStyle.NoPen)

        for i in range(bars):
            wave = math.sin(self._phase + i * 0.85) * 0.5 + 0.5
            envelope = 1.0 - abs(i - (bars - 1) / 2.0) / ((bars + 1) / 2.0) * 0.55
            h = max(3.5, 3.5 + 13.0 * wave * amp_scale * envelope)
            x = start_x + i * (bar_w + gap)
            y = mid_y - h / 2.0
            p.drawRoundedRect(QRectF(x, y, bar_w, h), 1.4, 1.4)

        p.end()


class SpectrumVisualizerWidget(QWidget):
    """Сегментированный неоновый спектроанализатор (бирюзовый -> синий -> фиолетовый)."""

    def __init__(self, num_bars: int = 24, compact: bool = False, parent=None):
        super().__init__(parent)
        self._compact = compact
        self.setFixedHeight(36 if compact else 64)
        self._num_bars = num_bars
        self._levels = [0.0] * num_bars
        self._peaks = [0.0] * num_bars
        self._peak_hold = [0] * num_bars
        self._fft_target = [0.0] * num_bars
        self._has_real_audio = False
        self._is_playing = False
        self._phase = 0.0
        self._accent = QColor("#38bdf8")
        self._lock = threading.Lock()
        self._stop_evt = threading.Event()
        self._audio_level = 0.0
        self._bass_avg = 0.0
        self._on_spectrum = None
        self._mirror_widget: SpectrumVisualizerWidget | None = None

        if not compact:
            self._start_loopback_capture()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)

    @property
    def audio_level(self) -> float:
        return self._audio_level

    def set_mirror_visualizer(self, other: "SpectrumVisualizerWidget"):
        self._mirror_widget = other
        other._num_bars = self._num_bars
        other._levels = [0.0] * self._num_bars
        other._peaks = [0.0] * self._num_bars

    def set_spectrum_callback(self, cb):
        self._on_spectrum = cb

    def set_playing(self, playing: bool):
        self._is_playing = playing
        if self._mirror_widget is not None:
            self._mirror_widget._is_playing = playing

    def set_accent_color(self, color: QColor):
        self._accent = color

    def shutdown(self):
        self._stop_evt.set()

    def get_music_features(self) -> tuple[float, float, float, float, float]:
        n = self._num_bars
        b_end = max(1, n // 4)
        m_end = max(b_end + 1, (n * 3) // 4)
        bass = sum(self._levels[:b_end]) / b_end
        mid = sum(self._levels[b_end:m_end]) / max(1, m_end - b_end)
        high = sum(self._levels[m_end:]) / max(1, n - m_end)

        self._bass_avg += (bass - self._bass_avg) * 0.12
        beat = max(0.0, min(1.0, (bass - self._bass_avg) * 3.8))
        mood = max(0.0, min(1.0, bass * 0.55 + beat * 0.45 + high * 0.35))
        return float(bass), float(mid), float(high), float(beat), float(mood)

    def _start_loopback_capture(self):
        def worker():
            try:
                import warnings
                import numpy as np
                import soundcard as sc
                from soundcard import SoundcardRuntimeWarning

                warnings.filterwarnings("ignore", category=SoundcardRuntimeWarning)

                speaker = sc.default_speaker()
                mic = sc.get_microphone(id=str(speaker.name), include_loopback=True)
                sr = 44100
                block = 2048
                window = np.hanning(block)

                freqs = np.fft.rfftfreq(block, 1.0 / sr)
                edges = np.geomspace(45, 14000, self._num_bars + 1)
                bins = []
                for i in range(self._num_bars):
                    idx = np.where((freqs >= edges[i]) & (freqs < edges[i + 1]))[0]
                    if len(idx) == 0:
                        idx = np.array([min(len(freqs) - 1, int(edges[i] / (sr / block)))])
                    bins.append(idx)

                self._has_real_audio = True
                with mic.recorder(samplerate=sr, channels=1, blocksize=block) as rec:
                    while not self._stop_evt.is_set():
                        data = rec.record(numframes=block)
                        if data is None or len(data) < block:
                            continue
                        mono = data[:, 0] if data.ndim > 1 else data
                        mag = np.abs(np.fft.rfft(mono * window)) / (block * 0.12)

                        bands = []
                        for i, idx in enumerate(bins):
                            val = float(np.mean(mag[idx]))
                            boost = 0.85 + (i / self._num_bars) * 1.35
                            bands.append(min(1.0, (val * boost) ** 0.65))

                        rms = float(np.sqrt(np.mean(mono ** 2)))
                        level = min(1.0, (rms * 14.0) ** 0.75)

                        with self._lock:
                            self._fft_target = bands
                            self._audio_level = level
            except Exception:
                self._has_real_audio = False

        threading.Thread(target=worker, daemon=True, name="ui-spectrum").start()

    def _tick(self):
        if self._compact:
            return
        self._phase += 0.24
        with self._lock:
            targets = list(self._fft_target) if self._has_real_audio else None

        for i in range(self._num_bars):
            if not self._is_playing:
                target = 0.0
            elif targets is not None:
                target = targets[i]
            else:
                norm_x = i / max(1, self._num_bars - 1)
                bass = math.sin(self._phase * 1.3 + norm_x * 2.5) * 0.5 + 0.5
                mid = math.sin(self._phase * 2.1 - i * 0.45) * 0.5 + 0.5
                high = math.cos(self._phase * 3.0 + i * 0.8) * 0.5 + 0.5
                envelope = 1.0 - 0.30 * norm_x
                target = min(
                    1.0,
                    (0.45 * bass + 0.35 * mid + 0.20 * high) * envelope * random.uniform(0.75, 1.05),
                )

            if target > self._levels[i]:
                self._levels[i] += (target - self._levels[i]) * 0.58
            else:
                self._levels[i] = max(0.0, self._levels[i] * 0.78)

            if self._levels[i] >= self._peaks[i]:
                self._peaks[i] = self._levels[i]
                self._peak_hold[i] = 10
            else:
                if self._peak_hold[i] > 0:
                    self._peak_hold[i] -= 1
                else:
                    self._peaks[i] = max(0.0, self._peaks[i] - 0.035)

        if self._mirror_widget is not None:
            self._mirror_widget._levels = list(self._levels)
            self._mirror_widget._peaks = list(self._peaks)
            self._mirror_widget.update()

        if self._is_playing and self._on_spectrum is not None:
            try:
                self._on_spectrum(*self.get_music_features())
            except Exception:
                pass

        self.update()

    @staticmethod
    def _bar_color(t: float, alpha: int = 240) -> QColor:
        if t < 0.5:
            k = t * 2.0
            r = round(34 + (59 - 34) * k)
            g = round(211 + (130 - 211) * k)
            b = round(238 + (246 - 238) * k)
        else:
            k = (t - 0.5) * 2.0
            r = round(59 + (168 - 59) * k)
            g = round(130 + (85 - 130) * k)
            b = round(246 + (247 - 246) * k)
        return QColor(r, g, b, alpha)

    def paintEvent(self, event):
        p = QPainter(self)
        try:
            levels = self._levels
            peaks = self._peaks
            n_bars = min(self._num_bars, len(levels))
            if n_bars <= 0:
                return

            w = float(self.width())
            h = float(self.height())
            gap = 3.0
            bar_w = max(4.0, (w - gap * (n_bars - 1)) / n_bars)
            total_w = n_bars * bar_w + (n_bars - 1) * gap
            start_x = (w - total_w) / 2.0

            seg_h = 2.6 if self._compact else 3.2
            seg_gap = 1.6
            max_segs = max(4, int((h - 4.0) / (seg_h + seg_gap)))

            p.setPen(Qt.PenStyle.NoPen)

            for i in range(n_bars):
                x = start_x + i * (bar_w + gap)
                t = i / max(1, n_bars - 1)
                lvl = levels[i]
                active_segs = max(1 if self._is_playing else 0, int(round(lvl * max_segs)))

                col_on = self._bar_color(t, 240)
                col_off = QColor(30, 58, 110, 48)

                for s in range(max_segs):
                    y = h - (s + 1) * (seg_h + seg_gap)
                    if y < 1.0:
                        break
                    p.setBrush(col_on if s < active_segs else col_off)
                    p.drawRoundedRect(QRectF(x, y, bar_w, seg_h), 1.0, 1.0)

                pk = peaks[i] if i < len(peaks) else 0.0
                if pk > 0.05:
                    pk_seg = min(max_segs - 1, int(round(pk * max_segs)))
                    pk_y = max(1.0, h - (pk_seg + 1) * (seg_h + seg_gap))
                    p.setBrush(QColor(224, 242, 254, 220))
                    p.drawRoundedRect(QRectF(x, pk_y, bar_w, seg_h), 1.0, 1.0)
        finally:
            p.end()


class CosmicBackdropWidget(QWidget):
    """Корневой контейнер окна с неоново-космическим фоном, шлейфами и скрипичным ключом."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("rootWidget")
        self._bg_pm: QPixmap | None = None
        self._bg_size: tuple[int, int] = (0, 0)

    def resizeEvent(self, event):
        self._bg_size = (0, 0)
        super().resizeEvent(event)

    def _build_cosmic_bg(self, w: int, h: int) -> QPixmap:
        img = QImage(max(1, w), max(1, h), QImage.Format.Format_ARGB32_Premultiplied)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        bg = QLinearGradient(0, 0, w, h)
        bg.setColorAt(0.0, QColor("#04091a"))
        bg.setColorAt(0.45, QColor("#071330"))
        bg.setColorAt(1.0, QColor("#050a1c"))
        p.fillRect(0, 0, w, h, bg)

        for cx_k, cy_k, rad_k, col in (
            (0.28, 0.22, 0.55, QColor(14, 165, 233, 45)),
            (0.82, 0.25, 0.50, QColor(139, 92, 246, 52)),
            (0.50, 0.46, 0.60, QColor(37, 99, 235, 42)),
            (0.85, 0.72, 0.45, QColor(99, 102, 241, 48)),
        ):
            r = max(w, h) * rad_k
            g = QRadialGradient(QPointF(w * cx_k, h * cy_k), r)
            g.setColorAt(0.0, col)
            g.setColorAt(1.0, QColor(0, 0, 0, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(g)
            p.drawEllipse(QPointF(w * cx_k, h * cy_k), r, r)

        rng = random.Random(777)
        for _ in range(95):
            sx = rng.uniform(0, w)
            sy = rng.uniform(0, h)
            sr = rng.uniform(0.6, 1.8)
            sa = rng.randint(60, 210)
            p.setBrush(QColor(186, 230, 253, sa))
            p.drawEllipse(QPointF(sx, sy), sr, sr)

        p.setBrush(Qt.BrushStyle.NoBrush)
        for i in range(4):
            path = QPainterPath()
            y0 = h * (0.18 + i * 0.21)
            path.moveTo(-20, y0)
            path.cubicTo(
                w * 0.32, y0 - 75 + i * 18,
                w * 0.68, y0 + 85 - i * 15,
                w + 20, y0 - 30,
            )
            c_glow = QColor(56, 189, 248, 34) if i % 2 == 0 else QColor(168, 85, 247, 34)
            p.setPen(QPen(c_glow, 7.0))
            p.drawPath(path)
            c_core = QColor(125, 211, 252, 95) if i % 2 == 0 else QColor(192, 132, 252, 95)
            p.setPen(QPen(c_core, 1.3))
            p.drawPath(path)

        kx = w * 0.87
        ky = h * 0.71
        clef = QPainterPath()
        clef.moveTo(kx, ky + 48)
        clef.cubicTo(kx - 16, ky + 48, kx - 18, ky + 32, kx - 6, ky + 32)
        clef.cubicTo(kx + 4, ky + 32, kx + 4, ky + 44, kx - 4, ky + 44)
        clef.moveTo(kx, ky + 48)
        clef.lineTo(kx + 4, ky - 42)
        clef.cubicTo(kx + 18, ky - 36, kx + 18, ky - 14, kx - 4, ky - 4)
        clef.cubicTo(kx - 24, ky + 10, kx - 20, ky + 34, kx + 4, ky + 32)
        clef.cubicTo(kx + 22, ky + 30, kx + 22, ky + 8, kx + 2, ky + 8)

        p.setPen(QPen(QColor(56, 189, 248, 45), 7.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawPath(clef)
        p.setPen(QPen(QColor(186, 230, 253, 145), 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawPath(clef)

        p.end()
        return QPixmap.fromImage(img)

    def paintEvent(self, event):
        w, h = self.width(), self.height()
        if self._bg_pm is None or self._bg_size != (w, h):
            self._bg_size = (w, h)
            self._bg_pm = self._build_cosmic_bg(w, h)

        p = QPainter(self)
        p.drawPixmap(0, 0, self._bg_pm)
        p.setPen(QPen(QColor(56, 189, 248, 130), 1.5))
        p.drawRoundedRect(QRectF(1.0, 1.0, w - 2.0, h - 2.0), 18, 18)
        p.end()


class AmbientGlassCard(QFrame):
    """Стеклянная карточка с неоновой рамкой и мягкой радиальной подсветкой."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("glassCard")
        self._accent = QColor("#38bdf8")

    def set_accent_color(self, color: QColor):
        self._accent = color
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, 18, 18)
        p.setClipPath(path)

        p.fillRect(rect, QColor(10, 20, 44, 178))

        glow = QRadialGradient(
            QPointF(rect.width() * 0.25, rect.height() * 0.28),
            max(rect.width(), rect.height()) * 0.65,
        )
        c1 = QColor(self._accent)
        c1.setAlpha(68)
        c2 = QColor("#6366f1")
        c2.setAlpha(22)
        glow.setColorAt(0.0, c1)
        glow.setColorAt(0.65, c2)
        glow.setColorAt(1.0, QColor(10, 20, 44, 0))
        p.fillRect(rect, glow)

        p.setClipping(False)
        p.setPen(QPen(QColor(56, 189, 248, 125), 1.2))
        p.drawRoundedRect(rect, 18, 18)
        p.end()


class _NeonAvatarBadge(QLabel):
    """Круглая неоновая аватарка слева от сообщения в ленте диалога."""

    def __init__(self, kind: str, parent=None):
        super().__init__(parent)
        self.setFixedSize(32, 32)
        self._kind = kind

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        c = QPointF(16.0, 16.0)
        if self._kind == "bot":
            ring = QColor("#a855f7")
            fill = QColor(49, 24, 92, 210)
        elif self._kind == "disc":
            ring = QColor("#38bdf8")
            fill = QColor(15, 45, 92, 210)
        else:
            ring = QColor("#7dd3fc")
            fill = QColor(20, 52, 102, 210)

        p.setBrush(fill)
        p.setPen(QPen(ring, 1.6))
        p.drawEllipse(c, 14.0, 14.0)

        p.setPen(QPen(QColor("#ffffff"), 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.setBrush(Qt.BrushStyle.NoBrush)

        if self._kind == "bot":
            p.drawArc(QRectF(9.0, 9.0, 14.0, 14.0), 0, 180 * 16)
            p.setBrush(QColor("#e9d5ff"))
            p.drawRoundedRect(QRectF(8.0, 15.0, 3.2, 6.5), 1.5, 1.5)
            p.drawRoundedRect(QRectF(20.8, 15.0, 3.2, 6.5), 1.5, 1.5)
        elif self._kind == "disc":
            p.drawEllipse(c, 8.0, 8.0)
            p.drawEllipse(c, 2.5, 2.5)
        else:
            p.setBrush(QColor("#e0f2fe"))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(16.0, 12.2), 3.6, 3.6)
            path = QPainterPath()
            path.moveTo(9.5, 22.5)
            path.cubicTo(9.5, 17.5, 22.5, 17.5, 22.5, 22.5)
            path.closeSubpath()
            p.drawPath(path)

        p.end()


class ChatBubble(QFrame):
    """Пузырь сообщения с круглой неоновой аватаркой слева."""

    def __init__(self, role: str, text: str, ts: float, meta: str = ""):
        super().__init__()
        is_user = role == "user"
        is_event = (not is_user) and (
            meta.lower() in ("event", "track", "music")
            or text.startswith("Начато воспроизведение")
        )
        time_str = datetime.fromtimestamp(ts).strftime("%H:%M") if ts else ""

        outer = QHBoxLayout(self)
        outer.setContentsMargins(2, 2, 2, 2)
        outer.setSpacing(8)

        badge_kind = "user" if is_user else ("disc" if is_event else "bot")
        badge = _NeonAvatarBadge(badge_kind)
        outer.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)

        bubble = QFrame()
        bubble.setObjectName("userBubble" if is_user else "botBubble")
        b_layout = QVBoxLayout(bubble)
        b_layout.setContentsMargins(12, 6, 12, 7)
        b_layout.setSpacing(2)

        top_row = QHBoxLayout()
        top_row.setSpacing(6)

        if not is_user and not is_event:
            sender_lbl = QLabel("Макс")
            sender_lbl.setStyleSheet(
                "color: #38bdf8; font-size: 11px; font-weight: 700; background: transparent;"
            )
            top_row.addWidget(sender_lbl)

        top_row.addStretch(1)

        if time_str:
            lbl_time = QLabel(time_str)
            lbl_time.setStyleSheet(
                "color: rgba(203, 213, 225, 0.72); font-size: 10px; background: transparent;"
            )
            top_row.addWidget(lbl_time)

        if (not is_user and not is_event) or time_str:
            b_layout.addLayout(top_row)

        lbl_text = QLabel(text)
        lbl_text.setWordWrap(True)
        lbl_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lbl_text.setStyleSheet("color: #f8fafc; font-size: 12px; background: transparent;")
        b_layout.addWidget(lbl_text)

        outer.addWidget(bubble, 1)