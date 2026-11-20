# workers/ui/player_panel.py
# Неоново-космическая верхняя панель плеера и 3D-аватара Макса.

import time
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QFrame, QGraphicsDropShadowEffect, QHBoxLayout,
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
    EqPresetTile,
    SpectrumVisualizerWidget,
)


COVER_SIZE = 128

_FALLBACK_EQ_PRESETS = [
    (0, "Ровный"), (1, "Классика"), (2, "Клуб"), (3, "Танцевальный"),
    (4, "Глубокий бас"), (5, "Бас и высокие"), (6, "Высокие частоты"),
    (7, "Наушники"), (8, "Большой зал"), (9, "Живой звук"),
    (10, "Вечеринка"), (11, "Поп"), (12, "Регги"), (13, "Рок"),
    (14, "Ска"), (15, "Мягкий"), (16, "Софт-рок"), (17, "Техно"),
]

_QUICK_EQ_CARDS = [
    ("Классика", "star", 1),
    ("Рок", "rock", 13),
    ("Поп", "note", 11),
    ("Электроника", "wave", 17),
    ("Джаз", "sax", 9),
    ("Бас", "bars", 4),
    ("Польз.", "user", 0),
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
        "genre": "Жанр",
        "radio": "Радио",
        "liked": "Моя музыка",
        "local_liked": "Лайки",
    }
    return mapping.get(mode, mode or "Ожидание")


