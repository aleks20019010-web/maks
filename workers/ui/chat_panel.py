# workers/ui/chat_panel.py
# Нижняя компактная панель окна (1/4 высоты): лента диалога,
# индикаторы состояния воркеров, быстрые чипсы-команды и поле ввода.

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
from workers.ui.widgets import ChatBubble


class ChatPanel(QFrame):
    """Компактная панель диалога, занимающая 1/4 высоты окна."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("glassCard")
        self._chat_offset = 0
        self._build_ui()
        self._load_initial_chat()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 10)
        layout.setSpacing(6)

        # Компактная шапка: заголовок + чипсы + статус воркеров в одну строку
        top_row = QHBoxLayout()
        top_row.setSpacing(6)

        chat_header = QLabel("Диалог")
        chat_header.setStyleSheet(
            "color: #cbd5e1; font-size: 11px; font-weight: 700; padding-left: 2px; background: transparent;"
        )
        top_row.addWidget(chat_header)

        quick_commands = [
            ("Волна", "включи мою волну"),
            ("Лайки", "включи мою музыку"),
            ("Трек?", "что сейчас играет"),
            ("Стоп", "стоп"),
        ]
        for label, cmd in quick_commands:
            chip = QPushButton(label)
            chip.setObjectName("chipBtn")
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.clicked.connect(lambda _, c=cmd: self._send_brain_cmd(c))
            top_row.addWidget(chip)

        top_row.addStretch(1)

        self.lbl_workers = QLabel("ASR ●  MUSIC ●  TTS ●")
        self.lbl_workers.setStyleSheet(
            "color: rgba(148, 163, 184, 0.65); font-size: 10px; font-weight: 600; background: transparent;"
        )
        top_row.addWidget(self.lbl_workers)
        layout.addLayout(top_row)

        # Компактная лента сообщений
        self.scroll = QScrollArea()
        self.scroll.setObjectName("chatScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.chat_container = QWidget()
        self.chat_container.setStyleSheet("background: transparent;")
        self.chat_layout = QVBoxLayout(self.chat_container)
        self.chat_layout.setContentsMargins(2, 1, 2, 1)
        self.chat_layout.setSpacing(4)
        self.chat_layout.addStretch(1)

        self.scroll.setWidget(self.chat_container)
        layout.addWidget(self.scroll, 1)

        # Строка ввода текста
        input_row = QHBoxLayout()
        input_row.setSpacing(6)

        self.input_box = QLineEdit()
        self.input_box.setObjectName("chatInput")
        self.input_box.setPlaceholderText("Написать команду Максу...")
        self.input_box.returnPressed.connect(self._on_send_input)

        btn_send = QPushButton()
        btn_send.setObjectName("sendBtn")
        btn_send.setIcon(make_vector_icon("send", 16, "#ffffff"))
        btn_send.setIconSize(QSize(16, 16))
        btn_send.setFixedSize(32, 32)
        btn_send.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_send.clicked.connect(self._on_send_input)

        input_row.addWidget(self.input_box, 1)
        input_row.addWidget(btn_send)
        layout.addLayout(input_row)

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