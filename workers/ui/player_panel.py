# workers/ui/player_panel.py
# Верхняя часть окна (3/4 высоты):
#   - Автопереключение между 3D-голограммой Макса (когда музыка выключена
#     или Макс говорит/слушает ответ) и музыкальным плеером с эквалайзером.

import time
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QGraphicsDropShadowEffect, QHBoxLayout,
    QLabel, QPushButton, QSlider, QStackedWidget, QVBoxLayout, QWidget,
)

from shared import ipc
from shared import protocol
from shared.log import log
from shared.sources import CommandSource
from workers.ui.avatar_widget import MaksAvatarWidget
from workers.ui.covers import CoverLoader
from workers.ui.theme import make_vector_icon
from workers.ui.widgets import (
    ActivityWaveWidget,
    AmbientGlassCard,
    SpectrumVisualizerWidget,
)


COVER_SIZE = 180

_FALLBACK_EQ_PRESETS = [
    (0, "Ровный"), (1, "Классика"), (2, "Клуб"), (3, "Танцевальный"),
    (4, "Глубокий бас"), (5, "Бас и высокие"), (6, "Высокие частоты"),
    (7, "Наушники"), (8, "Большой зал"), (9, "Живой звук"),
    (10, "Вечеринка"), (11, "Поп"), (12, "Регги"), (13, "Рок"),
    (14, "Ска"), (15, "Мягкий"), (16, "Софт-рок"), (17, "Техно"),
]


def _load_eq_presets() -> list[tuple[int, str]]:
    try:
        from workers.music.player import get_vlc_eq_presets
        presets = get_vlc_eq_presets()
        if presets:
            return presets
    except Exception:
        pass
    return _FALLBACK_EQ_PRESETS


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


