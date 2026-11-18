# workers/ui/main.py
# Десктоп-окно Макса (PySide6):
#   - Окно разделено 50/50 по высоте:
#       Верхняя половина: статус Макса, центрированная обложка, название/артист,
#       полоса прогресса со временем и современные векторные кнопки управления.
#       Нижняя половина: лента чата команд и ответов + ввод текста.
#   - Иконка из assets/icon.jpg (.jpeg/.png) в панели задач, окне и трее.
#   - Сворачивание в системный трей при закрытии окна.

import hashlib
import json
import os
import signal
import sys
import threading
import time
from datetime import datetime

if os.name == "nt":
    os.system("chcp 65001 > nul")

import requests
from PySide6.QtCore import Qt, QTimer, Signal, QObject, QPointF, QRectF, QSize
from PySide6.QtGui import (
    QAction, QColor, QFont, QIcon, QLinearGradient,
    QPainter, QPainterPath, QPen, QPixmap,
)
from PySide6.QtWidgets import (
    QApplication, QFrame, QGraphicsDropShadowEffect, QHBoxLayout,
    QLabel, QLineEdit, QMainWindow, QMenu, QPushButton,
    QScrollArea, QSlider, QSystemTrayIcon, QVBoxLayout, QWidget,
)

from shared import config as cfg
from shared import ipc
from shared import protocol
from shared import runtime_state as rt
from shared.log import log
from shared.sources import CommandSource
from shared.ui_bridge import CHAT_LOG_FILE, read_music_ui


COVER_CACHE_DIR = os.path.join(cfg.CACHE_DIR, "covers")
COVER_SIZE = 148


