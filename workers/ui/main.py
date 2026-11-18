# workers/ui/main.py
# Точка входа десктоп-интерфейса Макса:
#   - 3/4 окна: PlayerPanel (плеер, визуализатор, пресеты EQ)
#   - 1/4 окна: ChatPanel (компактный диалог и быстрые команды)
#   - Системный трей и фоновый опрос состояния воркеров

import os
import signal
import sys
import time

if os.name == "nt":
    os.system("chcp 65001 > nul")

from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QMenu, QSystemTrayIcon, QVBoxLayout, QWidget,
)

from shared import ipc
from shared import runtime_state as rt
from shared.log import log
from shared.ui_bridge import read_music_ui
from workers.ui.chat_panel import ChatPanel
from workers.ui.player_panel import PlayerPanel
from workers.ui.theme import APP_STYLESHEET, make_app_icon


class MaksMainWindow(QMainWindow):
    def __init__(self, app_icon: QIcon):
        super().__init__()
        self._app_icon = app_icon
        self.setWindowTitle("Макс — Ассистент")
        self.setWindowIcon(self._app_icon)
        self.setMinimumSize(480, 780)
        self.resize(520, 860)

        self._build_ui()
        self.setStyleSheet(APP_STYLESHEET)
        self._setup_tray()

        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_state)
        self._poll_timer.start(250)

        self._tick_timer = QTimer(self)
        self._tick_timer.timeout.connect(self.player_panel.tick_progress)
        self._tick_timer.start(200)

    def _build_ui(self):
        root = QWidget()
        root.setObjectName("rootWidget")
        self.setCentralWidget(root)

        layout = QVBoxLayout(root)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        self.player_panel = PlayerPanel()
        self.chat_panel = ChatPanel()

        # Пропорция 3:1 — 75% плееру, 25% компактному чату
        layout.addWidget(self.player_panel, 3)
        layout.addWidget(self.chat_panel, 1)

    def _setup_tray(self):
        self.tray = QSystemTrayIcon(self._app_icon, self)
        self.tray.setToolTip("Макс — голосовой ассистент")

        menu = QMenu()
        act_show = QAction("Открыть окно", self)
        act_pause = QAction("Пауза / Продолжить", self)
        act_next = QAction("Следующий трек", self)
        act_quit = QAction("Закрыть интерфейс", self)

        act_show.triggered.connect(self._restore_from_tray)
        act_pause.triggered.connect(self.player_panel.toggle_play_pause)
        act_next.triggered.connect(lambda: self.player_panel.send_music_ctrl("next"))
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
            self.player_panel.shutdown()
            event.accept()

    def _quit_ui(self):
        self.player_panel.shutdown()
        self.tray.hide()
        QApplication.quit()

    def _poll_state(self):
        tts_speaking = False
        tts_alive = False
        try:
            from workers.tts.state_file import get_state as get_tts
            tts_st = get_tts()
            tts_alive = bool(tts_st)
            tts_speaking = bool(tts_st.get("speaking", False))
        except Exception:
            pass

        expecting = rt.exists(rt.FLAG_EXPECTING_INPUT)

        st = read_music_ui()
        ts = float(st.get("ts", 0.0) or 0.0)
        music_alive = (time.time() - ts) < 5.0

        self.player_panel.update_state(st, music_alive, tts_speaking, expecting)
        self.chat_panel.poll(music_alive, tts_alive)


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
    app_icon = make_app_icon()
    app.setWindowIcon(app_icon)
    app.setQuitOnLastWindowClosed(False)

    win = MaksMainWindow(app_icon)
    win.show()

    signal.signal(signal.SIGINT, lambda *_: win._quit_ui())
    signal.signal(signal.SIGTERM, lambda *_: win._quit_ui())

    sig_timer = QTimer()
    sig_timer.start(300)
    sig_timer.timeout.connect(lambda: None)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()