class PlayerPanel(AmbientGlassCard):
    """Верхняя панель (3/4 высоты): голографический аватар Макса + музыкальный плеер."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._is_playing = False
        self._dur_ms = 0
        self._pos_ms = 0
        self._vol_dragging = False
        self._last_vol_sent_at = 0.0
        self._force_view: str | None = None  # None = авто, "avatar" / "player" = вручную

        self._icon_play = make_vector_icon("play", 24, "#ffffff")
        self._icon_pause = make_vector_icon("pause", 24, "#ffffff")

        self._cover_loader = CoverLoader(COVER_SIZE)
        self._cover_loader.loaded.connect(self._on_cover_loaded)

        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 14, 20, 16)
        layout.setSpacing(8)

        # 1. Общая верхняя статусная строка
        status_row = QHBoxLayout()
        status_row.setSpacing(8)

        self.wave_widget = ActivityWaveWidget()
        self.lbl_status_text = QLabel("Макс на связи")
        self.lbl_status_text.setStyleSheet(
            "color: #e2e8f0; font-size: 12px; font-weight: 600; background: transparent;"
        )

        self.lbl_source_badge = QLabel("Диск")
        self.lbl_source_badge.setObjectName("sourceBadge")
        self.lbl_source_badge.setVisible(False)

        self.lbl_mode_badge = QLabel("Ожидание")
        self.lbl_mode_badge.setObjectName("modeBadge")

        self.btn_switch_view = QPushButton("Плеер")
        self.btn_switch_view.setObjectName("chipBtn")
        self.btn_switch_view.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_switch_view.setToolTip("Переключить вид: Аватар / Плеер")
        self.btn_switch_view.clicked.connect(self._toggle_manual_view)

        status_row.addWidget(self.wave_widget)
        status_row.addWidget(self.lbl_status_text)
        status_row.addStretch(1)
        status_row.addWidget(self.lbl_source_badge)
        status_row.addWidget(self.lbl_mode_badge)
        status_row.addWidget(self.btn_switch_view)
        layout.addLayout(status_row)

        # 2. Стек представлений: [0] Аватар Макса | [1] Музыкальный плеер
        self.stack = QStackedWidget()
        self.stack.setStyleSheet("background: transparent;")

        # ── Страница 0: Голограмма Макса ──────────────────────────────
        avatar_page = QWidget()
        avatar_page.setStyleSheet("background: transparent;")
        av_layout = QVBoxLayout(avatar_page)
        av_layout.setContentsMargins(0, 4, 0, 4)
        av_layout.setSpacing(6)

        self.avatar = MaksAvatarWidget()
        av_layout.addWidget(self.avatar, 1)

        self.lbl_avatar_caption = QLabel("Готов к работе")
        self.lbl_avatar_caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_avatar_caption.setStyleSheet(
            "color: #94a3b8; font-size: 12px; font-weight: 500; background: transparent;"
        )
        av_layout.addWidget(self.lbl_avatar_caption)

        # ── Страница 1: Музыкальный плеер ─────────────────────────────
        player_page = QWidget()
        player_page.setStyleSheet("background: transparent;")
        p_layout = QVBoxLayout(player_page)
        p_layout.setContentsMargins(0, 2, 0, 0)
        p_layout.setSpacing(8)

        p_layout.addStretch(1)

        cover_row = QHBoxLayout()
        cover_row.addStretch(1)
        self.lbl_cover = QLabel()
        self.lbl_cover.setFixedSize(COVER_SIZE, COVER_SIZE)
        self.lbl_cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_cover.setPixmap(self._cover_loader.default_cover)
        self.lbl_cover.setStyleSheet("background: transparent;")

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(34)
        shadow.setOffset(0, 10)
        shadow.setColor(QColor(0, 0, 0, 180))
        self.lbl_cover.setGraphicsEffect(shadow)

        cover_row.addWidget(self.lbl_cover)
        cover_row.addStretch(1)
        p_layout.addLayout(cover_row)

        self.lbl_title = QLabel("Ничего не играет")
        self.lbl_title.setObjectName("trackTitle")
        self.lbl_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_title.setWordWrap(True)

        self.lbl_artist = QLabel("Скажите: «Макс, включи музыку»")
        self.lbl_artist.setObjectName("trackArtist")
        self.lbl_artist.setAlignment(Qt.AlignmentFlag.AlignCenter)

        p_layout.addWidget(self.lbl_title)
        p_layout.addWidget(self.lbl_artist)

        vis_row = QHBoxLayout()
        vis_row.setContentsMargins(10, 2, 10, 2)
        self.visualizer = SpectrumVisualizerWidget(28)
        vis_row.addWidget(self.visualizer)
        p_layout.addLayout(vis_row)

        prog_col = QVBoxLayout()
        prog_col.setSpacing(4)
        prog_col.setContentsMargins(6, 2, 6, 2)

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

        ctrl_row = QHBoxLayout()
        ctrl_row.setSpacing(14)
        ctrl_row.addStretch(1)

        self.btn_dislike = QPushButton()
        self.btn_dislike.setObjectName("iconBtnSubtle")
        self.btn_dislike.setIcon(make_vector_icon("dislike", 18, "#94a3b8"))
        self.btn_dislike.setIconSize(QSize(18, 18))
        self.btn_dislike.setFixedSize(36, 36)
        self.btn_dislike.setToolTip("Не нравится (пропустить)")

        self.btn_prev = QPushButton()
        self.btn_prev.setObjectName("iconBtn")
        self.btn_prev.setIcon(make_vector_icon("prev", 20, "#e2e8f0"))
        self.btn_prev.setIconSize(QSize(20, 20))
        self.btn_prev.setFixedSize(42, 42)
        self.btn_prev.setToolTip("Предыдущий трек")

        self.btn_play = QPushButton()
        self.btn_play.setObjectName("playCircleBtn")
        self.btn_play.setIcon(self._icon_play)
        self.btn_play.setIconSize(QSize(24, 24))
        self.btn_play.setFixedSize(54, 54)
        self.btn_play.setToolTip("Пауза / Продолжить")

        self.btn_next = QPushButton()
        self.btn_next.setObjectName("iconBtn")
        self.btn_next.setIcon(make_vector_icon("next", 20, "#e2e8f0"))
        self.btn_next.setIconSize(QSize(20, 20))
        self.btn_next.setFixedSize(42, 42)
        self.btn_next.setToolTip("Следующий трек")

        self.btn_like = QPushButton()
        self.btn_like.setObjectName("iconBtnSubtle")
        self.btn_like.setIcon(make_vector_icon("like", 19, "#f43f5e"))
        self.btn_like.setIconSize(QSize(19, 19))
        self.btn_like.setFixedSize(36, 36)
        self.btn_like.setToolTip("Нравится")

        for b in (self.btn_dislike, self.btn_prev, self.btn_play, self.btn_next, self.btn_like):
            b.setCursor(Qt.CursorShape.PointingHandCursor)

        self.btn_dislike.clicked.connect(lambda: self.send_music_ctrl("dislike"))
        self.btn_prev.clicked.connect(lambda: self.send_music_ctrl("prev"))
        self.btn_play.clicked.connect(self.toggle_play_pause)
        self.btn_next.clicked.connect(lambda: self.send_music_ctrl("next"))
        self.btn_like.clicked.connect(self._send_like)

        ctrl_row.addWidget(self.btn_dislike)
        ctrl_row.addWidget(self.btn_prev)
        ctrl_row.addWidget(self.btn_play)
        ctrl_row.addWidget(self.btn_next)
        ctrl_row.addWidget(self.btn_like)
        ctrl_row.addStretch(1)
        p_layout.addLayout(ctrl_row)

        bottom_row = QHBoxLayout()
        bottom_row.setContentsMargins(14, 4, 14, 0)
        bottom_row.setSpacing(8)

        lbl_vol_icon = QLabel()
        lbl_vol_icon.setPixmap(make_vector_icon("volume", 16, "#94a3b8").pixmap(16, 16))
        lbl_vol_icon.setStyleSheet("background: transparent;")

        self.vol_slider = QSlider(Qt.Orientation.Horizontal)
        self.vol_slider.setObjectName("volSlider")
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setValue(100)
        self.vol_slider.setCursor(Qt.CursorShape.PointingHandCursor)
        self.vol_slider.sliderPressed.connect(self._on_vol_pressed)
        self.vol_slider.sliderReleased.connect(self._on_vol_released)
        self.vol_slider.valueChanged.connect(self._on_vol_changed)

        self.lbl_vol_val = QLabel("100%")
        self.lbl_vol_val.setFixedWidth(34)
        self.lbl_vol_val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.lbl_vol_val.setStyleSheet("color: #94a3b8; font-size: 11px; font-weight: 600; background: transparent;")

        self.eq_combo = QComboBox()
        self.eq_combo.setObjectName("eqCombo")
        self.eq_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.eq_combo.setToolTip("Пресет 10-полосного эквалайзера VLC")
        self.eq_combo.addItem("EQ: Выкл", None)
        for idx, title_ru in _load_eq_presets():
            self.eq_combo.addItem(f"EQ: {title_ru}", idx)
        self.eq_combo.currentIndexChanged.connect(self._on_eq_changed)

        bottom_row.addWidget(lbl_vol_icon)
        bottom_row.addWidget(self.vol_slider, 1)
        bottom_row.addWidget(self.lbl_vol_val)
        bottom_row.addSpacing(10)
        bottom_row.addWidget(self.eq_combo)
        p_layout.addLayout(bottom_row)

        p_layout.addStretch(1)

        self.stack.addWidget(avatar_page)  # index 0
        self.stack.addWidget(player_page)  # index 1
        layout.addWidget(self.stack, 1)

    # ── Управление ────────────────────────────────────────────────────
    def _toggle_manual_view(self):
        if self.stack.currentIndex() == 0:
            self._force_view = "player"
            self.stack.setCurrentIndex(1)
            self.btn_switch_view.setText("Макс")
        else:
            self._force_view = "avatar"
            self.stack.setCurrentIndex(0)
            self.btn_switch_view.setText("Плеер")
            self.set_accent_color(QColor("#0ea5e9"))

    def send_music_ctrl(self, action: str, **kwargs):
        try:
            ipc.put(protocol.MUSIC_INBOX, protocol.music_control(action, **kwargs))
        except Exception as e:
            log(f"ui: music_control({action}) error: {e}", tag="UI", level="WARN")

    def toggle_play_pause(self):
        self.send_music_ctrl("pause" if self._is_playing else "resume")

    def _send_like(self):
        try:
            ipc.put(protocol.BRAIN_INBOX, protocol.command("нравится этот трек", source=CommandSource.SYSTEM))
        except Exception as e:
            log(f"ui: like cmd error: {e}", tag="UI", level="WARN")

    def _on_vol_pressed(self):
        self._vol_dragging = True

    def _on_vol_released(self):
        self._vol_dragging = False
        self._apply_volume(self.vol_slider.value(), force=True)

    def _on_vol_changed(self, val: int):
        self.lbl_vol_val.setText(f"{val}%")
        if self._vol_dragging:
            self._apply_volume(val, force=False)

    def _apply_volume(self, val: int, force: bool = False):
        now = time.time()
        if not force and (now - self._last_vol_sent_at) < 0.12:
            return
        self._last_vol_sent_at = now
        try:
            ipc.put(protocol.MUSIC_INBOX, protocol.music_volume_set(int(val)))
        except Exception as e:
            log(f"ui: volume_set({val}) error: {e}", tag="UI", level="WARN")

    def _on_eq_changed(self, combo_index: int):
        preset_id = self.eq_combo.itemData(combo_index)
        self.send_music_ctrl("eq_preset", preset=preset_id)

    def _on_cover_loaded(self, key: str, pix: QPixmap, accent: QColor):
        if key == self._cover_loader.current_key:
            self.lbl_cover.setPixmap(pix)
            if self.stack.currentIndex() == 1:
                self.set_accent_color(accent)
            self.visualizer.set_accent_color(accent)

    # ── Обновление состояния от главного окна ─────────────────────────
    def update_state(self, st: dict, music_alive: bool, tts_speaking: bool, expecting: bool):
        music_active = music_alive and bool(st.get("playing") or st.get("paused"))

        # Определяем состояние аватара
        if tts_speaking:
            av_state = "speaking"
            self.wave_widget.set_state("speaking", "#22d3ee")
            self.lbl_status_text.setText("Макс говорит...")
            self.lbl_avatar_caption.setText("Отвечаю...")
        elif expecting:
            av_state = "listening"
            self.wave_widget.set_state("listening", "#f59e0b")
            self.lbl_status_text.setText("Жду ответ...")
            self.lbl_avatar_caption.setText("Слушаю вас...")
        elif music_alive and st.get("playing"):
            av_state = "happy"
            self.wave_widget.set_state("music", "#818cf8")
            self.lbl_status_text.setText("Макс слушает («Макс...»)")
            self.lbl_avatar_caption.setText("Играет музыка")
        else:
            av_state = "idle"
            self.wave_widget.set_state("idle", "#4ade80")
            self.lbl_status_text.setText("Макс слушает («Макс...»)")
            self.lbl_avatar_caption.setText("Скажите «Макс...» или напишите команду")

        self.avatar.set_state(av_state)
        self.avatar.set_speech_level(self.visualizer.audio_level if tts_speaking else 0.0)

        # Сбрасываем ручной режим "avatar", если музыка полностью остановилась
        # (чтобы при следующем запуске трека плеер снова открылся сам)
        if not music_active and self._force_view == "avatar":
            self._force_view = None

        # Автовыбор страницы с учётом ручного переключения
        if tts_speaking or expecting:
            target_idx = 0
        elif self._force_view == "avatar":
            target_idx = 0
        elif self._force_view == "player":
            target_idx = 1
        elif music_active:
            target_idx = 1
        else:
            target_idx = 0

        if self.stack.currentIndex() != target_idx:
            self.stack.setCurrentIndex(target_idx)
            self.btn_switch_view.setText("Макс" if target_idx == 1 else "Плеер")
            if target_idx == 0:
                self.set_accent_color(QColor("#0ea5e9"))

        # Обновление данных плеера
        if music_active:
            self._is_playing = bool(st.get("playing"))
            self._dur_ms = int(st.get("dur_ms", 0) or 0)
            self._pos_ms = int(st.get("pos_ms", 0) or 0)

            title = st.get("title") or "Без названия"
            artist = st.get("artist") or "Неизвестный исполнитель"
            self.lbl_title.setText(title)
            self.lbl_artist.setText(artist)
            self.lbl_mode_badge.setText(_mode_label(st.get("mode", "")))
            self.btn_play.setIcon(self._icon_pause if self._is_playing else self._icon_play)

            is_yandex = bool(st.get("cover_url"))
            self.lbl_source_badge.setText("Яндекс" if is_yandex else "Диск")
            self.lbl_source_badge.setVisible(True)

            if not self._vol_dragging and (time.time() - self._last_vol_sent_at) > 0.6:
                vol = int(st.get("volume", 100) or 100)
                if self.vol_slider.value() != vol:
                    self.vol_slider.blockSignals(True)
                    self.vol_slider.setValue(vol)
                    self.vol_slider.blockSignals(False)
                    self.lbl_vol_val.setText(f"{vol}%")

            if "eq_preset" in st:
                idx = self.eq_combo.findData(st.get("eq_preset"))
                if idx >= 0 and self.eq_combo.currentIndex() != idx:
                    self.eq_combo.blockSignals(True)
                    self.eq_combo.setCurrentIndex(idx)
                    self.eq_combo.blockSignals(False)

            self._cover_loader.request(st.get("cover_url", ""), st.get("file_path", ""), artist, title)
            self._update_progress_widgets()
        else:
            self._is_playing = False
            self.btn_play.setIcon(self._icon_play)
            self.lbl_source_badge.setVisible(False)
            if not music_alive:
                self.lbl_title.setText("Ничего не играет")
                self.lbl_artist.setText("Скажите: «Макс, включи музыку»")
                self.lbl_mode_badge.setText("Ожидание")
                self._cover_loader.request("", "", "", "")
                self._pos_ms = 0
                self._dur_ms = 0
                self._update_progress_widgets()

        self.visualizer.set_playing(self._is_playing)

    def tick_progress(self):
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

    def shutdown(self):
        self.visualizer.shutdown()