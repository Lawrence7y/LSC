"""Control bar widget with timeline, playback controls, and mark in/out buttons."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from lsc.gui.theme import connect_theme_changed, get_theme
from lsc.utils.helpers import fmt_time

from .timeline import TIMELINE_HEIGHT, InlineTimeline


class ControlBar(QWidget):
    seek_back = Signal()
    seek_fwd = Signal()
    play_pause = Signal()
    stop_clicked = Signal()
    export_clicked = Signal()
    mark_in_clicked = Signal()
    mark_out_clicked = Signal()
    clear_selection_clicked = Signal()
    add_clip_clicked = Signal()
    fullscreen_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._playing = False
        self._draw_background = True
        self._build()
        connect_theme_changed(self.refresh_theme)

    def set_draw_background(self, draw: bool) -> None:
        self._draw_background = draw
        self.update()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._timeline = InlineTimeline()
        self._timeline.setFixedHeight(TIMELINE_HEIGHT)
        root.addWidget(self._timeline)

        # 同步模式指示器
        self._sync_indicator = QLabel("")
        self._sync_indicator.setObjectName("syncIndicator")
        self._sync_indicator.setVisible(False)
        root.addWidget(self._sync_indicator)

        # 按钮行
        self._buttons_scroll = QScrollArea()
        self._buttons_scroll.setFrameShape(QScrollArea.NoFrame)
        self._buttons_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._buttons_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._buttons_scroll.setWidgetResizable(True)
        row_widget = QWidget()
        row = QHBoxLayout(row_widget)
        row.setContentsMargins(8, 4, 8, 4)
        row.setSpacing(4)
        self._buttons_scroll.setWidget(row_widget)
        root.addWidget(self._buttons_scroll)

        # ── 播放控制组 ──
        self._back = QPushButton("5s")
        self._back.setObjectName("ctrlSecondary")
        self._back.setFixedWidth(42)
        self._back.setIconSize(QSize(12, 12))
        self._back.setToolTip("后退 5 秒 (←)")
        self._back.clicked.connect(self.seek_back.emit)
        row.addWidget(self._back)

        self._play = QPushButton("")
        self._play.setFixedWidth(32)
        self._play.setIconSize(QSize(16, 16))
        self._play.setToolTip("播放/暂停 (Space)")
        self._play.clicked.connect(self._on_play_pause)
        row.addWidget(self._play)

        self._stop = QPushButton("")
        self._stop.setObjectName("ctrlSecondary")
        self._stop.setFixedWidth(32)
        self._stop.setIconSize(QSize(14, 14))
        self._stop.setToolTip("停止")
        self._stop.clicked.connect(self.stop_clicked.emit)
        row.addWidget(self._stop)

        self._fwd = QPushButton("5s")
        self._fwd.setObjectName("ctrlSecondary")
        self._fwd.setFixedWidth(42)
        self._fwd.setIconSize(QSize(12, 12))
        self._fwd.setToolTip("前进 5 秒 (→)")
        self._fwd.clicked.connect(self.seek_fwd.emit)
        row.addWidget(self._fwd)

        # 分隔线
        sep1 = QWidget()
        sep1.setFixedSize(1, 20)
        sep1.setObjectName("ctrlSeparator")
        row.addWidget(sep1)

        # ── 时间码 ──
        self._time_label = QLabel("00:00:00")
        self._time_label.setObjectName("time_label")
        self._time_label.setAlignment(Qt.AlignCenter)
        self._time_label.setFixedWidth(90)
        row.addWidget(self._time_label)

        # 分隔线
        sep2 = QWidget()
        sep2.setFixedSize(1, 20)
        sep2.setObjectName("ctrlSeparator")
        row.addWidget(sep2)

        # ── 编码标签 ──
        self._codec_hw = QLabel("H/W")
        self._codec_hw.setObjectName("codecTagHighlight")
        self._codec_codec = QLabel("AVC1")
        self._codec_codec.setObjectName("codecTag")
        self._codec_audio = QLabel("AAC")
        self._codec_audio.setObjectName("codecTag")
        self._codec_channels = QLabel("2.0")
        self._codec_channels.setObjectName("codecTag")
        for tag in (self._codec_hw, self._codec_codec, self._codec_audio, self._codec_channels):
            row.addWidget(tag)

        row.addStretch(1)

        # ── 选区操作组 ──
        self._mark_in = QPushButton("入点")
        self._mark_in.setObjectName("ctrlMarkIn")
        self._mark_in.setToolTip("入点 (I)")
        self._mark_in.setFixedWidth(54)
        self._mark_in.clicked.connect(self.mark_in_clicked.emit)
        row.addWidget(self._mark_in)

        self._mark_out = QPushButton("出点")
        self._mark_out.setObjectName("ctrlMarkOut")
        self._mark_out.setToolTip("出点 (O)")
        self._mark_out.setFixedWidth(54)
        self._mark_out.clicked.connect(self.mark_out_clicked.emit)
        row.addWidget(self._mark_out)

        self._clear_sel = QPushButton("✕")
        self._clear_sel.setObjectName("ctrlSecondary")
        self._clear_sel.setFixedWidth(28)
        self._clear_sel.setToolTip("清除选区 (X)")
        self._clear_sel.clicked.connect(self.clear_selection_clicked.emit)
        row.addWidget(self._clear_sel)

        self._add_clip = QPushButton("+")
        self._add_clip.setObjectName("ctrlAddClip")
        self._add_clip.setFixedWidth(28)
        self._add_clip.setToolTip("添加到切片列表")
        self._add_clip.clicked.connect(self.add_clip_clicked.emit)
        row.addWidget(self._add_clip)

        # 分隔线
        sep3 = QWidget()
        sep3.setFixedSize(1, 20)
        sep3.setObjectName("ctrlSeparator")
        row.addWidget(sep3)

        # ── 导出/视图组 ──
        self._export = QPushButton("导出")
        self._export.setObjectName("ctrlExport")
        self._export.setEnabled(False)
        self._export.setFixedWidth(48)
        self._export.clicked.connect(self.export_clicked.emit)
        row.addWidget(self._export)

        self._fullscreen = QPushButton("全屏")
        self._fullscreen.setObjectName("ctrlSecondary")
        self._fullscreen.setFixedWidth(48)
        self._fullscreen.setVisible(False)
        self._fullscreen.clicked.connect(self.fullscreen_clicked.emit)
        row.addWidget(self._fullscreen)

        # 分隔线
        sep4 = QWidget()
        sep4.setFixedSize(1, 20)
        sep4.setObjectName("ctrlSeparator")
        row.addWidget(sep4)

        # ── 音量 ──
        self._mute_btn = QPushButton("🔊")
        self._mute_btn.setObjectName("ctrlSecondary")
        self._mute_btn.setFixedWidth(28)
        self._mute_btn.setToolTip("静音")
        self._mute_btn.clicked.connect(self._on_mute_toggle)
        row.addWidget(self._mute_btn)

        self._volume_slider = QSlider(Qt.Horizontal)
        self._volume_slider.setFixedWidth(60)
        self._volume_slider.setRange(0, 100)
        self._volume_slider.setValue(80)
        self._volume_slider.setObjectName("volumeSlider")
        self._volume_slider.setToolTip("音量")
        row.addWidget(self._volume_slider)

        self._muted = False
        self._apply_style()
        self._refresh_control_icons()

    def _apply_style(self):
        self._mark_in.setObjectName("ctrlMarkIn")
        self._mark_out.setObjectName("ctrlMarkOut")
        self._export.setObjectName("ctrlExport")
        self._play.setObjectName("ctrlPlay")
        for w in (self._mark_in, self._mark_out, self._export, self._play):
            w.style().unpolish(w)
            w.style().polish(w)

    def _make_control_icon(self, kind: str, normal_color: str) -> QIcon:
        icon = QIcon()
        icon.addPixmap(self._draw_control_icon(kind, normal_color), QIcon.Normal)
        icon.addPixmap(self._draw_control_icon(kind, get_theme().text_tertiary), QIcon.Disabled)
        return icon

    def _draw_control_icon(self, kind: str, color: str) -> QPixmap:
        pix = QPixmap(20, 20)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        qcolor = QColor(color)
        p.setPen(qcolor)
        p.setBrush(qcolor)

        if kind == "play":
            p.drawPolygon(QPolygonF([QPointF(7, 4), QPointF(16, 10), QPointF(7, 16)]))
        elif kind == "pause":
            p.drawRoundedRect(QRectF(6, 4, 3.5, 12), 1, 1)
            p.drawRoundedRect(QRectF(11, 4, 3.5, 12), 1, 1)
        elif kind == "back":
            p.drawPolygon(QPolygonF([QPointF(13, 4), QPointF(6, 10), QPointF(13, 16)]))
        elif kind == "fwd":
            p.drawPolygon(QPolygonF([QPointF(7, 4), QPointF(14, 10), QPointF(7, 16)]))
        elif kind == "stop":
            p.drawRoundedRect(QRectF(5, 5, 10, 10), 1, 1)

        p.end()
        return pix

    def _refresh_control_icons(self) -> None:
        c = get_theme()
        self._back.setIcon(self._make_control_icon("back", c.text_secondary))
        self._fwd.setIcon(self._make_control_icon("fwd", c.text_secondary))
        self._stop.setIcon(self._make_control_icon("stop", c.text_secondary))
        self._play.setIcon(self._make_control_icon("pause" if self._playing else "play", c.accent_primary))

    def paintEvent(self, e):
        if not self._draw_background:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = get_theme()
        p.setBrush(QColor(c.bg_secondary))
        p.setPen(QColor(c.border_subtle))
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 10, 10)
        p.end()

    def refresh_theme(self) -> None:
        self._apply_style()
        self._refresh_control_icons()
        self.update()

    def _on_play_pause(self):
        self._playing = not self._playing
        self._play.setToolTip("暂停" if self._playing else "播放")
        self._refresh_control_icons()
        self.play_pause.emit()

    def _on_mute_toggle(self):
        self._muted = not self._muted
        self._mute_btn.setText("🔇" if self._muted else "🔊")
        self._mute_btn.setToolTip("取消静音" if self._muted else "静音")

    def set_recording(self, recording: bool):
        self._back.setEnabled(recording)
        self._fwd.setEnabled(recording)
        self._stop.setEnabled(recording)
        self._mark_in.setEnabled(recording)
        self._mark_out.setEnabled(recording)
        self._play.setEnabled(recording)
        self._refresh_control_icons()

    def set_playing(self, playing: bool):
        self._playing = playing
        self._refresh_control_icons()

    def set_range_state(self, has_in: bool = False, has_out: bool = False,
                        in_time: float | None = None, out_time: float | None = None):
        self._mark_in.setChecked(has_in)
        self._mark_out.setChecked(has_out)
        self._mark_in.setText(f"入 {fmt_time(in_time)}" if has_in and in_time is not None else "入点")
        self._mark_out.setText(f"出 {fmt_time(out_time)}" if has_out and out_time is not None else "出点")

    def set_export_enabled(self, enabled: bool):
        self._export.setEnabled(enabled)

    def set_fullscreen(self, enabled: bool):
        self._fullscreen.setText("退出全屏" if enabled else "全屏")

    def set_fullscreen_visible(self, visible: bool) -> None:
        self._fullscreen.setVisible(visible)

    def set_sync_count(self, count: int) -> None:
        if count > 1:
            self._sync_indicator.setText(f"同步模式 ({count} 个房间)")
            self._sync_indicator.setVisible(True)
        else:
            self._sync_indicator.setVisible(False)

    def set_time(self, position: float, duration: float):
        self._time_label.setText(fmt_time(position))

    def set_codec_info(self, hw: str = "", codec: str = "", audio: str = "", channels: str = ""):
        if hw:
            self._codec_hw.setText(hw)
        if codec:
            self._codec_codec.setText(codec)
        if audio:
            self._codec_audio.setText(audio)
        if channels:
            self._codec_channels.setText(channels)

    @property
    def timeline(self) -> InlineTimeline:
        return self._timeline

    @property
    def mark_in_button(self) -> QPushButton:
        return self._mark_in

    @property
    def mark_out_button(self) -> QPushButton:
        return self._mark_out
