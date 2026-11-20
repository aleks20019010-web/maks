# workers/ui/chat_panel.py
# Нижняя панель диалога: быстрые команды с иконками, лента сообщений с аватарками,
# поле ввода и нижний мини-визуализатор.

import json
import os

from PySide6.QtCore import Qt, QTimer, QSize
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from shared import ipc
from shared import protocol
from shared.log import log
from shared.sources import CommandSource
from shared.ui_bridge import CHAT_LOG_FILE
from workers.ui.theme import make_vector_icon
from workers.ui.widgets import ChatBubble, SpectrumVisualizerWidget


class ChatPanel(QWidget):
    """Нижняя панель диалога в неоново-космическом стиле."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._chat_offset = 0
        self._build_ui()
        self._load_initial_chat()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # 1. Стеклянная карточка ленты диалога (справа место под неоновый скрипичный ключ)
        chat_row = QHBoxLayout()
        chat_row.setContentsMargins(0, 0, 56, 0)

        chat_card = QFrame()
        chat_card.setObjectName("glassCard")
        card_lay = QVBoxLayout(chat_card)
        card_lay.setContentsMargins(10, 8, 10, 8)
        card_lay.setSpacing(6)

        chips_row = QHBoxLayout()
        chips_row.setSpacing(6)

        quick_commands = [
            ("Музыка", "note", "включи музыку"),
            ("Волна", "wave", "включи мою волну"),
            ("Погода", "weather", "какая погода"),
            ("Радио", "radio", "включи радио"),
        ]
        for label, icon_kind, cmd in quick_commands:
            chip = QPushButton(f" {label}")
            chip.setObjectName("quickCmdChip")
            chip.setIcon(make_vector_icon(icon_kind, 14, "#7dd3fc"))
            chip.setIconSize(QSize(14, 14))
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setToolTip(f"Отправить команду: «{cmd}»")
            chip.clicked.connect(lambda _, c=cmd: self._send_brain_cmd(c))
            chips_row.addWidget(chip)

        chips_row.addStretch(1)

        self.lbl_workers = QLabel("ASR ●  MUSIC ●  TTS ●")
        self.lbl_workers.setStyleSheet(
            "color: rgba(148, 163, 184, 0.72); font-size: 10px; font-weight: 600; background: transparent;"
        )
        chips_row.addWidget(self.lbl_workers)
        card_lay.addLayout(chips_row)

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
        card_lay.addWidget(self.scroll, 1)

        chat_row.addWidget(chat_card, 1)
        layout.addLayout(chat_row, 1)

        # 2. Строка ввода текста с круглой неоновой кнопкой отправки
        input_row = QHBoxLayout()
        input_row.setSpacing(8)

        self.input_box = QLineEdit()
        self.input_box.setObjectName("chatInput")
        self.input_box.setPlaceholderText("Написать команду Максу...")
        self.input_box.returnPressed.connect(self._on_send_input)

        btn_send = QPushButton()
        btn_send.setObjectName("sendBtn")
        btn_send.setIcon(make_vector_icon("send", 17, "#ffffff"))
        btn_send.setIconSize(QSize(17, 17))
        btn_send.setFixedSize(36, 36)
        btn_send.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_send.clicked.connect(self._on_send_input)

        input_row.addWidget(self.input_box, 1)
        input_row.addWidget(btn_send)
        layout.addLayout(input_row)

        # 3. Нижний компактный спектроанализатор + реальные бейджи качества
        footer_card = QFrame()
        footer_card.setObjectName("glassCard")
        footer_lay = QHBoxLayout(footer_card)
        footer_lay.setContentsMargins(12, 6, 12, 6)
        footer_lay.setSpacing(10)

        self.bottom_visualizer = SpectrumVisualizerWidget(24, compact=True)
        footer_lay.addWidget(self.bottom_visualizer, 1)

        self.lbl_bitrate = QLabel("")
        self.lbl_bitrate.setObjectName("qualityBadge")
        self.lbl_bitrate.setVisible(False)

        self.lbl_sr = QLabel("")
        self.lbl_sr.setObjectName("qualityBadge")
        self.lbl_sr.setVisible(False)

        footer_lay.addWidget(self.lbl_bitrate)
        footer_lay.addWidget(self.lbl_sr)
        layout.addWidget(footer_card)

    def set_audio_info(self, bitrate: str, sample_rate: str, active: bool):
        br = (bitrate or "").strip()
        sr = (sample_rate or "").strip()
        self.lbl_bitrate.setVisible(active and bool(br))
        self.lbl_sr.setVisible(active and bool(sr))
        if br:
            self.lbl_bitrate.setText(f"♫  {br}")
        if sr:
            self.lbl_sr.setText(f"ılı  {sr}")

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

    def _add_bubble(self, role: str, text: str, ts: float, meta: str = ""):
        bubble = ChatBubble(role, text, ts, meta)
        idx = max(0, self.chat_layout.count() - 1)
        self.chat_layout.insertWidget(idx, bubble)

        while self.chat_layout.count() > 101:
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
            for line in lines[-40:]:
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

    def poll(self, music_alive: bool, tts_alive: bool):
        m_dot = "#4ade80" if music_alive else "#64748b"
        t_dot = "#4ade80" if tts_alive else "#64748b"
        self.lbl_workers.setText(
            f'<span style="color:#4ade80;">ASR ●</span>  '
            f'<span style="color:{m_dot};">MUSIC ●</span>  '
            f'<span style="color:{t_dot};">TTS ●</span>'
        )

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