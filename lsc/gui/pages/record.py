"""
Record page - 1:1 replica of ui-design-prototype.html
"""

import math
import os
import time as _time
from datetime import datetime

from lsc import get_logger
_log = get_logger(__name__)

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRect,
    QSettings,
    QSize,
    Qt,
    QTimer,
    Signal,
    Property,
)
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen, QPolygon
from PySide6.QtWidgets import (
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from lsc.utils.helpers import fmt_time as _fmt_time

from ..components.control_bar import ControlBar as SharedControlBar
from ..components.fullscreen_preview import FullscreenPreview
from ..components.mpv_widget import MpvWidget
from ..components.preview_surface import PreviewSurface
from ..components.timeline import InlineTimeline as SharedTimeline
from ..components.widgets import Card, ChipGroup, EmptyState, FadeInWidget, InputField, ParamPanel
from ..theme import get_option_button_palette, get_theme, is_dark
from .recording_controller import RecordingController

_ESTIMATED_MB_PER_SEC = 0.45  # Rough estimate for 1080p H.264


def _label(text, style="secondary", size=12):
    """Create a themed label with objectName-based styling."""
    lbl = QLabel(text)
    lbl.setObjectName(f"label_{style}")
    lbl.setProperty("fontSize", size)
    return lbl


class _FullscreenOverlayButton(QPushButton):
    """Small corner icon button used over video previews."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._exit_mode = False
        self.setObjectName("previewFullscreenButton")
        self.setFixedSize(36, 36)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("全屏")

    def set_exit_mode(self, exit_mode: bool) -> None:
        self._exit_mode = exit_mode
        self.setToolTip("退出全屏" if exit_mode else "全屏")
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg = QColor(0, 0, 0, 125 if not self.underMouse() else 170)
        p.setBrush(bg)
        p.setPen(QColor(255, 255, 255, 45))
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 8, 8)

        p.setPen(QPen(QColor(255, 255, 255, 235), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        if self._exit_mode:
            # Inward corners: collapse back to the normal preview.
            p.drawLine(12, 12, 17, 12)
            p.drawLine(12, 12, 12, 17)
            p.drawLine(24, 12, 19, 12)
            p.drawLine(24, 12, 24, 17)
            p.drawLine(12, 24, 17, 24)
            p.drawLine(12, 24, 12, 19)
            p.drawLine(24, 24, 19, 24)
            p.drawLine(24, 24, 24, 19)
        else:
            # Outward corners: enter fullscreen.
            p.drawLine(10, 10, 16, 10)
            p.drawLine(10, 10, 10, 16)
            p.drawLine(26, 10, 20, 10)
            p.drawLine(26, 10, 26, 16)
            p.drawLine(10, 26, 16, 26)
            p.drawLine(10, 26, 10, 20)
            p.drawLine(26, 26, 20, 26)
            p.drawLine(26, 26, 26, 20)
        p.end()

class _RecBadge(QWidget):
    """录制中徽章:右上角脉冲红点 + 时间,mpv 可见时半透明叠加。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._time = "00:00:00"
        self._visible = False
        self._pulse = 0.0
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        anim = QPropertyAnimation(self, b"pulse", self)
        anim.setDuration(1200)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.InOutSine)
        anim.setLoopCount(-1)
        self._anim = anim
        self.adjustSize()
        self.setVisible(False)

    def get_pulse(self) -> float:
        return self._pulse

    def set_pulse(self, v: float) -> None:
        self._pulse = v
        self.update()

    pulse = Property(float, get_pulse, set_pulse)

    def set_time(self, t: str) -> None:
        self._time = t
        self.update()

    def set_recording(self, on: bool) -> None:
        self._visible = on
        self.setVisible(on)
        if on:
            self._anim.start()
        else:
            self._anim.stop()
            self._pulse = 0.0

    def sizeHint(self) -> QSize:
        return QSize(160, 30)

    def paintEvent(self, _e) -> None:
        if not self._visible:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 160))
        p.drawRoundedRect(r, 8, 8)
        pulse = 0.5 + 0.5 * self._pulse
        cx = r.left() + 16
        cy = r.center().y()
        p.setBrush(QColor(220, 30, 30, int(100 + 120 * pulse)))
        p.drawEllipse(QPointF(cx, cy), 6, 6)
        p.setPen(QColor(255, 255, 255, 200))
        p.setFont(QFont("JetBrains Mono", 10))
        p.drawText(QRect(r.left() + 28, r.top(), r.width() - 32, r.height()),
                   Qt.AlignmentFlag.AlignVCenter, f"录制中 {self._time}")
        p.end()


class VideoPreview(PreviewSurface):
    """直播录制页预览容器。

    基于共享 `PreviewSurface`:占位 + 嵌入 MpvWidget + 右上 REC 徽章 +
    右下全屏角标 + 底部 hover 覆盖层(播放/静音)。底部覆盖层与下方
    `SharedControlBar` 联动(两处都保留播放控制,符合统一设计)。

    保留原 VideoPreview 的全部方法接口,RecordPage 调用点无需改动。
    """

    fullscreen_clicked = Signal()
    play_pause_clicked = Signal()
    mute_toggled = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("recordPreviewArea")
        self.setMinimumSize(400, 225)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        # mpv 惰性创建,普通构造与 offscreen 测试不启动 mpv
        self._mpv_widget: MpvWidget | None = None

        # 占位
        self._placeholder = QLabel("直播画面预览区域")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setObjectName("recordPreviewPlaceholder")
        self.set_content_widget(self._placeholder)

        # 右上 REC 徽章
        self._rec_badge = _RecBadge(self)
        self._rec_badge.setVisible(False)
        self.set_badge_widget(self._rec_badge)

        # 右下全屏角标
        self._fullscreen_btn = _FullscreenOverlayButton(self)
        self._fullscreen_btn.clicked.connect(self.fullscreen_clicked.emit)
        self.set_corner_widget(self._fullscreen_btn)

        # 底部 hover 覆盖层:播放/暂停 + 静音
        self._overlay = QWidget(self)
        self._overlay.setObjectName("recordPreviewOverlay")
        ol = QHBoxLayout(self._overlay)
        ol.setContentsMargins(8, 4, 8, 4)
        ol.setSpacing(8)
        self._play_btn = QPushButton("播放")
        self._play_btn.setObjectName("ctrlPlay")
        self._play_btn.setFixedHeight(26)
        self._play_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._play_btn.clicked.connect(self.play_pause_clicked.emit)
        self._mute_btn = QPushButton("静音")
        self._mute_btn.setObjectName("ctrlSecondary")
        self._mute_btn.setCheckable(True)
        self._mute_btn.setFixedHeight(26)
        self._mute_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._mute_btn.toggled.connect(self.mute_toggled.emit)
        ol.addWidget(self._play_btn)
        ol.addStretch(1)
        ol.addWidget(self._mute_btn)
        self.set_controls_widget(self._overlay)

        self._recording = False
        self._time = "00:00:00"

    # ── mpv lifecycle ──────────────────────────────────────────

    def _ensure_mpv_widget(self) -> MpvWidget:
        if self._mpv_widget is None:
            # 先移掉占位,再嵌入 mpv,避免两个 widget 竞争同一个 layout 空间
            self.clear_content()
            self._mpv_widget = MpvWidget()
            self._mpv_widget.setStyleSheet("border-radius:14px;")
            self._mpv_widget.hide()
            self.content_layout.addWidget(self._mpv_widget)
        return self._mpv_widget

    def play_video(self, path):
        if not os.path.isfile(path):
            return
        self.stop_video()
        player = self._ensure_mpv_widget()
        player.show()
        player.play_video(path, live=False)

    def play_live(self, path):
        player = self._ensure_mpv_widget()
        player.show()
        player.play_live(path)

    def stop_video(self):
        if self._mpv_widget is not None:
            self._mpv_widget.stop_video()
            self._mpv_widget.hide()
        # 恢复占位
        if self._layout.count() == 0 or self._layout.itemAt(0).widget() is not self._placeholder:
            self.set_content_widget(self._placeholder)

    def is_playing(self):
        return bool(self._mpv_widget and self._mpv_widget.is_playing())

    def toggle_play_pause(self):
        if self._mpv_widget is not None:
            self._mpv_widget.toggle_play_pause()

    def seek_to(self, sec):
        if self._mpv_widget is not None:
            self._mpv_widget.seek_to(sec)

    def position_sec(self):
        if self._mpv_widget is None:
            return 0.0
        return self._mpv_widget.position_sec()

    def duration_sec(self):
        if self._mpv_widget is None:
            return 0.0
        return self._mpv_widget.duration_sec()

    def cleanup(self):
        if self._mpv_widget is not None:
            self._mpv_widget.cleanup()

    # ── state ─────────────────────────────────────────────────

    def set_state(self, recording=False, connected=False, time="00:00:00"):
        self._recording = recording
        self._time = time
        self._rec_badge.set_time(time)
        self._rec_badge.set_recording(recording)
        self._play_btn.setText("暂停" if self.is_playing() else "播放")

    def set_fullscreen_mode(self, enabled: bool) -> None:
        self._fullscreen_btn.set_exit_mode(enabled)

    def paintEvent(self, e):
        # 占位背景:mpv 不可见时画,可见时让视频透出
        mpv_visible = bool(self._mpv_widget and self._mpv_widget.isVisible())
        if mpv_visible:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = get_theme()
        p.setBrush(QColor(c.bg_tertiary))
        p.setPen(QColor(c.border_subtle))
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 14, 14)
        p.end()


