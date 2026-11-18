# workers/ui/theme.py
# Векторные иконки, обработка обложек, извлечение акцентного цвета и стили приложения.

import os
from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import (
    QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
)
from shared import config as cfg


def extract_accent_color(pix: QPixmap) -> QColor:
    """Вычислить насыщенный акцентный цвет из обложки для фонового свечения."""
    if pix.isNull():
        return QColor("#6366f1")
    img = pix.scaled(
        24, 24,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.FastTransformation,
    ).toImage()

    r_sum = g_sum = b_sum = weight_sum = 0.0
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            _, s, v, _ = c.getHsvF()
            w = (0.2 + s * 1.8) * (1.0 - abs(v - 0.55))
            r_sum += c.red() * w
            g_sum += c.green() * w
            b_sum += c.blue() * w
            weight_sum += w

    if weight_sum <= 0:
        return QColor("#6366f1")

    color = QColor(
        int(min(255, r_sum / weight_sum)),
        int(min(255, g_sum / weight_sum)),
        int(min(255, b_sum / weight_sum)),
    )
    h, s, v, _ = color.getHsvF()
    color.setHsvF(h, max(0.45, min(0.85, s)), max(0.45, min(0.78, v)))
    return color


def make_vector_icon(kind: str, size: int = 22, color: str = "#f8fafc") -> QIcon:
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)

    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    c = QColor(color)

    if kind == "play":
        p.setBrush(c)
        p.setPen(Qt.PenStyle.NoPen)
        path = QPainterPath()
        path.moveTo(size * 0.34, size * 0.22)
        path.lineTo(size * 0.78, size * 0.50)
        path.lineTo(size * 0.34, size * 0.78)
        path.closeSubpath()
        p.drawPath(path)

    elif kind == "pause":
        p.setBrush(c)
        p.setPen(Qt.PenStyle.NoPen)
        bar_w = size * 0.16
        bar_h = size * 0.54
        y = size * 0.23
        p.drawRoundedRect(QRectF(size * 0.28, y, bar_w, bar_h), 2.0, 2.0)
        p.drawRoundedRect(QRectF(size * 0.56, y, bar_w, bar_h), 2.0, 2.0)

    elif kind == "next":
        p.setBrush(c)
        p.setPen(Qt.PenStyle.NoPen)
        path = QPainterPath()
        path.moveTo(size * 0.24, size * 0.24)
        path.lineTo(size * 0.62, size * 0.50)
        path.lineTo(size * 0.24, size * 0.76)
        path.closeSubpath()
        p.drawPath(path)
        p.drawRoundedRect(QRectF(size * 0.68, size * 0.24, size * 0.10, size * 0.52), 1.5, 1.5)

    elif kind == "prev":
        p.setBrush(c)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(size * 0.22, size * 0.24, size * 0.10, size * 0.52), 1.5, 1.5)
        path = QPainterPath()
        path.moveTo(size * 0.76, size * 0.24)
        path.lineTo(size * 0.38, size * 0.50)
        path.lineTo(size * 0.76, size * 0.76)
        path.closeSubpath()
        p.drawPath(path)

    elif kind == "like":
        pen = QPen(c, 1.8)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        w, h = float(size), float(size)
        path = QPainterPath()
        path.moveTo(w * 0.50, h * 0.78)
        path.cubicTo(w * 0.14, h * 0.54, w * 0.18, h * 0.22, w * 0.36, h * 0.24)
        path.cubicTo(w * 0.44, h * 0.25, w * 0.48, h * 0.31, w * 0.50, h * 0.35)
        path.cubicTo(w * 0.52, h * 0.31, w * 0.56, h * 0.25, w * 0.64, h * 0.24)
        path.cubicTo(w * 0.82, h * 0.22, w * 0.86, h * 0.54, w * 0.50, h * 0.78)
        p.drawPath(path)

    elif kind == "dislike":
        pen = QPen(c, 1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawLine(QPointF(size * 0.28, size * 0.28), QPointF(size * 0.72, size * 0.72))
        p.drawLine(QPointF(size * 0.72, size * 0.28), QPointF(size * 0.28, size * 0.72))

    elif kind == "send":
        p.setBrush(c)
        p.setPen(Qt.PenStyle.NoPen)
        path = QPainterPath()
        path.moveTo(size * 0.20, size * 0.22)
        path.lineTo(size * 0.84, size * 0.50)
        path.lineTo(size * 0.20, size * 0.78)
        path.lineTo(size * 0.34, size * 0.50)
        path.closeSubpath()
        p.drawPath(path)

    elif kind == "volume":
        p.setBrush(c)
        p.setPen(Qt.PenStyle.NoPen)
        path = QPainterPath()
        path.moveTo(size * 0.18, size * 0.38)
        path.lineTo(size * 0.34, size * 0.38)
        path.lineTo(size * 0.52, size * 0.22)
        path.lineTo(size * 0.52, size * 0.78)
        path.lineTo(size * 0.34, size * 0.62)
        path.lineTo(size * 0.18, size * 0.62)
        path.closeSubpath()
        p.drawPath(path)

        pen = QPen(c, 1.7)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(QRectF(size * 0.44, size * 0.32, size * 0.28, size * 0.36), -45 * 16, 90 * 16)
        p.drawArc(QRectF(size * 0.42, size * 0.20, size * 0.44, size * 0.60), -45 * 16, 90 * 16)

    p.end()
    return QIcon(pix)


def make_rounded_pixmap(pix: QPixmap, size: int, radius: int = 22) -> QPixmap:
    scaled = pix.scaled(
        size, size,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    x = (scaled.width() - size) // 2
    y = (scaled.height() - size) // 2
    cropped = scaled.copy(x, y, size, size)

    out = QPixmap(size, size)
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(0, 0, size, size, radius, radius)
    painter.setClipPath(path)
    painter.drawPixmap(0, 0, cropped)
    painter.end()
    return out


def make_default_cover(size: int) -> QPixmap:
    out = QPixmap(size, size)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    path = QPainterPath()
    path.addRoundedRect(0, 0, size, size, 22, 22)
    p.setClipPath(path)

    grad = QLinearGradient(0, 0, size, size)
    grad.setColorAt(0.0, QColor("#1e293b"))
    grad.setColorAt(0.5, QColor("#312e81"))
    grad.setColorAt(1.0, QColor("#0f172a"))
    p.fillRect(0, 0, size, size, grad)

    center = QPointF(size / 2, size / 2)
    p.setPen(QPen(QColor(165, 180, 252, 35), 1.2))
    p.drawEllipse(center, size * 0.36, size * 0.36)
    p.drawEllipse(center, size * 0.24, size * 0.24)

    p.setBrush(QColor("#a5b4fc"))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(QPointF(size * 0.44, size * 0.60), 8.5, 6.5)
    p.drawRoundedRect(QRectF(size * 0.48, size * 0.34, 3.6, size * 0.26), 1.5, 1.5)
    p.drawRoundedRect(QRectF(size * 0.48, size * 0.34, 14.0, 4.5), 1.5, 1.5)

    p.end()
    return out


def _make_fallback_vector_app_icon() -> QIcon:
    icon = QIcon()
    for s in (16, 24, 32, 48, 64, 128):
        pix = QPixmap(s, s)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        grad = QLinearGradient(0, 0, s, s)
        grad.setColorAt(0.0, QColor("#4f46e5"))
        grad.setColorAt(0.55, QColor("#2563eb"))
        grad.setColorAt(1.0, QColor("#06b6d4"))

        margin = s * 0.06
        rect = QRectF(margin, margin, s - margin * 2, s - margin * 2)
        p.setBrush(grad)
        p.setPen(QPen(QColor(255, 255, 255, 55), max(1.0, s * 0.025)))
        p.drawRoundedRect(rect, s * 0.26, s * 0.26)

        pen = QPen(QColor("#ffffff"), max(1.6, s * 0.085))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)

        path = QPainterPath()
        path.moveTo(s * 0.26, s * 0.68)
        path.lineTo(s * 0.26, s * 0.34)
        path.lineTo(s * 0.50, s * 0.55)
        path.lineTo(s * 0.74, s * 0.34)
        path.lineTo(s * 0.74, s * 0.68)
        p.drawPath(path)

        p.end()
        icon.addPixmap(pix)
    return icon


def make_app_icon() -> QIcon:
    assets_dir = os.path.join(cfg.BASE_DIR, "assets")
    src = QPixmap()
    for name in ("icon.jpg", "icon.jpeg", "icon.png"):
        path = os.path.join(assets_dir, name)
        if os.path.exists(path):
            src = QPixmap(path)
            if not src.isNull():
                break

    if src.isNull():
        return _make_fallback_vector_app_icon()

    icon = QIcon()
    for s in (16, 24, 32, 48, 64, 128):
        icon.addPixmap(make_rounded_pixmap(src, s, radius=max(3, int(s * 0.24))))
    return icon


APP_STYLESHEET = """
    QWidget#rootWidget {
        background: qlineargradient(
            x1:0, y1:0, x2:1, y2:1,
            stop:0 #070b14, stop:0.5 #0d1526, stop:1 #080d19
        );
        font-family: "Segoe UI", sans-serif;
    }
    QFrame#glassCard {
        background-color: rgba(22, 30, 46, 0.74);
        border: 1px solid rgba(148, 163, 184, 0.16);
        border-radius: 20px;
    }
    QLabel#modeBadge {
        background-color: rgba(99, 102, 241, 0.22);
        color: #c7d2fe;
        border: 1px solid rgba(129, 140, 248, 0.35);
        border-radius: 10px;
        padding: 2px 10px;
        font-size: 11px;
        font-weight: 600;
    }
    QLabel#sourceBadge {
        background-color: rgba(14, 165, 233, 0.18);
        color: #7dd3fc;
        border: 1px solid rgba(56, 189, 248, 0.30);
        border-radius: 10px;
        padding: 2px 9px;
        font-size: 11px;
        font-weight: 600;
    }
    QLabel#trackTitle {
        color: #f8fafc;
        font-size: 18px;
        font-weight: 700;
        background: transparent;
        padding-top: 2px;
    }
    QLabel#trackArtist {
        color: #94a3b8;
        font-size: 13px;
        background: transparent;
    }
    QPushButton#playCircleBtn {
        background: qlineargradient(
            x1:0, y1:0, x2:1, y2:1,
            stop:0 #6366f1, stop:1 #3b82f6
        );
        border: 1px solid rgba(199, 210, 254, 0.50);
        border-radius: 27px;
    }
    QPushButton#playCircleBtn:hover {
        background: qlineargradient(
            x1:0, y1:0, x2:1, y2:1,
            stop:0 #818cf8, stop:1 #60a5fa
        );
    }
    QPushButton#playCircleBtn:pressed {
        background-color: #4f46e5;
    }
    QPushButton#iconBtn {
        background-color: rgba(51, 65, 85, 0.58);
        border: 1px solid rgba(148, 163, 184, 0.18);
        border-radius: 21px;
    }
    QPushButton#iconBtn:hover {
        background-color: rgba(71, 85, 105, 0.85);
        border-color: rgba(165, 180, 252, 0.45);
    }
    QPushButton#iconBtnSubtle {
        background-color: rgba(30, 41, 59, 0.48);
        border: 1px solid rgba(148, 163, 184, 0.14);
        border-radius: 18px;
    }
    QPushButton#iconBtnSubtle:hover {
        background-color: rgba(51, 65, 85, 0.72);
        border-color: rgba(148, 163, 184, 0.30);
    }
    QPushButton#chipBtn {
        background-color: rgba(30, 41, 59, 0.75);
        color: #cbd5e1;
        border: 1px solid rgba(148, 163, 184, 0.20);
        border-radius: 10px;
        padding: 2px 8px;
        font-size: 10px;
        font-weight: 500;
    }
    QPushButton#chipBtn:hover {
        background-color: rgba(79, 70, 229, 0.38);
        color: #ffffff;
        border-color: rgba(129, 140, 248, 0.48);
    }
    QPushButton#sendBtn {
        background: qlineargradient(
            x1:0, y1:0, x2:1, y2:1,
            stop:0 #6366f1, stop:1 #4f46e5
        );
        border: 1px solid rgba(165, 180, 252, 0.42);
        border-radius: 16px;
    }
    QPushButton#sendBtn:hover {
        background-color: #818cf8;
    }
    QComboBox#eqCombo {
        background-color: rgba(30, 41, 59, 0.78);
        color: #e2e8f0;
        border: 1px solid rgba(148, 163, 184, 0.22);
        border-radius: 10px;
        padding: 3px 10px;
        font-size: 11px;
        font-weight: 600;
        min-width: 124px;
    }
    QComboBox#eqCombo:hover {
        border-color: rgba(129, 140, 248, 0.50);
    }
    QComboBox#eqCombo::drop-down {
        border: none;
        width: 18px;
    }
    QComboBox#eqCombo QAbstractItemView {
        background-color: #0f172a;
        color: #f8fafc;
        border: 1px solid rgba(129, 140, 248, 0.35);
        selection-background-color: #4f46e5;
    }
    QSlider#trackSlider::groove:horizontal {
        height: 5px;
        background: rgba(51, 65, 85, 0.85);
        border-radius: 2px;
    }
    QSlider#trackSlider::sub-page:horizontal {
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #6366f1, stop:1 #22d3ee);
        border-radius: 2px;
    }
    QSlider#trackSlider::handle:horizontal {
        width: 11px;
        height: 11px;
        margin: -3px 0;
        border-radius: 5px;
        background: #f8fafc;
    }
    QSlider#volSlider::groove:horizontal {
        height: 4px;
        background: rgba(51, 65, 85, 0.75);
        border-radius: 2px;
    }
    QSlider#volSlider::sub-page:horizontal {
        background: rgba(148, 163, 184, 0.85);
        border-radius: 2px;
    }
    QSlider#volSlider::handle:horizontal {
        width: 10px;
        height: 10px;
        margin: -3px 0;
        border-radius: 5px;
        background: #e2e8f0;
    }
    QScrollArea#chatScroll {
        border: none;
        background: transparent;
    }
    QFrame#userBubble {
        background-color: rgba(79, 70, 229, 0.46);
        border: 1px solid rgba(129, 140, 248, 0.35);
        border-radius: 12px;
    }
    QFrame#botBubble {
        background-color: rgba(30, 41, 59, 0.88);
        border: 1px solid rgba(148, 163, 184, 0.18);
        border-radius: 12px;
    }
    QLineEdit#chatInput {
        background-color: rgba(15, 23, 42, 0.82);
        color: #f8fafc;
        border: 1px solid rgba(148, 163, 184, 0.24);
        border-radius: 16px;
        padding: 0 12px;
        height: 32px;
        font-size: 12px;
    }
    QLineEdit#chatInput:focus {
        border-color: #6366f1;
    }
    QScrollBar:vertical {
        background: transparent;
        width: 5px;
    }
    QScrollBar::handle:vertical {
        background: rgba(148, 163, 184, 0.3);
        border-radius: 2px;
    }
"""