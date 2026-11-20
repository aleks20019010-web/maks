# workers/ui/theme.py
# Векторные иконки, обработка обложек, извлечение акцентного цвета и неоново-космические стили.

import math
import os
from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import (
    QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
)
from shared import config as cfg


def extract_accent_color(pix: QPixmap) -> QColor:
    """Вычислить насыщенный акцентный цвет из обложки для фонового свечения."""
    if pix.isNull():
        return QColor("#38bdf8")
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
        return QColor("#38bdf8")

    color = QColor(
        int(min(255, r_sum / weight_sum)),
        int(min(255, g_sum / weight_sum)),
        int(min(255, b_sum / weight_sum)),
    )
    h, s, v, _ = color.getHsvF()
    color.setHsvF(h, max(0.50, min(0.88, s)), max(0.50, min(0.85, v)))
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

    elif kind == "shuffle":
        pen = QPen(c, 1.7)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(QPointF(size * 0.20, size * 0.32), QPointF(size * 0.42, size * 0.32))
        p.drawLine(QPointF(size * 0.42, size * 0.32), QPointF(size * 0.62, size * 0.68))
        p.drawLine(QPointF(size * 0.62, size * 0.68), QPointF(size * 0.80, size * 0.68))

        p.drawLine(QPointF(size * 0.20, size * 0.68), QPointF(size * 0.42, size * 0.68))
        p.drawLine(QPointF(size * 0.42, size * 0.68), QPointF(size * 0.62, size * 0.32))
        p.drawLine(QPointF(size * 0.62, size * 0.32), QPointF(size * 0.80, size * 0.32))

        p.drawLine(QPointF(size * 0.72, size * 0.24), QPointF(size * 0.80, size * 0.32))
        p.drawLine(QPointF(size * 0.72, size * 0.40), QPointF(size * 0.80, size * 0.32))
        p.drawLine(QPointF(size * 0.72, size * 0.60), QPointF(size * 0.80, size * 0.68))
        p.drawLine(QPointF(size * 0.72, size * 0.76), QPointF(size * 0.80, size * 0.68))

    elif kind == "like":
        pen = QPen(c, 1.8)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(size * 0.22, size * 0.44, size * 0.14, size * 0.34), 2.5, 2.5)
        path = QPainterPath()
        path.moveTo(size * 0.36, size * 0.48)
        path.lineTo(size * 0.48, size * 0.24)
        path.cubicTo(size * 0.54, size * 0.22, size * 0.56, size * 0.30, size * 0.53, size * 0.42)
        path.lineTo(size * 0.74, size * 0.42)
        path.cubicTo(size * 0.80, size * 0.42, size * 0.80, size * 0.50, size * 0.76, size * 0.54)
        path.lineTo(size * 0.73, size * 0.72)
        path.cubicTo(size * 0.71, size * 0.77, size * 0.66, size * 0.78, size * 0.58, size * 0.78)
        path.lineTo(size * 0.36, size * 0.78)
        p.drawPath(path)

    elif kind == "dislike":
        pen = QPen(c, 1.8)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(size * 0.64, size * 0.22, size * 0.14, size * 0.34), 2.5, 2.5)
        path = QPainterPath()
        path.moveTo(size * 0.64, size * 0.52)
        path.lineTo(size * 0.52, size * 0.76)
        path.cubicTo(size * 0.46, size * 0.78, size * 0.44, size * 0.70, size * 0.47, size * 0.58)
        path.lineTo(size * 0.26, size * 0.58)
        path.cubicTo(size * 0.20, size * 0.58, size * 0.20, size * 0.50, size * 0.24, size * 0.46)
        path.lineTo(size * 0.27, size * 0.28)
        path.cubicTo(size * 0.29, size * 0.23, size * 0.34, size * 0.22, size * 0.42, size * 0.22)
        path.lineTo(size * 0.64, size * 0.22)
        p.drawPath(path)

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

    elif kind == "settings":
        pen = QPen(c, 1.7)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        ctr = QPointF(size * 0.5, size * 0.5)
        p.drawEllipse(ctr, size * 0.16, size * 0.16)
        p.drawEllipse(ctr, size * 0.30, size * 0.30)

    elif kind == "eq":
        pen = QPen(c, 1.7)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        for idx, x_k in enumerate((0.28, 0.50, 0.72)):
            p.drawLine(QPointF(size * x_k, size * 0.22), QPointF(size * x_k, size * 0.78))
            y_knob = size * (0.36 if idx == 0 else (0.62 if idx == 1 else 0.44))
            p.setBrush(c)
            p.drawEllipse(QPointF(size * x_k, y_knob), 2.4, 2.4)

    elif kind == "note":
        p.setBrush(c)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(size * 0.36, size * 0.68), size * 0.13, size * 0.10)
        p.drawEllipse(QPointF(size * 0.68, size * 0.62), size * 0.13, size * 0.10)
        p.drawRoundedRect(QRectF(size * 0.45, size * 0.26, size * 0.07, size * 0.42), 1.5, 1.5)
        p.drawRoundedRect(QRectF(size * 0.77, size * 0.20, size * 0.07, size * 0.42), 1.5, 1.5)
        path = QPainterPath()
        path.moveTo(size * 0.45, size * 0.26)
        path.lineTo(size * 0.84, size * 0.20)
        path.lineTo(size * 0.84, size * 0.29)
        path.lineTo(size * 0.45, size * 0.35)
        path.closeSubpath()
        p.drawPath(path)

    elif kind == "star":
        p.setBrush(c)
        p.setPen(Qt.PenStyle.NoPen)
        path = QPainterPath()
        cx, cy = size * 0.5, size * 0.5
        r_out, r_in = size * 0.37, size * 0.16
        for i in range(10):
            ang = -math.pi / 2 + i * math.pi / 5
            r = r_out if i % 2 == 0 else r_in
            pt = QPointF(cx + math.cos(ang) * r, cy + math.sin(ang) * r)
            if i == 0:
                path.moveTo(pt)
            else:
                path.lineTo(pt)
        path.closeSubpath()
        p.drawPath(path)

    elif kind == "rock":
        # Жест «коза» / рок
        pen = QPen(c, 1.7)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(size * 0.30, size * 0.46, size * 0.40, size * 0.32), 4.0, 4.0)
        p.drawRoundedRect(QRectF(size * 0.30, size * 0.20, size * 0.11, size * 0.28), 3.0, 3.0)
        p.drawRoundedRect(QRectF(size * 0.59, size * 0.22, size * 0.11, size * 0.26), 3.0, 3.0)
        p.drawLine(QPointF(size * 0.41, size * 0.46), QPointF(size * 0.59, size * 0.46))

    elif kind == "sax":
        # Саксофон для джаза
        pen = QPen(c, 1.9)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        path = QPainterPath()
        path.moveTo(size * 0.36, size * 0.22)
        path.lineTo(size * 0.48, size * 0.26)
        path.lineTo(size * 0.48, size * 0.66)
        path.cubicTo(size * 0.48, size * 0.78, size * 0.68, size * 0.78, size * 0.68, size * 0.60)
        path.lineTo(size * 0.74, size * 0.54)
        p.drawPath(path)

    elif kind == "wave":
        pen = QPen(c, 1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        heights = (0.20, 0.46, 0.30, 0.56, 0.24)
        for i, hk in enumerate(heights):
            x = size * (0.22 + i * 0.14)
            p.drawLine(QPointF(x, size * (0.5 - hk * 0.5)), QPointF(x, size * (0.5 + hk * 0.5)))

    elif kind == "bars":
        p.setBrush(c)
        p.setPen(Qt.PenStyle.NoPen)
        heights = (0.34, 0.58, 0.44, 0.64)
        for i, hk in enumerate(heights):
            x = size * (0.22 + i * 0.15)
            h_bar = size * hk
            p.drawRoundedRect(QRectF(x, size * 0.78 - h_bar, size * 0.09, h_bar), 1.5, 1.5)

    elif kind == "user":
        p.setBrush(c)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(size * 0.5, size * 0.35), size * 0.16, size * 0.16)
        path = QPainterPath()
        path.moveTo(size * 0.24, size * 0.76)
        path.cubicTo(size * 0.24, size * 0.56, size * 0.76, size * 0.56, size * 0.76, size * 0.76)
        path.closeSubpath()
        p.drawPath(path)

    elif kind == "weather":
        # Солнце + облако для команды «Погода»
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#fbbf24"))
        p.drawEllipse(QPointF(size * 0.38, size * 0.38), size * 0.16, size * 0.16)
        p.setBrush(c)
        p.drawEllipse(QPointF(size * 0.44, size * 0.56), size * 0.16, size * 0.14)
        p.drawEllipse(QPointF(size * 0.62, size * 0.58), size * 0.14, size * 0.12)
        p.drawRoundedRect(QRectF(size * 0.30, size * 0.54, size * 0.44, size * 0.16), 5.0, 5.0)

    elif kind == "radio":
        # Радиовышка / волны для команды «Радио»
        pen = QPen(c, 1.7)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        ctr = QPointF(size * 0.5, size * 0.46)
        p.drawArc(QRectF(size * 0.20, size * 0.18, size * 0.60, size * 0.56), 35 * 16, 110 * 16)
        p.drawArc(QRectF(size * 0.30, size * 0.28, size * 0.40, size * 0.36), 35 * 16, 110 * 16)
        p.setBrush(c)
        p.drawEllipse(ctr, 2.5, 2.5)
        p.drawLine(QPointF(size * 0.5, size * 0.48), QPointF(size * 0.5, size * 0.78))

    p.end()
    return QIcon(pix)