def _fmt_ms(ms: int) -> str:
    if ms <= 0:
        return "00:00"
    sec = ms // 1000
    m, s = divmod(sec, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _mode_label(mode: str) -> str:
    mapping = {
        "wave": "Моя волна",
        "local_search": "Поиск",
        "local_search_next": "Похожее",
        "single": "Трек",
        "artist_only": "Исполнитель",
        "genre": "Жанр / Настроение",
        "radio": "Радио",
        "liked": "Моя музыка",
        "local_liked": "Локальные лайки",
    }
    return mapping.get(mode, mode or "Ожидание")


# ── Векторные иконки для кнопок плеера ────────────────────────────────
def _make_vector_icon(kind: str, size: int = 22, color: str = "#f8fafc") -> QIcon:
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

    p.end()
    return QIcon(pix)


# ── Обложки и иконка приложения / трея ────────────────────────────────
def _make_rounded_pixmap(pix: QPixmap, size: int, radius: int = 20) -> QPixmap:
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


def _make_default_cover(size: int = COVER_SIZE) -> QPixmap:
    out = QPixmap(size, size)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    path = QPainterPath()
    path.addRoundedRect(0, 0, size, size, 20, 20)
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
    p.drawEllipse(QPointF(size * 0.44, size * 0.60), 7.5, 5.5)
    p.drawRoundedRect(QRectF(size * 0.48, size * 0.34, 3.2, size * 0.26), 1.5, 1.5)
    p.drawRoundedRect(QRectF(size * 0.48, size * 0.34, 13.0, 4.0), 1.5, 1.5)

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


def _make_app_icon() -> QIcon:
    """Загрузить иконку из assets/icon.(jpg|jpeg|png) с округлением углов."""
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
        icon.addPixmap(_make_rounded_pixmap(src, s, radius=max(3, int(s * 0.24))))
    return icon


def _extract_id3_cover(file_path: str) -> bytes | None:
    if not file_path or not os.path.exists(file_path):
        return None
    try:
        from mutagen import File as MutagenFile
        audio = MutagenFile(file_path)
        if audio is None or not getattr(audio, "tags", None):
            return None
        for tag in audio.tags.values():
            if hasattr(tag, "data") and isinstance(tag.data, (bytes, bytearray)):
                return bytes(tag.data)
    except Exception:
        pass
    return None


def _fetch_fallback_cover(artist: str, title: str) -> bytes | None:
    """Поиск обложки через Deezer / iTunes API, если нет в ID3 и Яндексе."""
    query = f"{artist} {title}".strip()
    if not query:
        return None

    try:
        r = requests.get(
            "https://api.deezer.com/search",
            params={"q": query, "limit": 1},
            timeout=3.5,
        )
        if r.status_code == 200:
            items = r.json().get("data") or []
            if items:
                album = items[0].get("album") or {}
                img_url = album.get("cover_big") or album.get("cover_medium")
                if img_url:
                    img_r = requests.get(img_url, timeout=3.5)
                    if img_r.status_code == 200 and len(img_r.content) > 256:
                        return img_r.content
    except Exception:
        pass

    try:
        r = requests.get(
            "https://itunes.apple.com/search",
            params={"term": query, "media": "music", "entity": "song", "limit": 1},
            timeout=3.5,
        )
        if r.status_code == 200:
            results = r.json().get("results") or []
            if results:
                art = results[0].get("artworkUrl100", "")
                if art:
                    img_url = art.replace("100x100bb", "400x400bb")
                    img_r = requests.get(img_url, timeout=3.5)
                    if img_r.status_code == 200 and len(img_r.content) > 256:
                        return img_r.content
    except Exception:
        pass

    return None


class CoverSignals(QObject):
    loaded = Signal(str, QPixmap)


class ChatBubble(QFrame):
    def __init__(self, role: str, text: str, ts: float, meta: str = ""):
        super().__init__()
        is_user = role == "user"
        time_str = datetime.fromtimestamp(ts).strftime("%H:%M") if ts else ""

        outer = QHBoxLayout(self)
        outer.setContentsMargins(4, 2, 4, 2)

        bubble = QFrame()
        bubble.setObjectName("userBubble" if is_user else "botBubble")
        bubble.setMaximumWidth(370)
        b_layout = QVBoxLayout(bubble)
        b_layout.setContentsMargins(12, 8, 12, 8)
        b_layout.setSpacing(3)

        lbl_text = QLabel(text)
        lbl_text.setWordWrap(True)
        lbl_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lbl_text.setStyleSheet("color: #f8fafc; font-size: 13px; background: transparent;")
        b_layout.addWidget(lbl_text)

        footer_parts = [p for p in (time_str, meta) if p]
        if footer_parts:
            lbl_meta = QLabel(" · ".join(footer_parts))
            lbl_meta.setStyleSheet("color: rgba(226, 232, 240, 0.50); font-size: 10px; background: transparent;")
            lbl_meta.setAlignment(Qt.AlignmentFlag.AlignRight if is_user else Qt.AlignmentFlag.AlignLeft)
            b_layout.addWidget(lbl_meta)

        if is_user:
            outer.addStretch(1)
            outer.addWidget(bubble)
        else:
            outer.addWidget(bubble)
            outer.addStretch(1)


class MaksMainWindow(QMainWindow):
    def __init__(self, app_icon: QIcon):
        super().__init__()
        self._app_icon = app_icon
        self.setWindowTitle("Макс — Ассистент")
        self.setWindowIcon(self._app_icon)
        self.setMinimumSize(460, 740)
        self.resize(500, 820)

        self._cover_key = ""
        self._default_cover = _make_default_cover(COVER_SIZE)
        self._cover_signals = CoverSignals()
        self._cover_signals.loaded.connect(self._on_cover_loaded)

        self._icon_play = _make_vector_icon("play", 22, "#ffffff")
        self._icon_pause = _make_vector_icon("pause", 22, "#ffffff")

        self._chat_offset = 0
        self._is_playing = False
        self._dur_ms = 0
        self._pos_ms = 0

        self._build_ui()
        self._apply_styles()
        self._setup_tray()
        self._load_initial_chat()

        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_state)
        self._poll_timer.start(250)

        self._tick_timer = QTimer(self)
        self._tick_timer.timeout.connect(self._tick_progress)
        self._tick_timer.start(200)

    def _build_ui(self):
        root = QWidget()
        root.setObjectName("rootWidget")
        self.setCentralWidget(root)

        main_layout = QVBoxLayout(root)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(14)

        # ══════════════════════════════════════════════════════════════
        # ВЕРХНЯЯ ПОЛОВИНА (50%): СТАТУС МАКСА + ЦЕНТРИРОВАННЫЙ ПЛЕЕР
        # ══════════════════════════════════════════════════════════════
        player_card = QFrame()
        player_card.setObjectName("glassCard")
        p_layout = QVBoxLayout(player_card)
        p_layout.setContentsMargins(20, 14, 20, 16)
        p_layout.setSpacing(8)

        status_row = QHBoxLayout()
        self.lbl_status_dot = QLabel("●")
        self.lbl_status_dot.setStyleSheet("color: #4ade80; font-size: 12px; background: transparent;")
        self.lbl_status_text = QLabel("Макс слушает («Макс...»)")
        self.lbl_status_text.setStyleSheet("color: #cbd5e1; font-size: 12px; font-weight: 600; background: transparent;")
        self.lbl_mode_badge = QLabel("Ожидание")
        self.lbl_mode_badge.setObjectName("modeBadge")

        status_row.addWidget(self.lbl_status_dot)
        status_row.addWidget(self.lbl_status_text)
        status_row.addStretch(1)
        status_row.addWidget(self.lbl_mode_badge)
        p_layout.addLayout(status_row)

        p_layout.addStretch(1)

        # 1. Сверху по центру — обложка трека
        cover_row = QHBoxLayout()
        cover_row.addStretch(1)
        self.lbl_cover = QLabel()
        self.lbl_cover.setFixedSize(COVER_SIZE, COVER_SIZE)
        self.lbl_cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_cover.setPixmap(self._default_cover)
        self.lbl_cover.setStyleSheet("background: transparent;")

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(0, 0, 0, 150))
        self.lbl_cover.setGraphicsEffect(shadow)

        cover_row.addWidget(self.lbl_cover)
        cover_row.addStretch(1)
        p_layout.addLayout(cover_row)

        # 2. Под обложкой по центру — название и исполнитель
        self.lbl_title = QLabel("Ничего не играет")
        self.lbl_title.setObjectName("trackTitle")
        self.lbl_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_title.setWordWrap(True)

        self.lbl_artist = QLabel("Скажите: «Макс, включи музыку»")
        self.lbl_artist.setObjectName("trackArtist")
        self.lbl_artist.setAlignment(Qt.AlignmentFlag.AlignCenter)

        p_layout.addWidget(self.lbl_title)
        p_layout.addWidget(self.lbl_artist)

        # 3. Под названием — полоса прогресса и время
        prog_col = QVBoxLayout()
        prog_col.setSpacing(4)
        prog_col.setContentsMargins(8, 4, 8, 2)

        self.progress = QSlider(Qt.Orientation.Horizontal)
        self.progress.setObjectName("trackSlider")
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setEnabled(False)
        prog_col.addWidget(self.progress)

        time_row = QHBoxLayout()
        self.lbl_pos = QLabel("00:00")
        self.lbl_dur = QLabel("00:00")
        self.lbl_pos.setStyleSheet("color: #94a3b8; font-size: 11px; font-weight: 500; background: transparent;")
        self.lbl_dur.setStyleSheet("color: #94a3b8; font-size: 11px; font-weight: 500; background: transparent;")
        time_row.addWidget(self.lbl_pos)
        time_row.addStretch(1)
        time_row.addWidget(self.lbl_dur)
        prog_col.addLayout(time_row)
        p_layout.addLayout(prog_col)

        # 4. Под полосой времени — современные векторные кнопки управления
        ctrl_row = QHBoxLayout()
        ctrl_row.setSpacing(14)
        ctrl_row.addStretch(1)

        self.btn_dislike = QPushButton()
        self.btn_dislike.setObjectName("iconBtnSubtle")
        self.btn_dislike.setIcon(_make_vector_icon("dislike", 18, "#94a3b8"))
        self.btn_dislike.setIconSize(QSize(18, 18))
        self.btn_dislike.setFixedSize(36, 36)
        self.btn_dislike.setToolTip("Не нравится (пропустить)")

        self.btn_prev = QPushButton()
        self.btn_prev.setObjectName("iconBtn")
        self.btn_prev.setIcon(_make_vector_icon("prev", 20, "#e2e8f0"))
        self.btn_prev.setIconSize(QSize(20, 20))
        self.btn_prev.setFixedSize(42, 42)
        self.btn_prev.setToolTip("Предыдущий трек")

        self.btn_play = QPushButton()
        self.btn_play.setObjectName("playCircleBtn")
        self.btn_play.setIcon(self._icon_play)
        self.btn_play.setIconSize(QSize(24, 24))
        self.btn_play.setFixedSize(52, 52)
        self.btn_play.setToolTip("Пауза / Продолжить")

        self.btn_next = QPushButton()
        self.btn_next.setObjectName("iconBtn")
        self.btn_next.setIcon(_make_vector_icon("next", 20, "#e2e8f0"))
        self.btn_next.setIconSize(QSize(20, 20))
        self.btn_next.setFixedSize(42, 42)
        self.btn_next.setToolTip("Следующий трек")

        self.btn_like = QPushButton()
        self.btn_like.setObjectName("iconBtnSubtle")
        self.btn_like.setIcon(_make_vector_icon("like", 19, "#f43f5e"))
        self.btn_like.setIconSize(QSize(19, 19))
        self.btn_like.setFixedSize(36, 36)
        self.btn_like.setToolTip("Нравится")

        for b in (self.btn_dislike, self.btn_prev, self.btn_play, self.btn_next, self.btn_like):
            b.setCursor(Qt.CursorShape.PointingHandCursor)

        self.btn_dislike.clicked.connect(lambda: self._send_music_ctrl("dislike"))
        self.btn_prev.clicked.connect(lambda: self._send_music_ctrl("prev"))
        self.btn_play.clicked.connect(self._toggle_play_pause)
        self.btn_next.clicked.connect(lambda: self._send_music_ctrl("next"))
        self.btn_like.clicked.connect(lambda: self._send_brain_cmd("нравится этот трек"))

        ctrl_row.addWidget(self.btn_dislike)
        ctrl_row.addWidget(self.btn_prev)
        ctrl_row.addWidget(self.btn_play)
        ctrl_row.addWidget(self.btn_next)
        ctrl_row.addWidget(self.btn_like)
        ctrl_row.addStretch(1)
        p_layout.addLayout(ctrl_row)

        p_layout.addStretch(1)

        # ══════════════════════════════════════════════════════════════
        # НИЖНЯЯ ПОЛОВИНА (50%): ЧАТ КОМАНД И ОТВЕТОВ
        # ══════════════════════════════════════════════════════════════
        chat_card = QFrame()
        chat_card.setObjectName("glassCard")
        c_layout = QVBoxLayout(chat_card)
        c_layout.setContentsMargins(14, 12, 14, 14)
        c_layout.setSpacing(10)

        chat_header = QLabel("Диалог")
        chat_header.setStyleSheet("color: #94a3b8; font-size: 12px; font-weight: 600; padding-left: 4px; background: transparent;")
        c_layout.addWidget(chat_header)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("chatScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.chat_container = QWidget()
        self.chat_container.setStyleSheet("background: transparent;")
        self.chat_layout = QVBoxLayout(self.chat_container)
        self.chat_layout.setContentsMargins(2, 2, 2, 2)
        self.chat_layout.setSpacing(6)
        self.chat_layout.addStretch(1)

        self.scroll.setWidget(self.chat_container)
        c_layout.addWidget(self.scroll, 1)

        input_row = QHBoxLayout()
        input_row.setSpacing(8)

        self.input_box = QLineEdit()
        self.input_box.setObjectName("chatInput")
        self.input_box.setPlaceholderText("Написать команду Максу...")
        self.input_box.returnPressed.connect(self._on_send_input)

        btn_send = QPushButton()
        btn_send.setObjectName("sendBtn")
        btn_send.setIcon(_make_vector_icon("send", 18, "#ffffff"))
        btn_send.setIconSize(QSize(18, 18))
        btn_send.setFixedSize(38, 38)
        btn_send.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_send.clicked.connect(self._on_send_input)

        input_row.addWidget(self.input_box, 1)
        input_row.addWidget(btn_send)
        c_layout.addLayout(input_row)

        main_layout.addWidget(player_card, 1)
        main_layout.addWidget(chat_card, 1)

    def _apply_styles(self):
        self.setStyleSheet("""
            QWidget#rootWidget {
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:1,
                    stop:0 #080c14, stop:0.5 #0f172a, stop:1 #090e1a
                );
                font-family: "Segoe UI", sans-serif;
            }
            QFrame#glassCard {
                background-color: rgba(30, 41, 59, 0.56);
                border: 1px solid rgba(148, 163, 184, 0.16);
                border-radius: 20px;
            }
            QLabel#modeBadge {
                background-color: rgba(99, 102, 241, 0.20);
                color: #c7d2fe;
                border: 1px solid rgba(129, 140, 248, 0.32);
                border-radius: 10px;
                padding: 2px 10px;
                font-size: 11px;
                font-weight: 600;
            }
            QLabel#trackTitle {
                color: #f8fafc;
                font-size: 16px;
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
                border: 1px solid rgba(199, 210, 254, 0.45);
                border-radius: 26px;
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
                background-color: rgba(51, 65, 85, 0.55);
                border: 1px solid rgba(148, 163, 184, 0.18);
                border-radius: 21px;
            }
            QPushButton#iconBtn:hover {
                background-color: rgba(71, 85, 105, 0.85);
                border-color: rgba(165, 180, 252, 0.45);
            }
            QPushButton#iconBtn:pressed {
                background-color: rgba(30, 41, 59, 0.9);
            }
            QPushButton#iconBtnSubtle {
                background-color: rgba(30, 41, 59, 0.45);
                border: 1px solid rgba(148, 163, 184, 0.12);
                border-radius: 18px;
            }
            QPushButton#iconBtnSubtle:hover {
                background-color: rgba(51, 65, 85, 0.70);
                border-color: rgba(148, 163, 184, 0.30);
            }
            QPushButton#sendBtn {
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:1,
                    stop:0 #6366f1, stop:1 #4f46e5
                );
                border: 1px solid rgba(165, 180, 252, 0.40);
                border-radius: 19px;
            }
            QPushButton#sendBtn:hover {
                background-color: #818cf8;
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
            QScrollArea#chatScroll {
                border: none;
                background: transparent;
            }
            QFrame#userBubble {
                background-color: rgba(79, 70, 229, 0.54);
                border: 1px solid rgba(129, 140, 248, 0.35);
                border-radius: 14px;
            }
            QFrame#botBubble {
                background-color: rgba(30, 41, 59, 0.86);
                border: 1px solid rgba(148, 163, 184, 0.18);
                border-radius: 14px;
            }
            QLineEdit#chatInput {
                background-color: rgba(15, 23, 42, 0.78);
                color: #f8fafc;
                border: 1px solid rgba(148, 163, 184, 0.22);
                border-radius: 19px;
                padding: 0 14px;
                height: 38px;
                font-size: 13px;
            }
            QLineEdit#chatInput:focus {
                border-color: #6366f1;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 6px;
            }
            QScrollBar::handle:vertical {
                background: rgba(148, 163, 184, 0.3);
                border-radius: 3px;
            }
        """)

    # ── Системный трей ────────────────────────────────────────────────
    def _setup_tray(self):
        self.tray = QSystemTrayIcon(self._app_icon, self)
        self.tray.setToolTip("Макс — голосовой ассистент")

        menu = QMenu()
        act_show = QAction("Открыть окно", self)
        act_pause = QAction("Пауза / Продолжить", self)
        act_next = QAction("Следующий трек", self)
        act_quit = QAction("Закрыть интерфейс", self)

        act_show.triggered.connect(self._restore_from_tray)
        act_pause.triggered.connect(self._toggle_play_pause)
        act_next.triggered.connect(lambda: self._send_music_ctrl("next"))
        act_quit.triggered.connect(self._quit_ui)

        menu.addAction(act_show)
        menu.addSeparator()
        menu.addAction(act_pause)
        menu.addAction(act_next)
        menu.addSeparator()
        menu.addAction(act_quit)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason):
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._restore_from_tray()

    def _restore_from_tray(self):
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def closeEvent(self, event):
        if self.tray.isVisible():
            event.ignore()
            self.hide()
            self.tray.showMessage(
                "Макс свёрнут в трей",
                "Голосовое управление и консоль логов продолжают работу.",
                QSystemTrayIcon.MessageIcon.Information,
                2000,
            )
        else:
            event.accept()

    def _quit_ui(self):
        self.tray.hide()
        QApplication.quit()

    # ── Управление плеером и отправка команд ──────────────────────────
    def _send_music_ctrl(self, action: str):
        try:
            ipc.put(protocol.MUSIC_INBOX, protocol.music_control(action))
        except Exception as e:
            log(f"ui: music_control({action}) error: {e}", tag="UI", level="WARN")

    def _toggle_play_pause(self):
        self._send_music_ctrl("pause" if self._is_playing else "resume")

    def _send_brain_cmd(self, text: str):
        try:
            ipc.put(protocol.BRAIN_INBOX, protocol.command(text, source=CommandSource.SYSTEM))
        except Exception as e:
            log(f"ui: send command error: {e}", tag="UI", level="WARN")

    def _on_send_input(self):
        text = self.input_box.text().strip()
        if not text:
            return
        self.input_box.clear()
        self._send_brain_cmd(text)

    # ── Загрузка обложки в фоне ───────────────────────────────────────
    def _request_cover(self, cover_url: str, file_path: str, artist: str = "", title: str = ""):
        key = cover_url or file_path or f"{artist}|{title}".strip("|") or "default"
        if key == self._cover_key:
            return
        self._cover_key = key

        if key == "default":
            self.lbl_cover.setPixmap(self._default_cover)
            return

        def worker():
            os.makedirs(COVER_CACHE_DIR, exist_ok=True)
            h = hashlib.md5(key.encode("utf-8")).hexdigest()
            cached_path = os.path.join(COVER_CACHE_DIR, f"{h}.jpg")

            raw_bytes = None
            if os.path.exists(cached_path):
                try:
                    with open(cached_path, "rb") as f:
                        raw_bytes = f.read()
                except Exception:
                    pass

            if raw_bytes is None and file_path:
                raw_bytes = _extract_id3_cover(file_path)

            if raw_bytes is None and cover_url:
                try:
                    r = requests.get(cover_url, timeout=4.0)
                    if r.status_code == 200 and len(r.content) > 256:
                        raw_bytes = r.content
                except Exception:
                    pass

            if raw_bytes is None and (artist or title):
                raw_bytes = _fetch_fallback_cover(artist, title)

            if raw_bytes:
                try:
                    with open(cached_path, "wb") as f:
                        f.write(raw_bytes)
                except Exception:
                    pass

            pix = QPixmap()
            if raw_bytes and pix.loadFromData(raw_bytes):
                rounded = _make_rounded_pixmap(pix, COVER_SIZE, 20)
            else:
                rounded = self._default_cover
            self._cover_signals.loaded.emit(key, rounded)

        threading.Thread(target=worker, daemon=True).start()

    def _on_cover_loaded(self, key: str, pix: QPixmap):
        if key == self._cover_key:
            self.lbl_cover.setPixmap(pix)

    # ── Чат ───────────────────────────────────────────────────────────
    def _add_bubble(self, role: str, text: str, ts: float, meta: str = ""):
        bubble = ChatBubble(role, text, ts, meta)
        idx = max(0, self.chat_layout.count() - 1)
        self.chat_layout.insertWidget(idx, bubble)

        while self.chat_layout.count() > 121:
            item = self.chat_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

        QTimer.singleShot(30, self._scroll_to_bottom)

    def _scroll_to_bottom(self):
        sb = self.scroll.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _load_initial_chat(self):
        if not os.path.exists(CHAT_LOG_FILE):
            return
        try:
            with open(CHAT_LOG_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()
                self._chat_offset = f.tell()
            for line in lines[-60:]:
                try:
                    item = json.loads(line)
                    self._add_bubble(
                        item.get("role", "assistant"),
                        item.get("text", ""),
                        float(item.get("ts", 0.0)),
                        item.get("meta", ""),
                    )
                except Exception:
                    continue
        except Exception:
            pass

    def _poll_chat(self):
        if not os.path.exists(CHAT_LOG_FILE):
            return
        try:
            size = os.path.getsize(CHAT_LOG_FILE)
            if size < self._chat_offset:
                self._chat_offset = 0
            if size == self._chat_offset:
                return
            with open(CHAT_LOG_FILE, "r", encoding="utf-8") as f:
                f.seek(self._chat_offset)
                new_lines = f.readlines()
                self._chat_offset = f.tell()
            for line in new_lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                    self._add_bubble(
                        item.get("role", "assistant"),
                        item.get("text", ""),
                        float(item.get("ts", 0.0)),
                        item.get("meta", ""),
                    )
                except Exception:
                    continue
        except Exception:
            pass

    # ── Опрос состояния ───────────────────────────────────────────────
    def _poll_state(self):
        tts_speaking = False
        try:
            from workers.tts.state_file import get_state as get_tts
            tts_speaking = bool(get_tts().get("speaking", False))
        except Exception:
            pass

        expecting = rt.exists(rt.FLAG_EXPECTING_INPUT)

        if tts_speaking:
            self.lbl_status_dot.setStyleSheet("color: #22d3ee; font-size: 12px; background: transparent;")
            self.lbl_status_text.setText("Макс говорит...")
        elif expecting:
            self.lbl_status_dot.setStyleSheet("color: #f59e0b; font-size: 12px; background: transparent;")
            self.lbl_status_text.setText("Жду ответ...")
        else:
            self.lbl_status_dot.setStyleSheet("color: #4ade80; font-size: 12px; background: transparent;")
            self.lbl_status_text.setText("Макс слушает («Макс...»)")

        st = read_music_ui()
        ts = float(st.get("ts", 0.0) or 0.0)
        fresh = (time.time() - ts) < 5.0

        if fresh and (st.get("playing") or st.get("paused")):
            self._is_playing = bool(st.get("playing"))
            self._dur_ms = int(st.get("dur_ms", 0) or 0)
            self._pos_ms = int(st.get("pos_ms", 0) or 0)

            title = st.get("title") or "Без названия"
            artist = st.get("artist") or "Неизвестный исполнитель"
            self.lbl_title.setText(title)
            self.lbl_artist.setText(artist)
            self.lbl_mode_badge.setText(_mode_label(st.get("mode", "")))
            self.btn_play.setIcon(self._icon_pause if self._is_playing else self._icon_play)

            self._request_cover(st.get("cover_url", ""), st.get("file_path", ""), artist, title)
            self._update_progress_widgets()
        else:
            self._is_playing = False
            self.btn_play.setIcon(self._icon_play)
            if not fresh:
                self.lbl_title.setText("Ничего не играет")
                self.lbl_artist.setText("Скажите: «Макс, включи музыку»")
                self.lbl_mode_badge.setText("Ожидание")
                self._request_cover("", "", "", "")
                self._pos_ms = 0
                self._dur_ms = 0
                self._update_progress_widgets()

        self._poll_chat()

    def _tick_progress(self):
        if self._is_playing and self._dur_ms > 0:
            self._pos_ms = min(self._dur_ms, self._pos_ms + 200)
            self._update_progress_widgets()

    def _update_progress_widgets(self):
        self.lbl_pos.setText(_fmt_ms(self._pos_ms))
        self.lbl_dur.setText(_fmt_ms(self._dur_ms))
        if self._dur_ms > 0:
            val = int(self._pos_ms * 1000 / self._dur_ms)
            self.progress.setValue(max(0, min(1000, val)))
        else:
            self.progress.setValue(0)


def main():
    ipc.init()
    log("ui: старт окна Макса", tag="UI")

    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("maks.assistant.ui.1")
        except Exception:
            pass

    app = QApplication(sys.argv)
    app_icon = _make_app_icon()
    app.setWindowIcon(app_icon)
    app.setQuitOnLastWindowClosed(False)

    win = MaksMainWindow(app_icon)
    win.show()

    signal.signal(signal.SIGINT, lambda *_: app.quit())
    signal.signal(signal.SIGTERM, lambda *_: app.quit())

    sig_timer = QTimer()
    sig_timer.start(300)
    sig_timer.timeout.connect(lambda: None)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()