class PlayerPanel(AmbientGlassCard):
    """Верхняя панель: голографический аватар Макса + неоновый музыкальный плеер."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._is_playing = False
        self._dur_ms = 0
        self._pos_ms = 0
        self._vol_dragging = False
        self._last_vol_sent_at = 0.0
        self._force_view: str | None = None
        self._active_eq_idx: int | None = None

        self._icon_play = make_vector_icon("play", 26, "#ffffff")
        self._icon_pause = make_vector_icon("pause", 26, "#ffffff")

        self._cover_loader = CoverLoader(COVER_SIZE)
        self._cover_loader.loaded.connect(self._on_cover_loaded)

        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 12)
        layout.setSpacing(8)

        # 1. Верхняя статусная строка
        status_row = QHBoxLayout()
        status_row.setSpacing(8)

        self.wave_widget = ActivityWaveWidget()
        self.lbl_status_text = QLabel("Макс на связи")
        self.lbl_status_text.setStyleSheet(
            "color: #f8fafc; font-size: 12px; font-weight: 600; background: transparent;"
        )

        self.lbl_status_dot = QLabel("●")
        self.lbl_status_dot.setStyleSheet("color: #4ade80; font-size: 10px; background: transparent;")

        self.lbl_source_badge = QLabel("Яндекс")
        self.lbl_source_badge.setObjectName("sourceBadge")
        self.lbl_source_badge.setVisible(False)

        self.lbl_mode_badge = QLabel("Ожидание")
        self.lbl_mode_badge.setObjectName("modeBadge")

        self.btn_switch_view = QPushButton("Плеер")
        self.btn_switch_view.setObjectName("chipBtn")
        self.btn_switch_view.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_switch_view.setToolTip("Переключить вид: Аватар / Плеер")
        self.btn_switch_view.clicked.connect(self._toggle_manual_view)

        self.btn_settings = QPushButton()
        self.btn_settings.setObjectName("iconBtnSubtle")
        self.btn_settings.setIcon(make_vector_icon("settings", 16, "#bae6fd"))
        self.btn_settings.setIconSize(QSize(16, 16))
        self.btn_settings.setFixedSize(28, 28)
        self.btn_settings.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_settings.setToolTip("Переключить вид Аватар / Плеер")
        self.btn_settings.clicked.connect(self._toggle_manual_view)

        status_row.addWidget(self.wave_widget)
        status_row.addWidget(self.lbl_status_text)
        status_row.addWidget(self.lbl_status_dot)
        status_row.addStretch(1)
        status_row.addWidget(self.lbl_source_badge)
        status_row.addWidget(self.lbl_mode_badge)
        status_row.addWidget(self.btn_switch_view)
        status_row.addWidget(self.btn_settings)
        layout.addLayout(status_row)

        # 2. Стек представлений: [0] Аватар Макса | [1] Музыкальный плеер
        self.stack = QStackedWidget()
        self.stack.setStyleSheet("background: transparent;")

        # ── Страница 0: Голограмма Макса ──────────────────────────────
        avatar_page = QWidget()
        avatar_page.setStyleSheet("background: transparent;")
        av_layout = QVBoxLayout(avatar_page)
        av_layout.setContentsMargins(0, 2, 0, 2)
        av_layout.setSpacing(6)

        self.avatar = MaksAvatarWidget()
        av_layout.addWidget(self.avatar, 1)

        self.lbl_avatar_caption = QLabel("Готов к работе")
        self.lbl_avatar_caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_avatar_caption.setStyleSheet(
            "color: #93c5fd; font-size: 12px; font-weight: 500; background: transparent;"
        )
        av_layout.addWidget(self.lbl_avatar_caption)

        # ── Страница 1: Неоновый музыкальный плеер ────────────────────
        player_page = QWidget()
        player_page.setStyleSheet("background: transparent;")
        p_layout = QVBoxLayout(player_page)
        p_layout.setContentsMargins(0, 2, 0, 0)
        p_layout.setSpacing(8)

        top_media_row = QHBoxLayout()
        top_media_row.setSpacing(14)

        cover_frame = QFrame()
        cover_frame.setFixedSize(COVER_SIZE + 6, COVER_SIZE + 6)
        cover_frame.setStyleSheet(
            "background-color: rgba(15, 29, 64, 0.85);"
            "border: 1.5px solid #38bdf8;"
            "border-radius: 18px;"
        )
        cf_lay = QVBoxLayout(cover_frame)
        cf_lay.setContentsMargins(2, 2, 2, 2)

        self.lbl_cover = QLabel()
        self.lbl_cover.setFixedSize(COVER_SIZE, COVER_SIZE)
        self.lbl_cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_cover.setPixmap(self._cover_loader.default_cover)
        self.lbl_cover.setStyleSheet("border: none; background: transparent;")
        cf_lay.addWidget(self.lbl_cover)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(56, 189, 248, 120))
        cover_frame.setGraphicsEffect(shadow)

        top_media_row.addWidget(cover_frame, 0, Qt.AlignmentFlag.AlignTop)

        info_col = QVBoxLayout()
        info_col.setSpacing(3)

        self.lbl_title = QLabel("Ничего не играет")
        self.lbl_title.setObjectName("trackTitle")
        self.lbl_title.setWordWrap(False)

        self.lbl_artist = QLabel("Скажите: «Макс, включи музыку»")
        self.lbl_artist.setObjectName("trackArtist")

        self.lbl_album_meta = QLabel("")
        self.lbl_album_meta.setObjectName("trackAlbum")
        self.lbl_album_meta.setVisible(False)

        quality_row = QHBoxLayout()
        quality_row.setSpacing(6)
        self.lbl_quality_badge = QLabel("")
        self.lbl_quality_badge.setObjectName("qualityBadge")
        self.lbl_quality_badge.setVisible(False)
        quality_row.addWidget(self.lbl_quality_badge)
        quality_row.addStretch(1)

        self.visualizer = SpectrumVisualizerWidget(24, compact=False)
        self.visualizer.set_spectrum_callback(self.avatar.set_music_spectrum)

        info_col.addWidget(self.lbl_title)
        info_col.addWidget(self.lbl_artist)
        info_col.addWidget(self.lbl_album_meta)
        info_col.addLayout(quality_row)
        info_col.addWidget(self.visualizer)

        top_media_row.addLayout(info_col, 1)
        p_layout.addLayout(top_media_row)

        prog_col = QVBoxLayout()
        prog_col.setSpacing(3)

        self.progress = QSlider(Qt.Orientation.Horizontal)
        self.progress.setObjectName("trackSlider")
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setEnabled(False)
        prog_col.addWidget(self.progress)

        time_row = QHBoxLayout()
        self.lbl_pos = QLabel("00:00")
        self.lbl_dur = QLabel("00:00")
        self.lbl_pos.setStyleSheet("color: #cbd5e1; font-size: 11px; font-weight: 600; background: transparent;")
        self.lbl_dur.setStyleSheet("color: #cbd5e1; font-size: 11px; font-weight: 600; background: transparent;")
        time_row.addWidget(self.lbl_pos)
        time_row.addStretch(1)
        time_row.addWidget(self.lbl_dur)
        prog_col.addLayout(time_row)
        p_layout.addLayout(prog_col)

        ctrl_row = QHBoxLayout()
        ctrl_row.setSpacing(12)
        ctrl_row.addStretch(1)

        self.btn_dislike = QPushButton()
        self.btn_dislike.setObjectName("iconBtnDislike")
        self.btn_dislike.setIcon(make_vector_icon("dislike", 19, "#fb7185"))
        self.btn_dislike.setIconSize(QSize(19, 19))
        self.btn_dislike.setFixedSize(42, 42)
        self.btn_dislike.setToolTip("Не нравится (пропустить)")

        self.btn_shuffle = QPushButton()
        self.btn_shuffle.setObjectName("iconBtnSubtle")
        self.btn_shuffle.setIcon(make_vector_icon("shuffle", 17, "#bae6fd"))
        self.btn_shuffle.setIconSize(QSize(17, 17))
        self.btn_shuffle.setFixedSize(34, 34)
        self.btn_shuffle.setToolTip("Перемешать / Волна")

        self.btn_prev = QPushButton()
        self.btn_prev.setObjectName("iconBtn")
        self.btn_prev.setIcon(make_vector_icon("prev", 20, "#f8fafc"))
        self.btn_prev.setIconSize(QSize(20, 20))
        self.btn_prev.setFixedSize(42, 42)
        self.btn_prev.setToolTip("Предыдущий трек")

        self.btn_play = QPushButton()
        self.btn_play.setObjectName("playCircleBtn")
        self.btn_play.setIcon(self._icon_play)
        self.btn_play.setIconSize(QSize(26, 26))
        self.btn_play.setFixedSize(60, 60)
        self.btn_play.setToolTip("Пауза / Продолжить")

        self.btn_next = QPushButton()
        self.btn_next.setObjectName("iconBtn")
        self.btn_next.setIcon(make_vector_icon("next", 20, "#f8fafc"))
        self.btn_next.setIconSize(QSize(20, 20))
        self.btn_next.setFixedSize(42, 42)
        self.btn_next.setToolTip("Следующий трек")

        self.btn_like = QPushButton()
        self.btn_like.setObjectName("iconBtnLike")
        self.btn_like.setIcon(make_vector_icon("like", 19, "#38bdf8"))
        self.btn_like.setIconSize(QSize(19, 19))
        self.btn_like.setFixedSize(42, 42)
        self.btn_like.setToolTip("Нравится")

        for b in (self.btn_dislike, self.btn_shuffle, self.btn_prev, self.btn_play, self.btn_next, self.btn_like):
            b.setCursor(Qt.CursorShape.PointingHandCursor)

        self.btn_dislike.clicked.connect(lambda: self.send_music_ctrl("dislike"))
        self.btn_shuffle.clicked.connect(lambda: self.send_music_ctrl("shuffle"))
        self.btn_prev.clicked.connect(lambda: self.send_music_ctrl("prev"))
        self.btn_play.clicked.connect(self.toggle_play_pause)
        self.btn_next.clicked.connect(lambda: self.send_music_ctrl("next"))
        self.btn_like.clicked.connect(self._send_like)

        ctrl_row.addWidget(self.btn_dislike)
        ctrl_row.addWidget(self.btn_shuffle)
        ctrl_row.addWidget(self.btn_prev)
        ctrl_row.addWidget(self.btn_play)
        ctrl_row.addWidget(self.btn_next)
        ctrl_row.addWidget(self.btn_like)
        ctrl_row.addStretch(1)
        p_layout.addLayout(ctrl_row)

        vol_row = QHBoxLayout()
        vol_row.setSpacing(8)

        lbl_vol_icon = QLabel()
        lbl_vol_icon.setPixmap(make_vector_icon("volume", 16, "#bae6fd").pixmap(16, 16))
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
        self.lbl_vol_val.setStyleSheet("color: #e2e8f0; font-size: 11px; font-weight: 600; background: transparent;")

        self.btn_eq_toggle = QPushButton(" Эквалайзер")
        self.btn_eq_toggle.setObjectName("chipBtn")
        self.btn_eq_toggle.setIcon(make_vector_icon("eq", 14, "#7dd3fc"))
        self.btn_eq_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_eq_toggle.clicked.connect(self._toggle_eq_cards)

        self.eq_combo = QComboBox()
        self.eq_combo.setObjectName("eqCombo")
        self.eq_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.eq_combo.setToolTip("Пресет 10-полосного эквалайзера VLC")
        self.eq_combo.addItem("Профиль: Выкл", None)
        for idx, title_ru in _load_eq_presets():
            self.eq_combo.addItem(f"{title_ru}", idx)
        self.eq_combo.currentIndexChanged.connect(self._on_eq_changed)

        vol_row.addWidget(lbl_vol_icon)
        vol_row.addWidget(self.vol_slider, 1)
        vol_row.addWidget(self.lbl_vol_val)
        vol_row.addSpacing(6)
        vol_row.addWidget(self.btn_eq_toggle)
        vol_row.addWidget(self.eq_combo)
        p_layout.addLayout(vol_row)

        # Ряд из 7 проработанных стеклянных плиток пресетов эквалайзера
        self.eq_cards_container = QWidget()
        self.eq_cards_container.setStyleSheet("background: transparent;")
        eq_cards_row = QHBoxLayout(self.eq_cards_container)
        eq_cards_row.setContentsMargins(0, 2, 0, 0)
        eq_cards_row.setSpacing(6)

        self._eq_tiles: list[EqPresetTile] = []
        for title, icon_kind, preset_idx in _QUICK_EQ_CARDS:
            tile = EqPresetTile(title, icon_kind, preset_idx)
            tile.clicked.connect(self._select_quick_eq)
            eq_cards_row.addWidget(tile, 1)
            self._eq_tiles.append(tile)

        p_layout.addWidget(self.eq_cards_container)

        self.stack.addWidget(avatar_page)  # index 0
        self.stack.addWidget(player_page)  # index 1
        layout.addWidget(self.stack, 1)

    # ── Управление ────────────────────────────────────────────────────
    def _toggle_eq_cards(self):
        self.eq_cards_container.setVisible(not self.eq_cards_container.isVisible())

    def _select_quick_eq(self, preset_id: int):
        # Повторное нажатие на активную плитку выключает эквалайзер
        if self._active_eq_idx == preset_id:
            self.eq_combo.setCurrentIndex(0)
            self.send_music_ctrl("eq_preset", preset=None)
            self._highlight_eq_card(None)
            return

        idx = self.eq_combo.findData(preset_id)
        if idx >= 0:
            self.eq_combo.setCurrentIndex(idx)
        else:
            self.send_music_ctrl("eq_preset", preset=preset_id)
        self._highlight_eq_card(preset_id)

    def _highlight_eq_card(self, preset_id: int | None):
        self._active_eq_idx = preset_id
        for tile in self._eq_tiles:
            tile.set_active(preset_id is not None and tile.preset_id == preset_id)

    def _toggle_manual_view(self):
        if self.stack.currentIndex() == 0:
            self._force_view = "player"
            self.stack.setCurrentIndex(1)
            self.btn_switch_view.setText("Макс")
        else:
            self._force_view = "avatar"
            self.stack.setCurrentIndex(0)
            self.btn_switch_view.setText("Плеер")
            self.set_accent_color(QColor("#38bdf8"))

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
        self._highlight_eq_card(preset_id)
        self.send_music_ctrl("eq_preset", preset=preset_id)

    def _on_cover_loaded(self, key: str, pix: QPixmap, accent: QColor):
        if key == self._cover_loader.current_key:
            self.lbl_cover.setPixmap(pix)
            if self.stack.currentIndex() == 1:
                self.set_accent_color(accent)
            self.visualizer.set_accent_color(accent)

    # ── Обновление состояния от главного окна ─────────────────────────
    def update_state(
        self,
        st: dict,
        music_alive: bool,
        tts_speaking: bool,
        expecting: bool,
        mic_level: float = 0.0,
        tts_level: float = 0.0,
        emotion: str = "neutral",
        last_active_ts: float | None = None,
        is_sleeping: bool = False,
    ):
        music_active = music_alive and bool(st.get("playing") or st.get("paused"))

        for name, value in (
            ("set_mic_level", float(mic_level)),
            ("set_speech_level", float(tts_level)),
            ("set_emotion", str(emotion)),
        ):
            fn = getattr(self.avatar, name, None)
            if callable(fn):
                try:
                    fn(value)
                except Exception:
                    pass

        artist_short = (st.get("artist") or "").strip()
        if tts_speaking:
            av_state = "speaking"
            self.wave_widget.set_state("speaking", "#38bdf8")
            self.lbl_status_text.setText("Макс говорит...")
            self.lbl_avatar_caption.setText("Отвечаю...")
        elif expecting:
            av_state = "listening"
            self.wave_widget.set_state("listening", "#f59e0b")
            self.lbl_status_text.setText("Жду ответ...")
            self.lbl_avatar_caption.setText("Слушаю вас...")
        elif emotion == "curious" and last_active_ts and (time.time() - last_active_ts) < 8.0:
            av_state = "curious"
            self.wave_widget.set_state("curious", "#a78bfa")
            self.lbl_status_text.setText("Интересный вопрос...")
            self.lbl_avatar_caption.setText("Думаю...")
        elif music_alive and st.get("playing"):
            av_state = "music"
            self.wave_widget.set_state("music", "#38bdf8")
            if artist_short:
                self.lbl_status_text.setText(f"Макс слушает «{artist_short}»")
            else:
                self.lbl_status_text.setText("Макс слушает («Макс...»)")
            self.lbl_avatar_caption.setText("Играет музыка")
        elif is_sleeping:
            av_state = "sleep"
            self.wave_widget.set_state("idle", "#64748b")
            self.lbl_status_text.setText("Макс отдыхает")
            self.lbl_avatar_caption.setText("Сплю. Скажите «Макс...»")
        else:
            av_state = "idle"
            self.wave_widget.set_state("idle", "#38bdf8")
            self.lbl_status_text.setText("Макс слушает («Макс...»)")
            self.lbl_avatar_caption.setText("Скажите «Макс...» или напишите команду")

        self.avatar.set_state(av_state)

        if tts_speaking and tts_level <= 0.0:
            self.avatar.set_speech_level(self.visualizer.audio_level)

        if not music_active and self._force_view == "avatar":
            self._force_view = None

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
                self.set_accent_color(QColor("#38bdf8"))

        if music_active:
            self._is_playing = bool(st.get("playing"))
            self._dur_ms = int(st.get("dur_ms", 0) or 0)
            self._pos_ms = int(st.get("pos_ms", 0) or 0)

            title = st.get("title") or "Без названия"
            artist = st.get("artist") or "Неизвестный исполнитель"
            self.lbl_title.setText(title)
            self.lbl_artist.setText(artist)

            album = (st.get("album") or "").strip()
            year = (st.get("year") or "").strip()
            meta_parts = []
            if album:
                meta_parts.append(f"Альбом: {album}")
            if year:
                meta_parts.append(f"Год: {year}")
            if meta_parts:
                self.lbl_album_meta.setText("  •  ".join(meta_parts))
                self.lbl_album_meta.setVisible(True)
            else:
                self.lbl_album_meta.setVisible(False)

            bitrate = (st.get("bitrate") or "").strip()
            sample_rate = (st.get("sample_rate") or "").strip()
            codec = (st.get("codec") or "").strip()
            q_parts = [p for p in (codec, bitrate, sample_rate) if p]
            if q_parts:
                self.lbl_quality_badge.setText("♫  " + "  •  ".join(q_parts))
                self.lbl_quality_badge.setVisible(True)
            else:
                self.lbl_quality_badge.setVisible(False)

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
                preset_val = st.get("eq_preset")
                self._highlight_eq_card(preset_val)
                idx = self.eq_combo.findData(preset_val)
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
            self.lbl_quality_badge.setVisible(False)
            self.lbl_album_meta.setVisible(False)
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