def make_rounded_pixmap(pix: QPixmap, size: int, radius: int = 16) -> QPixmap:
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
    path.addRoundedRect(0, 0, size, size, 16, 16)
    p.setClipPath(path)

    grad = QLinearGradient(0, 0, size, size)
    grad.setColorAt(0.0, QColor("#0f172a"))
    grad.setColorAt(0.5, QColor("#1e3a8a"))
    grad.setColorAt(1.0, QColor("#312e81"))
    p.fillRect(0, 0, size, size, grad)

    center = QPointF(size / 2, size / 2)
    p.setPen(QPen(QColor(56, 189, 248, 55), 1.4))
    p.drawEllipse(center, size * 0.36, size * 0.36)
    p.drawEllipse(center, size * 0.24, size * 0.24)

    p.setBrush(QColor("#7dd3fc"))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(QPointF(size * 0.44, size * 0.60), 7.5, 5.5)
    p.drawRoundedRect(QRectF(size * 0.48, size * 0.34, 3.2, size * 0.26), 1.5, 1.5)
    p.drawRoundedRect(QRectF(size * 0.48, size * 0.34, 12.0, 4.0), 1.5, 1.5)

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
        background-color: #050a18;
        font-family: "Segoe UI", sans-serif;
    }
    QFrame#glassCard {
        background-color: rgba(11, 21, 46, 0.72);
        border: 1px solid rgba(56, 189, 248, 0.45);
        border-radius: 18px;
    }
    QLabel#modeBadge {
        background-color: rgba(15, 29, 64, 0.85);
        color: #e0f2fe;
        border: 1px solid rgba(56, 189, 248, 0.55);
        border-radius: 12px;
        padding: 3px 12px;
        font-size: 11px;
        font-weight: 600;
    }
    QLabel#sourceBadge {
        background-color: rgba(14, 116, 144, 0.45);
        color: #bae6fd;
        border: 1px solid rgba(56, 189, 248, 0.65);
        border-radius: 12px;
        padding: 3px 12px;
        font-size: 11px;
        font-weight: 600;
    }
    QLabel#qualityBadge {
        background-color: rgba(15, 29, 64, 0.82);
        color: #cbd5e1;
        border: 1px solid rgba(56, 189, 248, 0.40);
        border-radius: 11px;
        padding: 2px 10px;
        font-size: 11px;
        font-weight: 500;
    }
    QLabel#trackTitle {
        color: #ffffff;
        font-size: 19px;
        font-weight: 700;
        background: transparent;
    }
    QLabel#trackArtist {
        color: #cbd5e1;
        font-size: 13px;
        font-weight: 500;
        background: transparent;
    }
    QLabel#trackAlbum {
        color: #94a3b8;
        font-size: 11px;
        background: transparent;
    }
    QPushButton#playCircleBtn {
        background: qlineargradient(
            x1:0, y1:0, x2:1, y2:1,
            stop:0 #2563eb, stop:0.5 #4f46e5, stop:1 #7c3aed
        );
        border: 2px solid #7dd3fc;
        border-radius: 30px;
    }
    QPushButton#playCircleBtn:hover {
        background: qlineargradient(
            x1:0, y1:0, x2:1, y2:1,
            stop:0 #3b82f6, stop:0.5 #6366f1, stop:1 #8b5cf6
        );
        border-color: #ffffff;
    }
    QPushButton#playCircleBtn:pressed {
        background-color: #1d4ed8;
    }
    QPushButton#iconBtn {
        background-color: rgba(15, 31, 68, 0.82);
        border: 1.5px solid rgba(56, 189, 248, 0.65);
        border-radius: 21px;
    }
    QPushButton#iconBtn:hover {
        background-color: rgba(30, 58, 138, 0.85);
        border-color: #bae6fd;
    }
    QPushButton#iconBtnLike {
        background-color: rgba(15, 31, 68, 0.82);
        border: 1.5px solid rgba(56, 189, 248, 0.70);
        border-radius: 21px;
    }
    QPushButton#iconBtnLike:hover {
        background-color: rgba(14, 116, 144, 0.75);
        border-color: #7dd3fc;
    }
    QPushButton#iconBtnDislike {
        background-color: rgba(45, 18, 38, 0.72);
        border: 1.5px solid rgba(244, 63, 94, 0.70);
        border-radius: 21px;
    }
    QPushButton#iconBtnDislike:hover {
        background-color: rgba(136, 19, 55, 0.70);
        border-color: #fda4af;
    }
    QPushButton#iconBtnSubtle {
        background-color: rgba(15, 31, 68, 0.72);
        border: 1px solid rgba(129, 140, 248, 0.45);
        border-radius: 17px;
    }
    QPushButton#iconBtnSubtle:hover {
        background-color: rgba(49, 46, 129, 0.75);
        border-color: #a5b4fc;
    }
    QPushButton#chipBtn {
        background-color: rgba(15, 29, 64, 0.85);
        color: #e2e8f0;
        border: 1px solid rgba(56, 189, 248, 0.52);
        border-radius: 12px;
        padding: 3px 11px;
        font-size: 11px;
        font-weight: 600;
    }
    QPushButton#chipBtn:hover {
        background-color: rgba(37, 99, 235, 0.55);
        color: #ffffff;
        border-color: #7dd3fc;
    }
    QPushButton#quickCmdChip {
        background: qlineargradient(
            x1:0, y1:0, x2:1, y2:1,
            stop:0 rgba(18, 38, 82, 0.88), stop:1 rgba(30, 27, 75, 0.88)
        );
        color: #e0f2fe;
        border: 1px solid rgba(56, 189, 248, 0.48);
        border-radius: 11px;
        padding: 3px 9px;
        font-size: 10px;
        font-weight: 600;
    }
    QPushButton#quickCmdChip:hover {
        background: qlineargradient(
            x1:0, y1:0, x2:1, y2:1,
            stop:0 rgba(37, 99, 235, 0.72), stop:1 rgba(109, 40, 217, 0.72)
        );
        color: #ffffff;
        border: 1px solid #7dd3fc;
    }
    QPushButton#quickCmdChip:pressed {
        background-color: #1d4ed8;
    }
    QPushButton#sendBtn {
        background: qlineargradient(
            x1:0, y1:0, x2:1, y2:1,
            stop:0 #38bdf8, stop:1 #2563eb
        );
        border: 1.5px solid #bae6fd;
        border-radius: 18px;
    }
    QPushButton#sendBtn:hover {
        background: qlineargradient(
            x1:0, y1:0, x2:1, y2:1,
            stop:0 #7dd3fc, stop:1 #3b82f6
        );
    }
    QComboBox#eqCombo {
        background-color: rgba(15, 29, 64, 0.85);
        color: #e2e8f0;
        border: 1px solid rgba(56, 189, 248, 0.48);
        border-radius: 12px;
        padding: 3px 10px;
        font-size: 11px;
        font-weight: 600;
        min-width: 110px;
    }
    QComboBox#eqCombo:hover {
        border-color: #7dd3fc;
    }
    QComboBox#eqCombo::drop-down {
        border: none;
        width: 18px;
    }
    QComboBox#eqCombo QAbstractItemView {
        background-color: #09132b;
        color: #f8fafc;
        border: 1px solid #38bdf8;
        selection-background-color: #2563eb;
    }
    QSlider#trackSlider::groove:horizontal {
        height: 6px;
        background: rgba(30, 41, 75, 0.90);
        border: 1px solid rgba(56, 189, 248, 0.25);
        border-radius: 3px;
    }
    QSlider#trackSlider::sub-page:horizontal {
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #2563eb, stop:0.6 #38bdf8, stop:1 #a5f3fc);
        border-radius: 3px;
    }
    QSlider#trackSlider::handle:horizontal {
        width: 14px;
        height: 14px;
        margin: -5px 0;
        border-radius: 7px;
        background: #ffffff;
        border: 2px solid #38bdf8;
    }
    QSlider#volSlider::groove:horizontal {
        height: 5px;
        background: rgba(30, 41, 75, 0.90);
        border-radius: 2px;
    }
    QSlider#volSlider::sub-page:horizontal {
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #2563eb, stop:1 #38bdf8);
        border-radius: 2px;
    }
    QSlider#volSlider::handle:horizontal {
        width: 12px;
        height: 12px;
        margin: -4px 0;
        border-radius: 6px;
        background: #ffffff;
        border: 2px solid #38bdf8;
    }
    QScrollArea#chatScroll {
        border: none;
        background: transparent;
    }
    QFrame#userBubble {
        background-color: rgba(23, 45, 96, 0.78);
        border: 1px solid rgba(56, 189, 248, 0.45);
        border-radius: 14px;
    }
    QFrame#botBubble {
        background-color: rgba(15, 29, 64, 0.84);
        border: 1px solid rgba(129, 140, 248, 0.42);
        border-radius: 14px;
    }
    QLineEdit#chatInput {
        background-color: rgba(9, 19, 43, 0.85);
        color: #f8fafc;
        border: 1.5px solid rgba(56, 189, 248, 0.48);
        border-radius: 18px;
        padding: 0 14px;
        height: 36px;
        font-size: 12px;
    }
    QLineEdit#chatInput:focus {
        border-color: #7dd3fc;
    }
    QScrollBar:vertical {
        background: transparent;
        width: 5px;
    }
    QScrollBar::handle:vertical {
        background: rgba(56, 189, 248, 0.4);
        border-radius: 2px;
    }
"""