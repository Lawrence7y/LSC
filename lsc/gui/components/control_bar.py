"""Control bar widget with timeline, playback controls, and mark in/out buttons."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap, QPolygonF
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from lsc.gui.theme import connect_theme_changed, get_theme
from lsc.utils.helpers import fmt_time

from .timeline import InlineTimeline


class ControlBar(QWidget):
    seek_back = Signal()
    seek_fwd = Signal()
    play_pause = Signal()
    export_clicked = Signal()
    mark_in_clicked = Signal()
    mark_out_clicked = Signal()
    return_live_clicked = Signal()
    fullscreen_clicked = Signal()
    preview_range_clicked = Signal()   # 试听选区

    def __init__(self, parent=None):
        super().__init__(parent)
        self._playing = False
        self._draw_background = True
        self._build()
        connect_theme_changed(self.refresh_theme)

    def set_draw_background(self, draw: bool) -> None:
        """是否由本控件自绘圆角背景（宿主容器提供背景时设为 False）。"""
        self._draw_background = draw
        self.update()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(10)

        self._timeline = InlineTimeline()
        self._timeline.setFixedHeight(60)
        root.addWidget(self._timeline)

        # 同步模式指示器（多选房间时显示）
        self._sync_indicator = QLabel("")
        self._sync_indicator.setObjectName("syncIndicator")
        self._sync_indicator.setVisible(False)
        root.addWidget(self._sync_indicator)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        root.addLayout(row)

        self._back = QPushButton("10s")
        self._back.setObjectName("ctrlSecondary")
        self._back.setFixedSize(54, 32)
        self._back.setIconSize(QSize(12, 12))
        self._back.setToolTip("后退 10 秒")
        self._back.clicked.connect(self.seek_back.emit)
        row.addWidget(self._back)

        self._play = QPushButton("")
        self._play.setFixedSize(36, 36)
        self._play.setIconSize(QSize(18, 18))
        self._play.setToolTip("播放")
        self._play.clicked.connect(self._on_play_pause)
        row.addWidget(self._play)

        self._fwd = QPushButton("10s")
        self._fwd.setObjectName("ctrlSecondary")
        self._fwd.setFixedSize(54, 32)
        self._fwd.setIconSize(QSize(12, 12))
        self._fwd.setToolTip("前进 10 秒")
        self._fwd.clicked.connect(self.seek_fwd.emit)
        row.addWidget(self._fwd)

        self._time_label = QLabel("00:00:00 / 00:00:00")
        self._time_label.setObjectName("time_label")
        self._time_label.setAlignment(Qt.AlignCenter)
        # 根据等宽字体完整时间字符串留出宽度，避免最后一位被截断
        self._time_label.setFixedWidth(180)
        row.addWidget(self._time_label)

        row.addStretch(1)

        self._mark_in = QPushButton("入点")
        self._mark_in.setCheckable(True)
        self._mark_in.setToolTip("将当前时间设为片段开始")
        self._mark_in.setMinimumWidth(72)
        self._mark_in.clicked.connect(self.mark_in_clicked.emit)
        row.addWidget(self._mark_in)

        self._mark_out = QPushButton("出点")
        self._mark_out.setCheckable(True)
        self._mark_out.setToolTip("将当前时间设为片段结束")
        self._mark_out.setMinimumWidth(72)
        self._mark_out.clicked.connect(self.mark_out_clicked.emit)
        row.addWidget(self._mark_out)

        self._export = QPushButton("导出")
        self._export.setEnabled(False)
        self._export.setMinimumWidth(48)
        self._export.clicked.connect(self.export_clicked.emit)
        row.addWidget(self._export)

        self._return_live = QPushButton("回直播")
        self._return_live.setObjectName("ctrlSecondary")
        self._return_live.setToolTip("切回当前直播画面")
        self._return_live.setMinimumWidth(54)
        self._return_live.clicked.connect(self.return_live_clicked.emit)
        row.addWidget(self._return_live)

        self._fullscreen = QPushButton("全屏")
        self._fullscreen.setObjectName("ctrlSecondary")
        self._fullscreen.setToolTip("进入全屏播放器")
        self._fullscreen.setMinimumWidth(48)
        self._fullscreen.setVisible(False)
        self._fullscreen.clicked.connect(self.fullscreen_clicked.emit)
        row.addWidget(self._fullscreen)

        self._preview_range = QPushButton("试听选区")
        self._preview_range.setObjectName("ctrlSecondary")
        self._preview_range.setCheckable(True)
        self._preview_range.setToolTip("循环播放当前入/出点区间")
        self._preview_range.setMinimumWidth(64)
        self._preview_range.clicked.connect(self.preview_range_clicked.emit)
        row.addWidget(self._preview_range)

        self._apply_style()
        self._refresh_control_icons()

    def _apply_style(self):
        # 样式已迁移到 theme.py generate_stylesheet，使用 objectName 驱动
        self._mark_in.setObjectName("ctrlMarkIn")
        self._mark_out.setObjectName("ctrlMarkOut")
        self._export.setObjectName("ctrlExport")
        self._play.setObjectName("ctrlPlay")
        # Re-polish 使 objectName 变更生效
        for w in (self._mark_in, self._mark_out, self._export, self._play):
            w.style().unpolish(w)
            w.style().polish(w)

    def _make_control_icon(self, kind: str, normal_color: str) -> QIcon:
        c = get_theme()
        icon = QIcon()
        icon.addPixmap(self._draw_control_icon(kind, normal_color), QIcon.Normal)
        icon.addPixmap(self._draw_control_icon(kind, c.text_tertiary), QIcon.Disabled)
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

        p.end()
        return pix

    def _refresh_control_icons(self) -> None:
        c = get_theme()
        self._back.setIcon(self._make_control_icon("back", c.text_secondary))
        self._fwd.setIcon(self._make_control_icon("fwd", c.text_secondary))
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

    def set_recording(self, recording: bool):
        self._back.setEnabled(recording)
        self._fwd.setEnabled(recording)
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
        # 已设置入/出点时,按钮直接显示时间值,让用户一眼看到选区位置
        self._mark_in.setText(f"入 {fmt_time(in_time)}" if has_in and in_time is not None else "入点")
        self._mark_out.setText(f"出 {fmt_time(out_time)}" if has_out and out_time is not None else "出点")
        # 只有同时有入点和出点时才启用试听
        self._preview_range.setEnabled(has_in and has_out)

    def set_export_enabled(self, enabled: bool):
        self._export.setEnabled(enabled)

    def set_live_available(self, enabled: bool):
        """Toggle the 'return to live' button availability."""
        self._return_live.setEnabled(enabled)

    def set_fullscreen(self, enabled: bool):
        """Toggle fullscreen button text between '全屏' and '退出全屏'."""
        self._fullscreen.setText("退出全屏" if enabled else "全屏")

    def set_fullscreen_visible(self, visible: bool) -> None:
        """Show the legacy control-bar fullscreen button when a host needs it."""
        self._fullscreen.setVisible(visible)

    def set_range_looping(self, looping: bool) -> None:
        """更新试听按钮的选中状态。"""
        self._preview_range.setChecked(looping)
        self._preview_range.setText("停止试听" if looping else "试听选区")

    def set_range_loop_progress(self, elapsed: float, total: float) -> None:
        """试听进行中时,按钮显示当前进度,让用户感知循环位置。"""
        if self._preview_range.isChecked():
            self._preview_range.setText(f"试听 {fmt_time(elapsed)}/{fmt_time(total)}")

    def set_sync_count(self, count: int) -> None:
        """Update the sync mode indicator. count > 1 means multi-room sync."""
        if count > 1:
            self._sync_indicator.setText(f"同步模式 ({count} 个房间)")
            self._sync_indicator.setVisible(True)
        else:
            self._sync_indicator.setVisible(False)

    def set_time(self, position: float, duration: float):
        self._time_label.setText(f"{fmt_time(position)} / {fmt_time(duration)}")

    @property
    def timeline(self) -> InlineTimeline:
        return self._timeline

    @property
    def mark_in_button(self) -> QPushButton:
        return self._mark_in

    @property
    def mark_out_button(self) -> QPushButton:
        return self._mark_out
