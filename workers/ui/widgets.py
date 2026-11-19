# workers/ui/widgets.py
# Кастомные графические виджеты: карточка с подсветкой, индикатор волны,
# 28-полосный спектроанализатор музыки и компактные пузыри чата.

import math
import random
import threading
from datetime import datetime

from PySide6.QtCore import Qt, QTimer, QPointF, QRectF
from PySide6.QtGui import (
    QColor, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient,
)
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget


class ActivityWaveWidget(QWidget):
    """Мини-волна статуса ассистента в шапке окна."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(44, 18)
        self._phase = 0.0
        self._mode = "idle"
        self._color = QColor("#4ade80")

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
            "music": 0.28,
            "idle": 0.10,
        }.get(self._mode, 0.12)
        self._phase += speed
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        bars = 7
        bar_w = 3.2
        gap = 2.8
        total_w = bars * bar_w + (bars - 1) * gap
        start_x = (self.width() - total_w) / 2.0
        mid_y = self.height() / 2.0

        amp_scale = {
            "speaking": 1.0,
            "listening": 0.85,
            "music": 0.72,
            "idle": 0.22,
        }.get(self._mode, 0.25)

        p.setBrush(self._color)
        p.setPen(Qt.PenStyle.NoPen)

        for i in range(bars):
            wave = math.sin(self._phase + i * 0.85) * 0.5 + 0.5
            envelope = 1.0 - abs(i - (bars - 1) / 2.0) / ((bars + 1) / 2.0) * 0.45
            h = max(3.5, 3.5 + 11.5 * wave * amp_scale * envelope)
            x = start_x + i * (bar_w + gap)
            y = mid_y - h / 2.0
            p.drawRoundedRect(QRectF(x, y, bar_w, h), 1.6, 1.6)

        p.end()


class SpectrumVisualizerWidget(QWidget):
    """28-полосный спектроанализатор с пиками (peak-hold) и WASAPI Loopback FFT.

    Дополнительно отдаёт общий уровень звука (audio_level) для синхронизации
    губ аватара с реальной громкостью речи/музыки.
    """

    def __init__(self, num_bars: int = 28, parent=None):
        super().__init__(parent)
        self.setFixedHeight(48)
        self._num_bars = num_bars
        self._levels = [0.0] * num_bars
        self._peaks = [0.0] * num_bars
        self._peak_hold = [0] * num_bars
        self._fft_target = [0.0] * num_bars
        self._has_real_audio = False
        self._is_playing = False
        self._phase = 0.0
        self._accent = QColor("#6366f1")
        self._lock = threading.Lock()
        self._stop_evt = threading.Event()
        self._audio_level = 0.0

        self._start_loopback_capture()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)

    @property
    def audio_level(self) -> float:
        return self._audio_level

    def set_playing(self, playing: bool):
        self._is_playing = playing

    def set_accent_color(self, color: QColor):
        self._accent = color

    def shutdown(self):
        self._stop_evt.set()

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
                envelope = 1.0 - 0.35 * norm_x
                target = min(
                    1.0,
                    (0.45 * bass + 0.35 * mid + 0.20 * high) * envelope * random.uniform(0.75, 1.05),
                )

            if target > self._levels[i]:
                self._levels[i] += (target - self._levels[i]) * 0.55
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

        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = float(self.width())
        h = float(self.height())
        gap = 4.0
        bar_w = max(3.0, (w - gap * (self._num_bars - 1)) / self._num_bars)
        total_w = self._num_bars * bar_w + (self._num_bars - 1) * gap
        start_x = (w - total_w) / 2.0

        grad = QLinearGradient(0, h, 0, 0)
        c_bottom = QColor(self._accent)
        c_bottom.setAlpha(185)
        grad.setColorAt(0.0, c_bottom)
        grad.setColorAt(0.65, self._accent)
        grad.setColorAt(1.0, QColor("#22d3ee"))

        p.setPen(Qt.PenStyle.NoPen)

        for i in range(self._num_bars):
            x = start_x + i * (bar_w + gap)
            lvl = self._levels[i]
            bar_h = max(3.0, lvl * (h - 6.0))
            y = h - bar_h

            p.setBrush(QColor(51, 65, 85, 65))
            p.drawRoundedRect(QRectF(x, 4.0, bar_w, h - 4.0), bar_w / 2.0, bar_w / 2.0)

            p.setBrush(grad)
            p.drawRoundedRect(QRectF(x, y, bar_w, bar_h), bar_w / 2.0, bar_w / 2.0)

            pk = self._peaks[i]
            if pk > 0.04:
                pk_y = max(1.0, h - pk * (h - 6.0) - 3.5)
                p.setBrush(QColor(248, 250, 252, 210))
                p.drawRoundedRect(QRectF(x, pk_y, bar_w, 2.2), 1.0, 1.0)

        p.end()


class AmbientGlassCard(QFrame):
    """Стеклянная карточка плеера с радиальной подсветкой под цвет обложки."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("glassCard")
        self._accent = QColor("#6366f1")

    def set_accent_color(self, color: QColor):
        self._accent = color
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, 20, 20)
        p.setClipPath(path)

        p.fillRect(rect, QColor(22, 30, 46, 190))

        glow = QRadialGradient(
            QPointF(rect.width() * 0.5, rect.height() * 0.30),
            max(rect.width(), rect.height()) * 0.65,
        )
        c1 = QColor(self._accent)
        c1.setAlpha(72)
        c2 = QColor(self._accent)
        c2.setAlpha(15)
        glow.setColorAt(0.0, c1)
        glow.setColorAt(0.65, c2)
        glow.setColorAt(1.0, QColor(15, 23, 42, 0))
        p.fillRect(rect, glow)

        p.setClipping(False)
        p.setPen(QPen(QColor(148, 163, 184, 46), 1.0))
        p.drawRoundedRect(rect, 20, 20)
        p.end()