class IconButton(QWidget):
    clicked = Signal()

    def __init__(self, size, icon_fn, tooltip="", parent=None):
        super().__init__(parent)
        self._icon_fn = icon_fn
        self._hover = False
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(tooltip)
        self.setMouseTracking(True)

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.isEnabled():
            self.clicked.emit()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = get_theme()
        r = self.rect()
        color = QColor(c.text_primary if self._hover else c.text_secondary)
        self._icon_fn(p, r, color)


def _icon_seek_back(p, r, c):
    """Double left arrow."""
    p.setPen(QPen(c, 2.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.setBrush(Qt.NoBrush)
    cx, cy = r.center().x(), r.center().y()
    p.drawLine(cx + 3, cy - 6, cx - 5, cy)
    p.drawLine(cx - 5, cy, cx + 3, cy + 6)
    p.drawLine(cx - 3, cy - 6, cx - 11, cy)
    p.drawLine(cx - 11, cy, cx - 3, cy + 6)


def _icon_seek_fwd(p, r, c):
    """Double right arrow."""
    p.setPen(QPen(c, 2.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.setBrush(Qt.NoBrush)
    cx, cy = r.center().x(), r.center().y()
    p.drawLine(cx - 3, cy - 6, cx + 5, cy)
    p.drawLine(cx + 5, cy, cx - 3, cy + 6)
    p.drawLine(cx + 3, cy - 6, cx + 11, cy)
    p.drawLine(cx + 11, cy, cx + 3, cy + 6)


def _icon_stop(p, r, c):
    """Stop square."""
    p.setPen(Qt.NoPen)
    p.setBrush(c)
    s = 10
    cx, cy = r.center().x(), r.center().y()
    p.drawRoundedRect(cx - s // 2, cy - s // 2, s, s, 2, 2)


def _icon_play(p, r, c):
    """Play triangle."""
    p.setPen(Qt.NoPen)
    p.setBrush(c)
    cx, cy = r.center().x(), r.center().y()
    points = QPolygon([
        QPoint(cx - 5, cy - 7),
        QPoint(cx + 6, cy),
        QPoint(cx - 5, cy + 7),
    ])
    p.drawPolygon(points)


def _icon_pause(p, r, c):
    """Pause bars."""
    p.setPen(Qt.NoPen)
    p.setBrush(c)
    cx, cy = r.center().x(), r.center().y()
    p.drawRoundedRect(cx - 5, cy - 6, 4, 12, 1, 1)
    p.drawRoundedRect(cx + 1, cy - 6, 4, 12, 1, 1)


class ExportedCard(QWidget):
    clicked = Signal()

    def __init__(self, name, start, end, size, parent=None):
        super().__init__(parent)
        self._hover = False
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(120)
        self.setMinimumWidth(190)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # Thumb
        thumb = QWidget()
        thumb.setFixedHeight(60)
        thumb.setObjectName("card_thumb")
        thumb_lay = QVBoxLayout(thumb)
        thumb_lay.setAlignment(Qt.AlignCenter)
        thumb_lbl = QLabel("片段预览")
        thumb_lbl.setObjectName("label_tertiary")
        thumb_lay.addWidget(thumb_lbl, alignment=Qt.AlignCenter)

        # Duration badge
        dur = QLabel(_fmt_time(end - start))
        dur.setObjectName("duration_badge")
        dur.setFixedSize(dur.sizeHint().width() + 12, 18)
        dur.setParent(thumb)
        self._dur = dur
        self._thumb = thumb

        lay.addWidget(thumb)

        # Info
        info = QWidget()
        info.setObjectName("card_info")
        info_lay = QVBoxLayout(info)
        info_lay.setContentsMargins(12, 10, 12, 10)
        info_lay.setSpacing(4)

        name_lbl = QLabel(name)
        name_lbl.setObjectName("label_size")
        name_lbl.setWordWrap(False)
        info_lay.addWidget(name_lbl)

        meta = QLabel(f"{_fmt_time(start)} - {_fmt_time(end)} · {size}")
        meta.setObjectName("label_mono")
        info_lay.addWidget(meta)

        lay.addWidget(info)

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def showEvent(self, e):
        super().showEvent(e)
        # Position duration badge after layout is calculated
        if hasattr(self, '_dur') and hasattr(self, '_thumb'):
            self._dur.move(
                self._thumb.width() - self._dur.width() - 6,
                self._thumb.height() - self._dur.height() - 6
            )

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = get_theme()
        if self._hover:
            # Elevation effect: draw shadow below
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 30))
            p.drawRoundedRect(self.rect().adjusted(2, 4, -2, 2), 10, 10)
            p.setBrush(QColor(c.bg_secondary))
            p.setPen(QColor(c.border_default))
            p.drawRoundedRect(self.rect().adjusted(1, 1, -1, 1), 10, 10)
        else:
            p.setBrush(QColor(c.bg_secondary))
            p.setPen(QColor(c.border_subtle))
            p.drawRoundedRect(self.rect().adjusted(1, 1, -1, 1), 10, 10)
        p.end()


class ExportedClipsGrid(QWidget):
    clip_clicked = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(16)

        # Header
        hdr = QWidget()
        hdr_lay = QVBoxLayout(hdr)
        hdr_lay.setContentsMargins(0, 0, 0, 0)
        hdr_lay.setSpacing(3)
        t = QLabel("已导出片段")
        t.setObjectName("section_title")
        hdr_lay.addWidget(t)
        sub = QLabel("本次录制中导出的所有片段")
        sub.setObjectName("label_secondary")
        hdr_lay.addWidget(sub)
        self._layout.addWidget(hdr)

        # Grid
        self._grid_widget = QWidget()
        self._grid = QGridLayout(self._grid_widget)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(14)
        self._layout.addWidget(self._grid_widget)

        self._empty = EmptyState("暂无导出片段", "在上方时间轴上选取区间，点击「导出」按钮", icon_type="clip")
        self._layout.addWidget(self._empty)

        self._cards = []

    def add_clip(self, name, start, end, size):
        self._empty.hide()
        card = ExportedCard(name, start, end, size)
        idx = len(self._cards)
        card.clicked.connect(lambda i=idx: self.clip_clicked.emit(i))
        self._cards.append(card)
        col = (idx) % 4
        row = (idx) // 4
        self._grid.addWidget(card, row, col)

    def clear_clips(self):
        for card in self._cards:
            card.deleteLater()
        self._cards.clear()
        self._empty.show()


class AnalysisResultsGrid(QWidget):
    result_clicked = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(16)

        hdr = QWidget()
        hdr_lay = QVBoxLayout(hdr)
        hdr_lay.setContentsMargins(0, 0, 0, 0)
        hdr_lay.setSpacing(3)
        t = QLabel("分析高光")
        t.setObjectName("section_title")
        hdr_lay.addWidget(t)
        sub = QLabel("分析完成后，在这里快速浏览和导出候选高光")
        sub.setObjectName("label_secondary")
        hdr_lay.addWidget(sub)
        self._layout.addWidget(hdr)

        self._grid_widget = QWidget()
        self._grid = QGridLayout(self._grid_widget)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(14)
        self._layout.addWidget(self._grid_widget)

        self._empty = EmptyState("暂无分析结果", "完成录制后点击「分析当前录制」生成高光候选", icon_type="clip")
        self._layout.addWidget(self._empty)

        self._cards = []

    def set_results(self, results: list[dict]):
        self.clear_results()
        if not results:
            return

        self._empty.hide()
        for idx, result in enumerate(results):
            name = result.get("description") or f"高光 {idx + 1}"
            start = float(result.get("start_sec", 0.0))
            end = float(result.get("end_sec", start))
            score = float(result.get("score", 0.0))
            card = ExportedCard(name, start, end, f"score {score:.2f}")
            card.clicked.connect(lambda checked_idx=idx: self.result_clicked.emit(checked_idx))
            self._cards.append(card)
            col = idx % 4
            row = idx // 4
            self._grid.addWidget(card, row, col)

    def clear_results(self):
        for card in self._cards:
            card.deleteLater()
        self._cards.clear()
        self._empty.show()


class ConfigPanel(QWidget):
    connect_requested = Signal(str)
    start_record_requested = Signal()
    analyze_requested = Signal()
    export_analysis_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._settings = QSettings("LSC", "LiveStreamClipper")
        self._build()

    def _build(self):
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(16)

        card = Card()

        title = QLabel("录制配置")
        title.setObjectName("card_title")
        card.add_widget(title)

        # URL
        card.add_widget(_label("直播间链接"))
        url_lay = QHBoxLayout()
        self._url = InputField("https://live.douyin.com/xxx")
        url_lay.addWidget(self._url)
        self._connect_btn = QPushButton("连接")
        self._connect_btn.setFixedHeight(36)
        self._connect_btn.setObjectName("btnPrimary")
        self._connect_btn.clicked.connect(lambda: self.connect_requested.emit(self._url.text()))
        url_lay.addWidget(self._connect_btn)
        card.add_layout(url_lay)

        # Output dir
        card.add_widget(_label("输出目录"))
        default_output = self._settings.value("output_dir", os.path.join(os.path.expanduser("~"), "LSC", "recordings"))
        out_lay = QHBoxLayout()
        self._output = InputField(default_output)
        self._output.set_text(str(default_output))
        out_lay.addWidget(self._output)
        self._browse_btn = QPushButton("浏览")
        self._browse_btn.setFixedHeight(36)
        self._browse_btn.setObjectName("btnPrimary")
        self._browse_btn.clicked.connect(self._on_browse)
        out_lay.addWidget(self._browse_btn)
        card.add_layout(out_lay)

        # Quality
        card.add_widget(_label("画质预设"))
        self._quality = ChipGroup(["原画", "高清", "流畅"])
        card.add_widget(self._quality)

        # Encoder
        card.add_widget(_label("编码器"))
        self._encoder = ChipGroup(["H.264 NVENC", "H.264 CPU", "Copy"])
        self._encoder.selection_changed.connect(self._on_encoder_changed)
        card.add_widget(self._encoder)

        # Param mode
        self._param_label = _label("编码参数")
        card.add_widget(self._param_label)
        self._param = ChipGroup(["CRF 质量", "码率限制", "不限制"])
        self._param.selection_changed.connect(self._on_param_mode)
        card.add_widget(self._param)

        # Param panel
        self._param_panel = ParamPanel()
        card.add_widget(self._param_panel)

        # Start button
        self._start_btn = QPushButton("开始录制")
        self._start_btn.setFixedHeight(36)
        self._start_btn.setObjectName("btnPrimary")
        self._start_btn.clicked.connect(self.start_record_requested.emit)
        card.add_widget(self._start_btn)

        self._analyze_btn = QPushButton("分析当前录制")
        self._analyze_btn.setFixedHeight(36)
        self._analyze_btn.setObjectName("btnPrimary")
        self._analyze_btn.setEnabled(False)
        self._analyze_btn.clicked.connect(self.analyze_requested.emit)
        card.add_widget(self._analyze_btn)

        self._export_analysis_btn = QPushButton("导出分析高光")
        self._export_analysis_btn.setFixedHeight(36)
        self._export_analysis_btn.setObjectName("btnPrimary")
        self._export_analysis_btn.setEnabled(False)
        self._export_analysis_btn.clicked.connect(self.export_analysis_requested.emit)
        card.add_widget(self._export_analysis_btn)

        # Analysis profile
        card.add_widget(_label("分析 Profile"))
        self._analysis_profile = ChipGroup(["valorant", "fps", "generic"])
        card.add_widget(self._analysis_profile)

        self._layout.addWidget(card)

        # Info card
        info_card = Card()
        info_title = QLabel("录制信息")
        info_title.setObjectName("card_title")
        info_card.add_widget(info_title)

        self._info_grid = QWidget()
        gl = QGridLayout(self._info_grid)
        gl.setContentsMargins(0, 0, 0, 0)
        gl.setSpacing(14)

        self._info_values = {}
        for i, (label_text, key) in enumerate([
            ("分辨率", "res"), ("帧率", "fps"), ("编码", "codec"),
            ("编码参数", "bitrate"), ("文件大小", "size"), ("输出路径", "path"),
            ("分析结果", "analysis"), ("结果文件", "analysis_path"),
        ]):
            col = i % 2
            row = i // 2
            item = QWidget()
            il = QVBoxLayout(item)
            il.setContentsMargins(0, 0, 0, 0)
            il.setSpacing(4)
            lbl = QLabel(label_text)
            lbl.setObjectName("info_label")
            il.addWidget(lbl)
            val = QLabel("--")
            val.setObjectName("info_value")
            val.setWordWrap(True)  # Long paths should wrap, not expand the panel
            val.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            il.addWidget(val)
            self._info_values[key] = val
            gl.addWidget(item, row, col)

        info_card.add_widget(self._info_grid)
        self._layout.addWidget(info_card)
        self._layout.addStretch()

        # Initialize param panel state to match default encoder selection.
        # ChipGroup does NOT emit selection_changed on construction,
        # so we must call the handlers explicitly.
        self._load_saved_defaults()
        self._on_encoder_changed(self._encoder.selected)
        self._on_param_mode(self._param.selected)

    def _load_saved_defaults(self):
        encoder = self._settings.value("encoder", self._encoder.selected)
        if encoder in self._encoder._items:
            self._encoder._click(encoder)

        quality = self._settings.value("quality", self._quality.selected)
        quality_map = {
            "原画": "原画",
            "高清": "高清",
            "高清 1080p": "高清",
            "高清 720p": "高清",
            "标清": "流畅",
            "流畅": "流畅",
        }
        quality = quality_map.get(quality, quality)
        if quality in self._quality._items:
            self._quality._click(quality)

        param_mode = self._settings.value("param_mode", self._param.selected)
        if param_mode in self._param._items:
            self._param._click(param_mode)

        self._param_panel.set_crf_value(self._settings.value("crf", "23"))
        self._param_panel.set_bitrate_value(self._settings.value("bitrate_value", "8000"))
        self._param_panel.set_bitrate_unit(self._settings.value("bitrate_unit", "kbps"))

    def _on_encoder_changed(self, text):
        """When encoder changes, update param panel availability.

        Copy mode: no encoding params needed (stream saved as-is).
        NVENC: CRF maps to -cq, CBR maps to -b:v.
        CPU: all param modes fully supported.
        """
        is_copy = (text == "Copy")
        self._param.setEnabled(not is_copy)
        self._param_panel.setEnabled(not is_copy)
        self._param_label.setEnabled(not is_copy)
        if is_copy:
            # Dim the labels to indicate they're inactive
            self._param_label.setStyleSheet("opacity:0.4;")
        else:
            self._param_label.setStyleSheet("")

    def _on_param_mode(self, text):
        mode_map = {"CRF 质量": 0, "码率限制": 1, "不限制": 2}
        self._param_panel.set_mode(mode_map.get(text, 0))

    def _on_browse(self):
        path = QFileDialog.getExistingDirectory(self, "选择输出目录", self._output.text())
        if path:
            self._output.set_text(path)

    def set_connected(self, connected):
        if connected:
            self._connect_btn.setText("已连接")
            self._connect_btn.setEnabled(False)
        else:
            self._connect_btn.setText("连接")
            self._connect_btn.setEnabled(True)

    def set_connecting(self):
        self._connect_btn.setText("连接中...")
        self._connect_btn.setEnabled(False)

    def set_recording(self, recording):
        if recording:
            self._start_btn.setText("停止录制")
            self._start_btn.setObjectName("btnStopRecording")
            self._start_btn.setStyleSheet("")  # Let theme handle it
            self.set_analyze_enabled(False)
        else:
            self._start_btn.setText("开始录制")
            self._start_btn.setObjectName("btnPrimary")
            self._start_btn.setStyleSheet("")

    def set_info(self, key, value):
        if key in self._info_values:
            self._info_values[key].setText(value)

    # ── Public accessors (replace direct private-member access) ──

    @property
    def output_path(self) -> str:
        """Current output directory text."""
        return self._output.text()

    def set_analyze_enabled(self, enabled: bool):
        self._analyze_btn.setEnabled(enabled)

    def set_export_analysis_enabled(self, enabled: bool):
        self._export_analysis_btn.setEnabled(enabled)

    @property
    def quality_selection(self) -> str:
        """Currently selected quality preset text."""
        return self._quality.selected

    @property
    def encoder_selection(self) -> str:
        """Currently selected encoder chip text."""
        return self._encoder.selected

    @property
    def param_mode_selection(self) -> str:
        """Currently selected encoding parameter mode text."""
        return self._param.selected

    @property
    def crf_value(self) -> int:
        """Current CRF value from param panel."""
        return self._param_panel.crf_value()

    @property
    def bitrate_value(self) -> str:
        """Current bitrate text from param panel."""
        return self._param_panel.bitrate_value()

    @property
    def bitrate_unit(self) -> str:
        """Current bitrate unit from param panel."""
        return self._param_panel.bitrate_unit()

    @property
    def analysis_profile(self) -> str:
        """Currently selected analysis profile."""
        return self._analysis_profile.selected


class RecordPage(QWidget):
    """Record page — UI layer only. Business logic lives in RecordingController."""

    status_changed = Signal(str, str)  # text, type
    stats_changed = Signal(int, int, int)  # active recordings, seconds, exported clips

    def __init__(self, parent=None):
        super().__init__(parent)
        # Delegate all business logic to the controller
        self._ctrl = RecordingController()
        self._ctrl.timer.timeout.connect(self._tick)
        self._ctrl.watchdog.timeout.connect(self._watchdog_check)
        self._ctrl.init_capture(on_status_cb=self._on_capture_status)
        self._ctrl.init_exporter()

        # UI-only state
        self._has_start = False
        self._start_sec = None
        self._end_sec = None
        self._live_cursor_sec = None
        self._fullscreen_window = None
        self._pending_reconnect_reason = ""
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 3
        self._reconnect_token = 0
        self._analysis_video_path = ""
        self._analysis_highlights: list[dict] = []
        self._preview_was_playing_before_hide = False

        self._build()

    def _build(self):
        lay = QHBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(20)

        # Left side - player stays responsive, results scroll independently.
        left_widget = QWidget()
        left_widget.setStyleSheet("background:transparent;")
        left = QVBoxLayout(left_widget)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(16)

        player_section = QWidget()
        player_section.setStyleSheet("background:transparent;")
        player_section.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        player_layout = QVBoxLayout(player_section)
        player_layout.setContentsMargins(0, 0, 0, 0)
        player_layout.setSpacing(16)
        self._player_layout = player_layout

        self._preview = VideoPreview()
        self._preview.fullscreen_clicked.connect(self._on_fullscreen_toggle)
        # 底部 hover 覆盖层与下方 SharedControlBar 联动(两处都保留播放控制)
        self._preview.play_pause_clicked.connect(self._on_play_pause)
        self._preview.mute_toggled.connect(self._on_preview_mute)
        player_layout.addWidget(self._preview, 1)

        self._controls = SharedControlBar()
        self._controls.play_pause.connect(self._on_play_pause)
        self._controls.mark_in_clicked.connect(self._on_mark_in)
        self._controls.mark_out_clicked.connect(self._on_mark_out)
        self._controls.export_clicked.connect(self._on_export)
        self._controls.seek_back.connect(self._on_seek_back)
        self._controls.seek_fwd.connect(self._on_seek_fwd)
        self._controls.timeline.position_changed.connect(self._on_timeline_seek)
        self._controls.return_live_clicked.connect(self._on_return_to_live)
        player_layout.addWidget(self._controls)

        left.addWidget(player_section, 3)

        # Export progress bar (hidden by default)
        self._export_progress_bar = QProgressBar()
        self._export_progress_bar.setFixedHeight(20)
        self._export_progress_bar.setRange(0, 100)
        self._export_progress_bar.setTextVisible(True)
        self._export_progress_bar.setFormat("导出中 %p%")
        self._export_progress_bar.setVisible(False)
        left.addWidget(self._export_progress_bar)

        results_scroll = QScrollArea()
        results_scroll.setWidgetResizable(True)
        results_scroll.setFrameShape(QScrollArea.NoFrame)
        results_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        results_scroll.setStyleSheet("QScrollArea { background:transparent; border:none; }")

        results_widget = QWidget()
        results_widget.setStyleSheet("background:transparent;")
        results_layout = QVBoxLayout(results_widget)
        results_layout.setContentsMargins(0, 0, 0, 0)
        results_layout.setSpacing(16)

        self._analysis_results = AnalysisResultsGrid()
        self._analysis_results.result_clicked.connect(self._on_analysis_result_clicked)
        results_layout.addWidget(self._analysis_results)

        self._clips = ExportedClipsGrid()
        self._clips.clip_clicked.connect(self._on_clip_clicked)
        results_layout.addWidget(self._clips)

        results_layout.addStretch()
        results_scroll.setWidget(results_widget)
        left.addWidget(results_scroll, 2)
        lay.addWidget(left_widget, 1)

        # Right side - scrollable config
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QScrollArea.NoFrame)
        right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        right_scroll.setMinimumWidth(400)
        right_scroll.setMaximumWidth(420)
        right_scroll.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)

        self._config = ConfigPanel()
        self._config.connect_requested.connect(self._on_connect)
        self._config.start_record_requested.connect(self._on_record_toggle)
        self._config.analyze_requested.connect(self._on_analyze_current)
        self._config.export_analysis_requested.connect(self._on_export_analysis_results)
        self._config.setMaximumWidth(380)
        fade_config = FadeInWidget(delay_ms=100)
        fade_config.addWidget(self._config)
        fade_config.setMaximumWidth(380)
        right_scroll.setWidget(fade_config)
        lay.addWidget(right_scroll)

    def set_video_path(self, path):
        self._ctrl.video_path = path
        self._live_cursor_sec = None
        if path and os.path.isfile(path):
            probe = getattr(self._ctrl, "probe_video_duration", None)
            dur = probe() if callable(probe) else 0
            if dur > 0:
                self._ctrl.total_sec = dur
                if hasattr(self._controls, "timeline"):
                    self._controls.timeline.set_data(duration=dur, position=0)
                if hasattr(self._controls, "set_time"):
                    self._controls.set_time(0, dur)
            self._preview.play_video(path)
            self._controls.set_playing(True)
            config_panel = getattr(self, "_config", None)
            if config_panel is not None and hasattr(config_panel, "set_analyze_enabled"):
                config_panel.set_analyze_enabled(True)
            timer = getattr(self._ctrl, "timer", None)
            if timer is not None and hasattr(timer, "start"):
                timer.start(1000)

    def _emit_stats(self):
        recording = 1 if getattr(self._ctrl, "is_recording", False) else 0
        duration = int(getattr(self._ctrl, "total_sec", 0) or 0)
        clips = len(getattr(self._ctrl, "exported", []))
        try:
            self.stats_changed.emit(recording, duration, clips)
        except RuntimeError:
            pass

    def _on_fullscreen_toggle(self):
        if self._fullscreen_window is None:
            self._enter_fullscreen()
        else:
            self._exit_fullscreen(close_window=True)

    def _enter_fullscreen(self):
        if self._fullscreen_window is not None:
            return
        # 惰性确保 MpvWidget 已创建(全屏 reparent 的是 MpvWidget 而非整个预览容器)
        mpv = self._preview._ensure_mpv_widget() if hasattr(self._preview, "_ensure_mpv_widget") else None
        if mpv is None:
            self._status_changed.emit("预览未就绪,无法全屏", "warning")
            return
        # detach 控制栏;FullscreenPreview 会把 MpvWidget + 控制栏 reparent 进全屏窗口
        self._controls.setParent(None)
        self._controls.set_fullscreen(True)
        self._preview.set_fullscreen_mode(True)

        def _on_restore(_widget, controls):
            # widget 已 reparent 回 controls 的父级之外;放回 player_layout
            if controls is not None:
                controls.setParent(None)
            self._player_layout.insertWidget(0, self._preview, 1)
            self._player_layout.insertWidget(1, self._controls)
            self._controls.set_fullscreen(False)
            self._preview.set_fullscreen_mode(False)
            self._fullscreen_window = None

        fp = FullscreenPreview(
            self,
            get_widget=lambda: self._preview._mpv_widget,
            get_controls=lambda: self._controls,
            get_position=lambda: self._preview.position_sec(),
            get_duration=lambda: self._preview.duration_sec(),
            is_paused=lambda: not self._preview.is_playing(),
            is_muted=lambda: bool(self._preview._mpv_widget and getattr(self._preview._mpv_widget, '_muted', False)),
            on_toggle_play=lambda: self._preview.toggle_play_pause(),
            on_toggle_mute=lambda: self._preview._mpv_widget.set_muted(not getattr(self._preview._mpv_widget, '_muted', False)) if self._preview._mpv_widget else None,
            on_seek=lambda v: self._preview.seek_to(float(v)),
            on_restore=_on_restore,
            title="LSC 全屏播放器",
        )
        fp.enter()
        self._fullscreen_window = fp

    def _exit_fullscreen(self, close_window=True):
        fp = self._fullscreen_window
        if fp is None:
            return
        win = fp.window()
        if close_window and win is not None:
            win.close()
        else:
            # closeEvent 已触发 on_restore;手动触发兜底
            fp._close()

    # ── Connect ─────────────────────────────────────────────────

    def _on_connect(self, url):
        url = (url or "").strip()
        if not url:
            self.status_changed.emit("请输入直播间链接", "warning")
            return

        # 新连接会重置所有 pending 的重连状态，使旧重连回调失效
        self._reconnect_token += 1
        self._pending_reconnect_reason = ""
        self._reconnect_attempts = 0

        self._ctrl.page_url = url
        self._ctrl.stream_url = url
        self._config.set_connecting()
        self._controls.set_live_available(bool(self._ctrl.page_url))
        self.status_changed.emit("连接中...", "info")
        self._ctrl.start_url_parse(url, self._on_url_parsed)

    def _on_url_parsed(self, info, token=None):
        # token 用于区分重连会话；若 token 不匹配说明用户在解析完成前已停止/重置，应忽略旧回调
        if token is not None and token != getattr(self, "_reconnect_token", 0):
            _log.debug("Stale reconnect callback ignored (token=%s, current=%s)", token, self._reconnect_token)
            return

        if info.get('isLive'):
            stream_url, _selected_quality = self._ctrl.select_stream_url(
                info,
                self._config.quality_selection,
            )
            self._ctrl.stream_url = stream_url or info.get('streamUrl', '')
            streamer = info.get('streamerName', '未知')
            title = info.get('title', '')
            self._config.set_connected(True)
            pending_reconnect_reason = getattr(self, "_pending_reconnect_reason", "")
            if pending_reconnect_reason:
                # 再次检查 token，因为 pending 状态可能在解析期间被其他操作重置
                if token is not None and token != getattr(self, "_reconnect_token", 0):
                    _log.debug("Reconnect state changed during parse, ignoring callback")
                    return
                reason = pending_reconnect_reason
                self._pending_reconnect_reason = ""
                self.status_changed.emit(f"直播流已恢复，正在继续录制: {streamer} - {title}", "success")
                restarted = self._start_recording()
                if not restarted:
                    self.status_changed.emit(f"自动恢复失败: {reason}", "error")
                    self._config.set_recording(False)
                return

            self.status_changed.emit(f"已连接: {streamer} - {title}", "success")
        else:
            error = info.get('error', '直播未开播或链接无效')
            if getattr(self, "_pending_reconnect_reason", ""):
                if token is not None and token != getattr(self, "_reconnect_token", 0):
                    _log.debug("Reconnect state changed during parse, ignoring callback")
                    return
                self._pending_reconnect_reason = ""
                self.status_changed.emit(f"自动恢复失败: {error}", "error")
                self._stop_recording()
                self._config.set_recording(False)
                return

            self.status_changed.emit(f"连接失败: {error}", "error")
            self._config.set_connected(False)
            self._controls.set_live_available(False)

    def _refresh_stream_info(self, source: str) -> None:
        """Probe stream metadata and update the info panel."""
        resolution, fps = self._ctrl.probe_stream_metadata(source)
        self._config.set_info("res", resolution or "--")
        self._config.set_info("fps", fps or "--")

    # ── Record toggle ──────────────────────────────────────────

    def _on_record_toggle(self):
        if self._ctrl.is_recording:
            self._stop_recording()
            self._config.set_recording(False)
        else:
            ok = self._start_recording()
            if ok:
                self._config.set_recording(True)

    def _start_recording(self):
        if not self._ctrl.stream_url:
            self.status_changed.emit("请先输入直播间链接并连接", "warning")
            return False

        # Reset UI state first (before starting any recording)
        self._live_cursor_sec = None
        self._preview.stop_video()
        self._preview.set_state(recording=True, connected=True, time="00:00:00")
        self._controls.set_recording(True)
        self._controls.set_range_state(False, False)
        self._controls.set_export_enabled(False)
        self._controls.timeline.set_cursor_mode(SharedTimeline.CURSOR_WHITE)

        # NOTE: Do NOT set is_recording=True or start timers here.
        # Let the controller handle state — it sets is_recording and
        # record_start_mono atomically inside start_recording_with_crf().
        # Starting timers before the capture is ready caused FFmpeg to be
        # force-killed and restarted on every tick (the "immediate stop" bug).

        if not self._ctrl.capture:
            self._preview.set_state(recording=False, connected=False)
            self._controls.set_recording(False)
            reason = getattr(self._ctrl, "capture_init_error", "") or "未找到 FFmpeg 或录制器初始化失败"
            self.status_changed.emit(f"录制启动失败: {reason}", "error")
            return False

        output_dir = self._config.output_path or self._ctrl.output_dir or os.path.join(os.path.expanduser("~"), "LSC", "recordings")
        encoder = self._config.encoder_selection
        crf = self._config.crf_value
        param_mode = self._config.param_mode_selection
        bitrate_value = self._config.bitrate_value
        bitrate_unit = self._config.bitrate_unit

        success, output_path, encoder_used, error_msg = self._ctrl.start_recording_with_crf(
            self._ctrl.stream_url,
            output_dir,
            encoder,
            crf,
            param_mode=param_mode,
            bitrate=bitrate_value,
            bitrate_unit=bitrate_unit,
            input_args=self._ctrl.input_args or None,
            on_status=lambda txt, typ: self.status_changed.emit(txt, typ),
        )

        if success:
            # Now that capture is confirmed running, start the timers
            self._pending_reconnect_reason = ""
            self._reconnect_attempts = 0
            self._ctrl.timer.start(1000)
            self._ctrl.watchdog.start(2000)
            self._ctrl.output_dir = output_dir
            self._ctrl.video_path = output_path
            self._config.set_info("path", output_path)
            self._config.set_info("codec", encoder_used)
            # Show encoding parameters in info panel
            crf = self._config.crf_value
            if encoder_used == "H.264 CPU":
                self._config.set_info("bitrate", f"CRF {crf}")
            elif encoder_used == "H.264 NVENC":
                self._config.set_info("bitrate", f"CQ {crf}")
            else:
                self._config.set_info("bitrate", "原始码率")
            self.status_changed.emit("录制中", "info")
            # Preview the live stream directly. Tailing a growing mp4 can hit
            # transient EOF/cache stalls after a few seconds.
            self._preview.play_live(self._ctrl.stream_url)
            self._controls.set_playing(True)
            self._emit_stats()
        else:
            detail = error_msg or "FFmpeg 无法连接直播流"
            self.status_changed.emit(f"录制启动失败: {detail}", "error")
            # Reset UI — no need to stop capture since it never started
            self._preview.set_state(recording=False, connected=False)
            self._controls.set_recording(False)
            return False

        self._config.set_info("res", "探测中...")
        self._config.set_info("fps", "探测中...")
        return True

    def _stop_recording(self):
        self._ctrl.is_recording = False
        self._live_cursor_sec = None
        self._pending_reconnect_reason = ""
        self._ctrl.timer.stop()
        self._ctrl.watchdog.stop()
        self._preview.stop_video()
        self._controls.set_playing(False)

        success, size_mb, output_path = self._ctrl.stop_recording()
        played_output = False
        if success:
            self._ctrl.video_path = output_path
            self._config.set_info("size", f"{size_mb:.1f} MB")
            self.status_changed.emit(f"录制完成: {size_mb:.1f} MB", "success")
            def _on_probed_success(dur):
                if dur > 0:
                    self._ctrl.total_sec = dur
                    self._controls.timeline.set_data(duration=dur, position=0)
            self._ctrl.probe_video_duration(on_probed=_on_probed_success)
            # Auto-play the completed recording
            if output_path and os.path.isfile(output_path):
                self._preview.play_video(output_path)
                played_output = True
        else:
            # Even if stop() reports failure (e.g. status was already ERROR
            # from check_and_handle_crash), the output file may still exist.
            # Don't lose the user's recording!
            actual_path = self._ctrl.video_path
            if actual_path and os.path.isfile(actual_path):
                size_mb = os.path.getsize(actual_path) / (1024 * 1024)
                self._config.set_info("size", f"{size_mb:.1f} MB")
                self.status_changed.emit(f"录制完成: {size_mb:.1f} MB", "success")
                def _on_probed_failure(dur):
                    if dur > 0:
                        self._ctrl.total_sec = dur
                        self._controls.timeline.set_data(duration=dur, position=0)
                self._ctrl.probe_video_duration(on_probed=_on_probed_failure)
                # Auto-play the completed recording
                self._preview.play_video(actual_path)
                played_output = True
            else:
                self.status_changed.emit("录制停止", "info")

        self._preview.set_state(recording=False, connected=False)
        self._controls.set_recording(False)
        self._controls.set_export_enabled(False)
        self._controls.set_range_state(False, False)
        self._controls.timeline.set_cursor_mode(SharedTimeline.CURSOR_WHITE)
        self._has_start = False
        self._start_sec = None
        self._end_sec = None
        self._config.set_recording(False)
        self._config.set_analyze_enabled(bool(self._ctrl.video_path and os.path.isfile(self._ctrl.video_path)))
        if hasattr(self._config, "set_export_analysis_enabled"):
            self._config.set_export_analysis_enabled(bool(getattr(self, "_analysis_highlights", [])))
        self._controls.set_playing(played_output)
        self._emit_stats()

    # ── Play / Pause ────────────────────────────────────────────

    def _on_play_pause(self):
        if self._ctrl.is_recording:
            if self._preview.is_playing():
                self._preview.toggle_play_pause()
                self._controls.set_playing(False)
            elif self._ctrl.stream_url:
                self._preview.play_live(self._ctrl.stream_url)
                self._controls.set_playing(True)
            return

        if not self._ctrl.is_recording and self._ctrl.video_path and os.path.isfile(self._ctrl.video_path):
            if self._preview.is_playing():
                self._preview.toggle_play_pause()
                self._controls.set_playing(False)
            else:
                self._preview.play_video(self._ctrl.video_path)
                self._controls.set_playing(True)
                self._ctrl.timer.start(1000)
            return

    def _on_preview_mute(self, muted: bool) -> None:
        """底部覆盖层静音切换:委托 mpv,与 SharedControlBar 状态独立。"""
        if self._preview._mpv_widget is not None:
            self._preview._mpv_widget.set_muted(muted)

    # ── Timer tick ──────────────────────────────────────────────

    def _tick(self):
        if self._ctrl.is_recording:
            # Use public API to check for FFmpeg crash
            exit_code = self._ctrl.check_capture_crash()
            if exit_code is not None:
                msg = f"FFmpeg 异常退出 (code {exit_code})"
                if self._attempt_stream_reconnect(msg):
                    self.status_changed.emit("录制异常，正在尝试恢复直播流...", "warning")
                    return

                self.status_changed.emit(msg, "error")
                self._stop_recording()
                self._config.set_recording(False)
                return

            total = self._ctrl.tick()
            self._preview.set_state(recording=True, connected=True, time=_fmt_time(total))
            self._controls.timeline.set_live_clock(datetime.now().strftime("%H:%M:%S"))
            current_pos = self._live_cursor_sec if self._live_cursor_sec is not None else total
            display_end = self._end_sec if self._end_sec is not None else (current_pos if self._has_start else None)
            cursor_mode = SharedTimeline.CURSOR_RED if self._has_start else SharedTimeline.CURSOR_WHITE
            self._controls.timeline.set_cursor_mode(cursor_mode)
            pos = current_pos if not self._has_start else display_end
            self._controls.timeline.set_data(
                duration=total,
                position=pos,
                start=self._start_sec,
                end=display_end,
            )
            self._controls.set_time(pos, total)
            self._emit_stats()

            # File size from actual file
            if self._ctrl.video_path and os.path.isfile(self._ctrl.video_path):
                size_mb = os.path.getsize(self._ctrl.video_path) / (1024 * 1024)
                self._config.set_info("size", f"{size_mb:.1f} MB")
            else:
                self._config.set_info("size", f"{total * _ESTIMATED_MB_PER_SEC:.1f} MB")
        else:
            if self._preview.is_playing():
                pos = self._preview.position_sec()
                dur = self._ctrl.total_sec
                self._controls.timeline.set_data(duration=dur, position=pos)
                self._controls.set_time(pos, dur)
            else:
                self._ctrl.timer.stop()
                self._controls.set_playing(False)

    # ── Capture status callback ─────────────────────────────────

    def _on_capture_status(self, status):
        from lsc.recorder.capture import CaptureStatus
        if status == CaptureStatus.ERROR:
            self.status_changed.emit("录制出错", "error")
        elif status == CaptureStatus.CONNECTING:
            self.status_changed.emit("连接中...", "info")

    # ── Watchdog ────────────────────────────────────────────────

    def _watchdog_check(self):
        msg = self._ctrl.watchdog_check()
        if msg:
            if self._attempt_stream_reconnect(msg):
                self.status_changed.emit("录制异常，正在尝试恢复直播流...", "warning")
                return
            self.status_changed.emit(f"录制异常: {msg}", "error")
            self._stop_recording()
            self._config.set_recording(False)

    def _attempt_stream_reconnect(self, reason: str) -> bool:
        page_url = getattr(self._ctrl, "page_url", "")
        if not page_url or getattr(self, "_pending_reconnect_reason", ""):
            return False
        if getattr(self, "_reconnect_attempts", 0) >= getattr(self, "_max_reconnect_attempts", 3):
            return False

        self._reconnect_token += 1
        token = self._reconnect_token
        self._pending_reconnect_reason = reason
        self._reconnect_attempts = getattr(self, "_reconnect_attempts", 0) + 1
        # 绑定本次重连的 token，回调中若 token 已过期则忽略，避免用户停止后旧回调仍启动录制
        # 重连时强制刷新缓存，避免 10 秒失败缓存导致用户点“重试”后仍返回旧错误
        self._ctrl._force_next_parse_refresh = True
        self._ctrl.start_url_parse(
            page_url, lambda info: self._on_url_parsed(info, token=token)
        )
        return True

    # ── Mark / Export ────────────────────────────────────────────

    def _current_range_time(self):
        if self._ctrl.is_recording:
            duration = float(self._ctrl.total_sec)
            live_cursor_sec = getattr(self, "_live_cursor_sec", None)
            if live_cursor_sec is not None:
                return float(live_cursor_sec), duration
            return duration, duration
        pos = float(self._preview.position_sec())
        dur = float(self._preview.duration_sec() or self._ctrl.total_sec)
        return pos, dur

    def _sync_range_controls(self):
        has_in = self._start_sec is not None
        has_out = self._end_sec is not None
        can_export = has_in and has_out and self._end_sec > self._start_sec
        self._has_start = has_in
        self._controls.set_range_state(has_in, has_out)
        self._controls.set_export_enabled(can_export)
        self._controls.timeline.set_cursor_mode(
            SharedTimeline.CURSOR_RED if has_in and not has_out else SharedTimeline.CURSOR_WHITE
        )

    def _apply_range_to_timeline(self, position=None, duration=None):
        if duration is None:
            duration = float(self._ctrl.total_sec)
        if position is None:
            position = self._end_sec if self._end_sec is not None else self._start_sec
        self._controls.timeline.set_data(
            duration=duration,
            position=position or 0,
            start=self._start_sec,
            end=self._end_sec,
        )

    def _on_mark_in(self):
        pos, dur = self._current_range_time()
        if self._end_sec is not None and pos > self._end_sec:
            # 入点超过出点时，自动交换（与多房间工作台行为一致）
            self._start_sec = self._end_sec
            self._end_sec = pos
        else:
            self._start_sec = pos
        self._sync_range_controls()
        preview_end = self._end_sec
        if preview_end is None and self._ctrl.is_recording:
            preview_end = dur
        self._controls.timeline.set_data(
            duration=max(dur, preview_end or pos, pos),
            position=pos,
            start=self._start_sec,
            end=preview_end,
        )

    def _on_mark_out(self):
        pos, dur = self._current_range_time()
        if self._start_sec is None:
            self._start_sec = max(0.0, pos - 10.0)
        elif pos < self._start_sec:
            # 出点小于入点时，自动交换（与多房间工作台行为一致）
            self._end_sec = self._start_sec
            self._start_sec = pos
            self._sync_range_controls()
            self._apply_range_to_timeline(position=pos, duration=max(dur, pos, self._end_sec or 0))
            return
        self._end_sec = pos
        self._sync_range_controls()
        self._apply_range_to_timeline(position=pos, duration=max(dur, pos, self._start_sec or 0))

    def _on_export(self):
        if not self._has_start or self._start_sec is None or self._end_sec is None:
            return
        if self._end_sec <= self._start_sec:
            return
        # NOTE: Export is allowed while recording.  The capture uses
        # fragmented MP4 (frag_keyframe+empty_moov+faststart), so FFmpeg
        # can read and clip from the growing file without issues.
        # Export is also allowed during playback of completed recordings.

        idx = len(self._ctrl.exported) + 1
        # 注意:不带 .mp4 后缀,ClipExporter.export_clip 会在拼输出路径时自动追加。
        name = f"clip_{idx:03d}_{_fmt_time(self._start_sec).replace(':', '')}"
        start_sec = self._start_sec
        end_sec = self._end_sec

        output_root = self._config.output_path or self._ctrl.output_dir or "./output"
        output_dir = os.path.join(output_root, "highlights")
        if self._ctrl.start_export(
            start_sec,
            end_sec,
            output_dir,
            name,
            on_done=lambda ok, path, err, sz, export_name=name, export_idx=idx, export_start=start_sec, export_end=end_sec:
                self._on_export_done(ok, path, err, sz, export_name, export_idx, export_start, export_end),
            on_progress=self._on_export_progress,
        ):
            self._controls.set_export_enabled(False)
            self._export_progress_bar.setValue(0)
            self._export_progress_bar.setVisible(True)
        else:
            reason = getattr(self._ctrl, "exporter_init_error", "") or "导出器不可用或源文件不存在"
            self.status_changed.emit(f"片段导出失败: {reason}", "error")

    def _on_export_done(self, success, path, error, size_mb, name, idx, start_sec, end_sec):
        self._export_progress_bar.setVisible(False)
        if success:
            size = f"{size_mb:.1f} MB"
            self._ctrl.exported.append((name, start_sec, end_sec, size, path))
            self._clips.add_clip(name, start_sec, end_sec, size)
            self._controls.timeline.add_marker(start_sec, end_sec, name)
            self.status_changed.emit(f"片段已导出: {path}", "success")
            self._emit_stats()
        elif error:
            self.status_changed.emit(f"片段导出失败: {error}", "error")
        self._controls.set_export_enabled(True)
        self._on_clear_range()

    def _on_export_progress(self, percent: float, elapsed: float, total: float) -> None:
        """Update export progress bar from ExportWorker."""
        self._export_progress_bar.setValue(int(min(percent, 100)))
        if total > 0:
            self._export_progress_bar.setFormat(f"导出中 {_fmt_time(elapsed)}/{_fmt_time(total)} · %p%")

    def _on_analyze_current(self):
        video_path = getattr(self._ctrl, "video_path", "")
        if not video_path or not os.path.isfile(video_path):
            self.status_changed.emit("分析失败: 当前没有可分析的录制文件", "error")
            return

        output_dir = os.path.dirname(video_path) or self._config.output_path or "."
        self._config.set_analyze_enabled(False)
        self.status_changed.emit("分析中...", "info")
        started = self._ctrl.start_analysis(
            video_path,
            self._config.analysis_profile,
            output_dir,
            self._on_analysis_done,
        )
        if not started:
            self._config.set_analyze_enabled(True)
            self.status_changed.emit("分析失败: 无法启动分析任务", "error")

    def _on_analysis_done(self, success, result_path, error, highlight_count):
        self._config.set_analyze_enabled(True)
        if success:
            highlights: list[dict] = []
            try:
                import json

                with open(result_path, encoding="utf-8") as f:
                    payload = json.load(f)
                highlights = payload.get("highlights", []) if isinstance(payload, dict) else []
            except Exception:
                highlights = []

            ctrl = getattr(self, "_ctrl", None)
            self._analysis_video_path = getattr(ctrl, "video_path", "") if ctrl is not None else ""
            self._analysis_highlights = highlights
            analysis_results = getattr(self, "_analysis_results", None)
            if analysis_results is not None and hasattr(analysis_results, "set_results"):
                analysis_results.set_results(highlights)
            if hasattr(self._config, "set_export_analysis_enabled"):
                self._config.set_export_analysis_enabled(bool(highlights))
            self._config.set_info("analysis", f"{highlight_count} 个高光")
            self._config.set_info("analysis_path", result_path)
            self.status_changed.emit(f"分析完成: 检测到 {highlight_count} 个高光", "success")
            return

        if hasattr(self._config, "set_export_analysis_enabled"):
            self._config.set_export_analysis_enabled(False)
        self.status_changed.emit(f"分析失败: {error}", "error")

    def _on_analysis_result_clicked(self, idx: int):
        highlights = getattr(self, "_analysis_highlights", [])
        if idx < 0 or idx >= len(highlights):
            return

        source_path = getattr(self, "_analysis_video_path", "")
        if source_path and os.path.isfile(source_path):
            self.set_video_path(source_path)

        highlight = highlights[idx]
        self._start_sec = float(highlight.get("start_sec", 0.0))
        self._end_sec = float(highlight.get("end_sec", self._start_sec))
        self._sync_range_controls()
        self._apply_range_to_timeline(position=self._start_sec, duration=float(getattr(self._ctrl, "total_sec", 0) or 0))

    def _on_export_analysis_results(self):
        source_path = getattr(self, "_analysis_video_path", "")
        highlights = getattr(self, "_analysis_highlights", [])
        if not source_path or not os.path.isfile(source_path) or not highlights:
            self.status_changed.emit("导出失败: 当前没有可导出的分析高光", "error")
            return

        output_root = self._config.output_path or self._ctrl.output_dir or "./output"
        output_dir = os.path.join(output_root, "highlights")
        self._config.set_export_analysis_enabled(False)
        self.status_changed.emit("正在批量导出分析高光...", "info")
        started = self._ctrl.start_export_all(
            source_path,
            highlights,
            output_dir,
            self._on_export_analysis_done,
        )
        if not started:
            self._config.set_export_analysis_enabled(True)
            self.status_changed.emit("导出失败: 无法启动批量导出任务", "error")

    def _on_export_analysis_done(self, success, exported_count, total_count, error, results):
        self._config.set_export_analysis_enabled(bool(getattr(self, "_analysis_highlights", [])))
        if success:
            for idx, result in enumerate(results):
                if not result.success:
                    continue
                highlight = self._analysis_highlights[idx] if idx < len(self._analysis_highlights) else {}
                start_sec = float(highlight.get("start_sec", 0.0))
                end_sec = float(highlight.get("end_sec", start_sec))
                size = f"{result.file_size_mb:.1f} MB"
                self._ctrl.exported.append(
                    (result.title, start_sec, end_sec, size, result.output_path)
                )
                self._clips.add_clip(result.title, start_sec, end_sec, size)
                self._controls.timeline.add_marker(start_sec, end_sec, result.title)
            self._emit_stats()
            self.status_changed.emit(f"批量导出完成: {exported_count}/{total_count}", "success")
            return

        self.status_changed.emit(f"导出失败: {error}", "error")

    def _on_clip_clicked(self, idx):
        if idx < 0 or idx >= len(self._ctrl.exported):
            return
        clip = self._ctrl.exported[idx]
        path = clip[4] if len(clip) >= 5 else ""
        start_sec = clip[1] if len(clip) >= 2 else 0
        end_sec = clip[2] if len(clip) >= 3 else 0
        if path and os.path.isfile(path):
            self.set_video_path(path)
            # Restore the original selection range so user can see where the clip came from
            if end_sec > start_sec:
                self._start_sec = start_sec
                self._end_sec = end_sec
                self._sync_range_controls()
                self._apply_range_to_timeline(
                    position=start_sec,
                    duration=float(self._ctrl.total_sec or end_sec),
                )

    def _on_clear_range(self):
        self._has_start = False
        self._start_sec = None
        self._end_sec = None
        self._controls.set_range_state(False, False)
        self._controls.set_export_enabled(False)
        self._controls.timeline.set_cursor_mode(SharedTimeline.CURSOR_WHITE)

    # ── Seek ─────────────────────────────────────────────────────

    def _on_timeline_seek(self, sec):
        """Handle timeline click/drag — seek video during playback."""
        if self._ctrl.is_recording:
            duration = float(self._ctrl.total_sec or 0)
            if duration <= 0:
                return
            self._live_cursor_sec = max(0.0, min(float(sec), duration))
            self._controls.timeline.set_data(
                duration=duration,
                position=self._live_cursor_sec,
                start=self._start_sec,
                end=self._end_sec,
            )
            self._controls.set_time(self._live_cursor_sec, duration)
            return

        if self._ctrl.video_path and os.path.isfile(self._ctrl.video_path):
            duration = float(self._preview.duration_sec() or self._ctrl.total_sec or 0)
            position = max(0.0, min(float(sec), duration if duration > 0 else float(sec)))
            self._preview.seek_to(position)
            self._controls.timeline.set_data(duration=duration, position=position)
            self._controls.set_time(position, duration)

    def _on_return_to_live(self):
        if not self._ctrl.stream_url:
            return
        self._live_cursor_sec = None
        self._preview.play_live(self._ctrl.stream_url)
        self._controls.set_playing(True)
        duration = float(self._ctrl.total_sec or 0)
        if duration > 0:
            self._controls.timeline.set_data(
                duration=duration,
                position=duration,
                start=self._start_sec,
                end=self._end_sec,
            )
            self._controls.set_time(duration, duration)

    def _on_seek_back(self):
        if self._has_start and self._end_sec:
            self._end_sec = max(self._start_sec, self._end_sec - 10)
            self._controls.timeline.set_data(
                duration=self._ctrl.total_sec, position=self._end_sec,
                start=self._start_sec, end=self._end_sec,
            )
        elif not self._ctrl.is_recording and self._preview.is_playing():
            # Seek video back 10 seconds during playback
            pos = max(0, self._preview.position_sec() - 10)
            self._preview.seek_to(pos)

    def _on_seek_fwd(self):
        if self._has_start and self._end_sec is not None:
            self._end_sec = min(self._ctrl.total_sec, self._end_sec + 10)
            self._controls.timeline.set_data(
                duration=self._ctrl.total_sec, position=self._end_sec,
                start=self._start_sec, end=self._end_sec,
            )
        elif not self._ctrl.is_recording and self._preview.is_playing():
            # Seek video forward 10 seconds during playback
            dur = self._preview.duration_sec() or self._ctrl.total_sec
            pos = min(dur, self._preview.position_sec() + 10)
            self._preview.seek_to(pos)

    def keyPressEvent(self, event):
        """Handle keyboard shortcuts for common actions."""
        if event.key() == Qt.Key_Space:
            self._on_play_pause()
            event.accept()
            return
        if event.key() == Qt.Key_I:
            self._on_mark_in()
            event.accept()
            return
        if event.key() == Qt.Key_O:
            self._on_mark_out()
            event.accept()
            return
        if event.key() == Qt.Key_Left:
            self._on_seek_back()
            event.accept()
            return
        if event.key() == Qt.Key_Right:
            self._on_seek_fwd()
            event.accept()
            return
        if event.key() == Qt.Key_E and event.modifiers() & Qt.ControlModifier:
            self._on_export()
            event.accept()
            return
        super().keyPressEvent(event)

    # ── Page visibility ──────────────────────────────────────────

    def showEvent(self, event):
        """Re-start preview when page becomes visible again."""
        super().showEvent(event)
        if self._preview_was_playing_before_hide:
            if self._ctrl.is_recording and self._ctrl.stream_url:
                self._preview.play_live(self._ctrl.stream_url)
                self._controls.set_playing(True)
            elif self._ctrl.video_path and os.path.isfile(self._ctrl.video_path):
                self._preview.play_video(self._ctrl.video_path)
                self._controls.set_playing(True)
                self._ctrl.timer.start(1000)
            self._preview_was_playing_before_hide = False

    def hideEvent(self, event):
        """Stop preview when page is hidden to save bandwidth/CPU."""
        super().hideEvent(event)
        if self._preview.is_playing():
            self._preview_was_playing_before_hide = True
            self._preview.stop_video()
            self._controls.set_playing(False)
            if not self._ctrl.is_recording:
                self._ctrl.timer.stop()

    # ── Cleanup ──────────────────────────────────────────────────

    def cleanup(self):
        self._ctrl.cleanup()
        self._preview.stop_video()
        self._preview.cleanup()
