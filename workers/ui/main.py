# workers/ui/main.py
# Точка входа десктоп-интерфейса Макса:
#   - 3/4 окна: PlayerPanel (плеер, визуализатор, пресеты EQ)
#   - 1/4 окна: ChatPanel (компактный диалог и быстрые команды)
#   - Системный трей и фоновый опрос состояния воркеров
#
# ── AVATAR TELEMETRY (2026-11-19) ──────────────────────────────
#   _poll_state читает data/avatar_telemetry.json:
#     mic_level, tts_level, emotion, last_active_ts.
#   Опрос ускорен до 60 мс — для плавной синхронизации уровня речи
#   и микрофона с аватар-виджетом. is_sleeping = 3 минуты бездействия.
#
# ── OPENGL (2026-11-20) ────────────────────────────────────────
#   QT_OPENGL / QT_ANGLE_PLATFORM не выставляем: Qt 6 на Windows сам
#   берёт аппаратный OpenGL встроенной графики (Intel/AMD/NVIDIA)
#   через ANGLE/D3D11 по умолчанию. Если понадобится аварийный
#   обход — задать QT_OPENGL=software в окружении снаружи.
#
# ── COSMIC BACKDROP + MIRROR VISUALIZER (2026-11-20) ───────────
#   Корневой виджет — CosmicBackdropWidget (звёздный фон/градиент).
#   Верхний SpectrumVisualizerWidget (в PlayerPanel) зеркалит нижний
#   визуализатор ChatPanel через set_mirror_visualizer(...) — оба
#   показывают один и тот же спектр в одной «строке».
#   Пропорция плеер/чат: 3:2 (было 3:1) — нижний визуализатор
#   получает больше места.

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
from shared.ui_bridge import read_music_ui, read_avatar_telemetry
from workers.ui.chat_panel import ChatPanel
from workers.ui.player_panel import PlayerPanel
from workers.ui.theme import APP_STYLESHEET, make_app_icon
from workers.ui.widgets import CosmicBackdropWidget


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

        # 60 мс — плавная синхронизация mic/tts level с аватаром
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_state)
        self._poll_timer.start(60)

        self._tick_timer = QTimer(self)
        self._tick_timer.timeout.connect(self.player_panel.tick_progress)
        self._tick_timer.start(200)

    def _build_ui(self):
        root = CosmicBackdropWidget()
        self.setCentralWidget(root)

        layout = QVBoxLayout(root)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        self.player_panel = PlayerPanel()
        self.chat_panel = ChatPanel()
        self.player_panel.visualizer.set_mirror_visualizer(self.chat_panel.bottom_visualizer)

        layout.addWidget(self.player_panel, 3)
        layout.addWidget(self.chat_panel, 2)

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

        # ── AVATAR TELEMETRY (2026-11-19) ──
        telem = read_avatar_telemetry()
        mic_level = float(telem.get("mic_level", 0.0) or 0.0)
        tts_level = float(telem.get("tts_level", 0.0) or 0.0)
        emotion = str(telem.get("emotion", "neutral") or "neutral")
        last_active = float(telem.get("last_active_ts", time.time()) or time.time())
        is_sleeping = (time.time() - last_active) > 180.0  # 3 минуты бездействия → sleep

        self.player_panel.update_state(
            st, music_alive, tts_speaking, expecting,
            mic_level=mic_level,
            tts_level=tts_level,
            emotion=emotion,
            last_active_ts=last_active,
            is_sleeping=is_sleeping,
        )
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