class ChatBubble(QFrame):
    """Компактный пузырь сообщения для нижней панели диалога."""

    def __init__(self, role: str, text: str, ts: float, meta: str = ""):
        super().__init__()
        is_user = role == "user"
        time_str = datetime.fromtimestamp(ts).strftime("%H:%M") if ts else ""

        outer = QHBoxLayout(self)
        outer.setContentsMargins(2, 1, 2, 1)

        bubble = QFrame()
        bubble.setObjectName("userBubble" if is_user else "botBubble")
        bubble.setMaximumWidth(400)
        b_layout = QVBoxLayout(bubble)
        b_layout.setContentsMargins(10, 5, 10, 5)
        b_layout.setSpacing(2)

        top_row = QHBoxLayout()
        top_row.setSpacing(6)
        sender_lbl = QLabel("Вы" if is_user else "Макс")
        sender_lbl.setStyleSheet(
            f"color: {'#c7d2fe' if is_user else '#38bdf8'}; "
            "font-size: 10px; font-weight: 700; background: transparent;"
        )
        top_row.addWidget(sender_lbl)
        top_row.addStretch(1)

        meta_map = {"voice": "голос", "voice_hot": "быстрая", "system": "текст"}
        nice_meta = meta_map.get(meta.lower(), meta) if meta else ""
        footer_parts = [p for p in (nice_meta, time_str) if p]
        if footer_parts:
            lbl_meta = QLabel(" · ".join(footer_parts))
            lbl_meta.setStyleSheet(
                "color: rgba(226, 232, 240, 0.48); font-size: 9px; background: transparent;"
            )
            top_row.addWidget(lbl_meta)

        b_layout.addLayout(top_row)

        lbl_text = QLabel(text)
        lbl_text.setWordWrap(True)
        lbl_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lbl_text.setStyleSheet("color: #f8fafc; font-size: 12px; background: transparent;")
        b_layout.addWidget(lbl_text)

        if is_user:
            outer.addStretch(1)
            outer.addWidget(bubble)
        else:
            outer.addWidget(bubble)
            outer.addStretch(1)