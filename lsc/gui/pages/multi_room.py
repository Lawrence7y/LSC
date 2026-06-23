"""多房间工作台页面 — 包含卡片网格、详情面板、控制栏和状态栏。"""
from __future__ import annotations

import os

from PySide6.QtCore import QEvent, QObject, QPointF, Qt, Signal, QTimer
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPen, QPolygonF, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QCheckBox,
    QSlider,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from PySide6.QtCore import QSettings

from lsc.gui.components.flow_layout import FlowLayout
from lsc.gui.components.room_card import RoomCard
from lsc.gui.components.control_bar import ControlBar
from lsc.gui.components.clip_list import ClipListWidget, ClipSegment
from lsc.gui.components.dialogs import ExportConfirmDialog
from lsc.gui.components.widgets import Card, ChipGroup, EmptyState, InputField, ParamPanel
from lsc.gui.multi_room.manager import MAX_ROOMS, MultiRoomManager
from lsc.gui.multi_room.session import RoomSession
from lsc.gui.theme import connect_theme_changed, get_theme, is_dark
from lsc.gui.undo import Command, UndoStack
from lsc.utils.helpers import fmt_time

# ── Grid configuration ────────────────────────────────────────
# _CARD_MIN_WIDTH 仅用于「名义列数」计算(方向键导航 & 响应式测试),
# 实际换行由 FlowLayout 按每张卡片自身宽度完成。
_CARD_MAX_WIDTH = 560
_CARD_MIN_WIDTH = 340
_GRID_H_SPACING = 8
_GRID_VMARGIN = 12
_GRID_HMARGIN = 10


class DetailPanel(QWidget):
    """右侧房间详情面板（不包含 Card 外壳，由宿主页面包装）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_room_id: str | None = None
        self._build()

    def _build(self):
        self.setObjectName("detailPanel")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._body_layout = QVBoxLayout()
        self._body_layout.setContentsMargins(0, 0, 0, 0)
        self._body_layout.setSpacing(0)
        self._body_layout.setAlignment(Qt.AlignTop)
        root.addLayout(self._body_layout)

        self._empty = EmptyState("选择房间查看详情", "点击左侧房间卡片查看详细信息")
        self._body_layout.addWidget(self._empty)
        self._body_layout.addStretch()

    def refresh_theme(self) -> None:
        # EmptyState 使用全局 objectName 样式，无需额外处理
        pass

    def show_room(self, room: RoomSession | None) -> None:
        new_id = room.room_id if room else None
        self._current_room_id = new_id
        self._rebuild_body(room)

    def _rebuild_body(self, room: RoomSession | None) -> None:
        while self._body_layout.count():
            item = self._body_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._empty = None

        if room is None:
            self._empty = EmptyState("选择房间查看详情", "点击左侧房间卡片查看详细信息")
            self._body_layout.addWidget(self._empty)
            self._body_layout.addStretch()
            return

        info_grid = QWidget()
        gl = QGridLayout(info_grid)
        gl.setContentsMargins(0, 0, 0, 0)
        gl.setHorizontalSpacing(18)
        gl.setVerticalSpacing(14)

        controller = room.controller
        video_path = getattr(controller, "video_path", "") if controller else ""
        output_path = room.record_output_path or video_path or "--"
        size_text = f"{room.record_size_mb:.1f} MB" if room.record_size_mb > 0 else "--"
        codec = getattr(controller, "encoder", "") if controller else ""
        param = getattr(controller, "record_profile", "") if controller else ""
        if not param and controller is not None:
            crf = getattr(controller, "crf", None)
            param = f"CRF {crf}" if crf is not None else ""
        analysis_text = room.status_text()
        if room.record_started_at:
            from datetime import datetime
            elapsed = (datetime.now() - room.record_started_at).total_seconds()
            analysis_text = f"{analysis_text} · {fmt_time(elapsed)}"

        items = [
            ("分辨率", room.selected_quality or "--"),
            ("帧率", "--"),
            ("编码", codec or "--"),
            ("编码参数", param or "--"),
            ("文件大小", size_text),
            ("输出路径", output_path),
            ("分析结果", analysis_text or "--"),
            ("结果文件", room.last_error and room.friendly_error or "--"),
        ]

        for i, (label_text, value_text) in enumerate(items):
            col = i % 2
            row = i // 2
            item = QWidget()
            il = QVBoxLayout(item)
            il.setContentsMargins(0, 0, 0, 0)
            il.setSpacing(4)
            lbl = QLabel(label_text)
            lbl.setObjectName("info_label")
            il.addWidget(lbl)
            val = QLabel(str(value_text))
            val.setObjectName("info_value")
            val.setWordWrap(True)
            val.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            il.addWidget(val)
            gl.addWidget(item, row, col)

        self._body_layout.addWidget(info_grid)
        self._body_layout.addStretch()

    @staticmethod
    def _preview_text(room: RoomSession) -> str:
        if not room.preview_enabled:
            return "未开启"
        return "已暂停" if room.preview_paused else "播放中"


class StatusBar(QWidget):
    """底部状态栏。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(36)
        self._build()

    def _build(self):
        self.setObjectName("multiRoomStatusBar")
        self._stat_dots: list[QLabel] = []
        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 0, 20, 0)
        layout.setSpacing(16)

        self._total = self._make_stat("房间", "0")
        self._connected = self._make_stat("已连接", "0", "statusBarDotSuccess")
        self._recording = self._make_stat("录制中", "0", "statusBarDotError")
        self._preview = self._make_stat("预览中", "0", "statusBarDotPrimary")

        for s in (self._total, self._connected, self._recording, self._preview):
            layout.addWidget(s)

        layout.addStretch()

        # 导出进度条（默认隐藏）
        self._progress_bar = QProgressBar()
        self._progress_bar.setFixedWidth(160)
        self._progress_bar.setFixedHeight(14)
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setTextVisible(True)
        self._progress_bar.setFormat("导出中 %p%")
        self._progress_bar.setObjectName("progressBar")
        self._progress_bar.setVisible(False)
        layout.addWidget(self._progress_bar)

        self._message_label = QLabel("")
        self._message_label.setObjectName("statusBarMessage")
        layout.addWidget(self._message_label)

        self._error_label = QLabel("")
        self._error_label.setObjectName("statusBarError")
        layout.addWidget(self._error_label)

    def _make_stat(self, label: str, value: str, dot_name: str = "") -> QWidget:
        w = QWidget()
        w.setObjectName("statusBarStat")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(5)
        if dot_name:
            dot = QLabel("●")
            dot.setObjectName(dot_name)
            self._stat_dots.append(dot)
            lay.addWidget(dot)
        lbl = QLabel(label)
        lbl.setObjectName("statusBarLabel")
        val = QLabel(value)
        val.setObjectName("statusBarValue")
        lay.addWidget(lbl)
        lay.addWidget(val)
        return w

    def update_stats(self, total: int, connected: int, recording: int, previewing: int, errors: int):
        for w, v in zip(
            [self._total, self._connected, self._recording, self._preview],
            [total, connected, recording, previewing],
        ):
            val = w.findChild(QLabel, "statusBarValue")
            if val:
                val.setText(str(v))
        self._error_label.setText(f"{errors} 个房间有错误" if errors > 0 else "")

    def refresh_theme(self) -> None:
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def show_message(self, text: str, timeout_ms: int = 5000) -> None:
        """Show a temporary status message (toast-like)."""
        self._message_label.setText(text)
        if timeout_ms > 0:
            QTimer.singleShot(timeout_ms, lambda: self._message_label.setText(""))

    def show_progress(self, percent: float) -> None:
        """显示导出进度条。"""
        self._progress_bar.setValue(int(percent))
        self._progress_bar.setVisible(True)

    def hide_progress(self) -> None:
        """隐藏导出进度条。"""
        self._progress_bar.setVisible(False)
        self._progress_bar.setValue(0)


class _BottomBar(QWidget):
    """底部控制栏与状态栏合并后的圆角容器（风格同录制页 ControlBar）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build()
        connect_theme_changed(self.refresh_theme)

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(10)

        self._controls = ControlBar()
        self._controls.set_draw_background(False)
        root.addWidget(self._controls)

        self._status = StatusBar()
        root.addWidget(self._status)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = get_theme()
        p.setBrush(QColor(c.bg_secondary))
        # 亮色模式下白底容器在浅灰页面上几乎无边，使用更强的描边保证可见
        pen_color = c.border_subtle if is_dark() else c.border_strong
        p.setPen(QColor(pen_color))
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 10, 10)
        p.end()

    def refresh_theme(self) -> None:
        self._controls.refresh_theme()
        self._status.refresh_theme()
        self.update()

    @property
    def controls(self) -> ControlBar:
        return self._controls

    @property
    def status(self) -> StatusBar:
        return self._status


class _FullscreenIconButton(QPushButton):
    """字体无关的全屏播放器图标按钮，避免系统缺字导致图标乱码。"""

    def __init__(self, icon_kind: str, parent=None) -> None:
        super().__init__("", parent)
        self._icon_kind = icon_kind
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def icon_kind(self) -> str:
        return self._icon_kind

    def set_icon_kind(self, icon_kind: str) -> None:
        if self._icon_kind == icon_kind:
            return
        self._icon_kind = icon_kind
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg = QColor(0, 0, 0, 165 if self.underMouse() else 120)
        p.setBrush(bg)
        p.setPen(QColor(255, 255, 255, 42))
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 8, 8)

        pen = QPen(QColor(255, 255, 255, 238), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(QColor(255, 255, 255, 238))
        cx = self.width() / 2
        cy = self.height() / 2

        if self._icon_kind == "play":
            p.drawPolygon(
                QPolygonF(
                    [
                        QPointF(cx - 5, cy - 8),
                        QPointF(cx - 5, cy + 8),
                        QPointF(cx + 8, cy),
                    ]
                )
            )
        elif self._icon_kind == "pause":
            p.drawRoundedRect(int(cx - 7), int(cy - 8), 4, 16, 1, 1)
            p.drawRoundedRect(int(cx + 3), int(cy - 8), 4, 16, 1, 1)
        elif self._icon_kind == "minimize":
            p.drawLine(int(cx - 8), int(cy + 6), int(cx + 8), int(cy + 6))
        elif self._icon_kind == "exit_fullscreen":
            left, right = int(cx - 10), int(cx + 10)
            top, bottom = int(cy - 8), int(cy + 8)
            mid_left, mid_right = int(cx - 3), int(cx + 3)
            mid_top, mid_bottom = int(cy - 2), int(cy + 2)
            p.drawLine(left, top, mid_left, top)
            p.drawLine(left, top, left, mid_top)
            p.drawLine(right, top, mid_right, top)
            p.drawLine(right, top, right, mid_top)
            p.drawLine(left, bottom, mid_left, bottom)
            p.drawLine(left, bottom, left, mid_bottom)
            p.drawLine(right, bottom, mid_right, bottom)
            p.drawLine(right, bottom, right, mid_bottom)
        p.end()


class _FullscreenActivityFilter(QObject):
    """捕获全屏窗口和控件上的鼠标活动，用于唤醒自动隐藏的底栏。"""

    def __init__(self, on_activity, parent=None) -> None:
        super().__init__(parent)
        self._on_activity = on_activity

    def eventFilter(self, obj, event) -> bool:
        if event.type() in (
            QEvent.Type.MouseMove,
            QEvent.Type.Enter,
            QEvent.Type.HoverMove,
            QEvent.Type.MouseButtonPress,
        ):
            self._on_activity()
        return super().eventFilter(obj, event)


class MultiRoomPage(QWidget):
    """多房间工作台页面。"""

    room_selected = Signal(str)

    def __init__(self, manager: MultiRoomManager | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._manager = manager if manager is not None else MultiRoomManager()
        self._cards: dict[str, RoomCard] = {}
        self._selected_room_id: str | None = None
        # Multi-selection set (Ctrl/Shift click). When more than one room is
        # selected, timeline seek / seek-back / seek-fwd apply to all of them
        # simultaneously so the user can scrub several streams in lockstep.
        self._selected_room_ids: set[str] = set()
        # Fullscreen preview window handle (created on demand).
        self._fullscreen_window = None
        # 导出进度跟踪：{room_id: percent}，用于聚合多房间导出进度
        self._export_progress: dict[str, float] = {}
        self._export_total_tasks: int = 0
        self._export_completed_tasks: int = 0
        self._grid_columns = 1
        self._undo = UndoStack(limit=50)
        self._build_ui()
        self._connect_signals()
        connect_theme_changed(self._refresh_theme)
        self.setMinimumWidth(1080)
        # resizeEvent 防抖：避免拖拽窗口边缘时频繁重建网格
        self._grid_debounce = QTimer(self)
        self._grid_debounce.setSingleShot(True)
        self._grid_debounce.setInterval(150)
        self._grid_debounce.timeout.connect(self._update_grid_columns)
        # 从持久化配置自动加载上次保存的房间列表
        self._manager.load_rooms()
        for room in self._manager.list_rooms():
            self._add_card(room)
        self._refresh()

    def _get_recording_settings(self) -> tuple[str, str, int]:
        """从 QSettings 读取用户配置的录制参数。

        Returns:
            (output_dir, encoder, crf) 元组。
        """
        import os
        settings = QSettings("LSC", "LiveStreamClipper")
        default_output = os.path.join(os.path.expanduser("~"), "LSC", "output")
        output_dir = ""
        output_input = getattr(self, "_output_input", None)
        if output_input is not None:
            output_dir = output_input.text().strip()
        if not output_dir:
            output_dir = str(settings.value("output_dir", default_output))
        settings.setValue("output_dir", output_dir)
        encoder = settings.value("encoder", "H.264 NVENC")
        try:
            crf = int(settings.value("crf", "23"))
        except (ValueError, TypeError):
            crf = 23
        return output_dir, encoder, crf

    def _get_recording_profile(self) -> tuple[str, str, int, str, str, str]:
        """读取并保存多房间录制配置，返回完整 FFmpeg 参数。"""
        settings = QSettings("LSC", "LiveStreamClipper")
        output_dir, fallback_encoder, fallback_crf = self._get_recording_settings()

        quality_widget = getattr(self, "_record_quality", None)
        if quality_widget is not None:
            settings.setValue("quality", quality_widget.selected)

        encoder_widget = getattr(self, "_record_encoder", None)
        encoder = encoder_widget.selected if encoder_widget is not None else fallback_encoder
        settings.setValue("encoder", encoder)

        param_widget = getattr(self, "_record_param", None)
        param_mode = param_widget.selected if param_widget is not None else str(settings.value("param_mode", "CRF 质量"))
        settings.setValue("param_mode", param_mode)

        param_panel = getattr(self, "_record_param_panel", None)
        try:
            crf = int(param_panel.crf_value() if param_panel is not None else fallback_crf)
        except (ValueError, TypeError):
            crf = 23
        bitrate = param_panel.bitrate_value() if param_panel is not None else str(settings.value("bitrate", "8000"))
        bitrate_unit = param_panel.bitrate_unit() if param_panel is not None else str(settings.value("bitrate_unit", "kbps"))
        settings.setValue("crf", crf)
        settings.setValue("bitrate", bitrate)
        settings.setValue("bitrate_unit", bitrate_unit)
        return output_dir, str(encoder), crf, str(param_mode), str(bitrate), str(bitrate_unit)

    def _on_browse_output_dir(self) -> None:
        current_dir, _encoder, _crf = self._get_recording_settings()
        chosen = QFileDialog.getExistingDirectory(self, "选择输出目录", current_dir)
        if not chosen:
            return
        self._output_input.setText(chosen)
        QSettings("LSC", "LiveStreamClipper").setValue("output_dir", chosen)

    def _record_setting_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("field_label")
        return label

    def _build_record_settings_card(self) -> Card:
        card = Card()
        title = QLabel("录制设置")
        title.setObjectName("card_title")
        card.add_widget(title)

        settings = QSettings("LSC", "LiveStreamClipper")

        card.add_widget(self._record_setting_label("画质预设"))
        self._record_quality = ChipGroup(["原画", "高清", "流畅"])
        quality = str(settings.value("quality", self._record_quality.selected))
        if quality in self._record_quality._items:
            self._record_quality._click(quality)
        card.add_widget(self._record_quality)

        card.add_widget(self._record_setting_label("编码器"))
        self._record_encoder = ChipGroup(["H.264 NVENC", "H.264 CPU", "Copy"])
        encoder = str(settings.value("encoder", self._record_encoder.selected))
        if encoder in self._record_encoder._items:
            self._record_encoder._click(encoder)
        card.add_widget(self._record_encoder)

        card.add_widget(self._record_setting_label("编码参数"))
        self._record_param = ChipGroup(["CRF 质量", "码率限制", "不限制"])
        param_mode = str(settings.value("param_mode", self._record_param.selected))
        if param_mode in self._record_param._items:
            self._record_param._click(param_mode)
        card.add_widget(self._record_param)

        self._record_param_panel = ParamPanel()
        self._record_param_panel.set_crf_value(settings.value("crf", "23"))
        self._record_param_panel.set_bitrate_value(str(settings.value("bitrate", "8000")))
        self._record_param_panel.set_bitrate_unit(str(settings.value("bitrate_unit", "kbps")))
        card.add_widget(self._record_param_panel)

        self._record_start_btn = QPushButton("开始录制")
        self._record_start_btn.setObjectName("addRoomButton")
        self._record_start_btn.setFixedHeight(36)
        self._record_start_btn.setToolTip("使用上方配置开始所有已连接房间的录制")
        self._record_start_btn.clicked.connect(self._on_batch_record)
        card.add_widget(self._record_start_btn)

        self._record_analyze_btn = QPushButton("分析当前录制")
        self._record_analyze_btn.setObjectName("btnSecondary")
        self._record_analyze_btn.setFixedHeight(36)
        self._record_analyze_btn.setEnabled(False)
        self._record_analyze_btn.setToolTip("多房间分析入口尚未接入；直播录制页的分析按钮是真实可用的")
        card.add_widget(self._record_analyze_btn)

        self._record_export_analysis_btn = QPushButton("导出分析高光")
        self._record_export_analysis_btn.setObjectName("btnSecondary")
        self._record_export_analysis_btn.setFixedHeight(36)
        self._record_export_analysis_btn.setEnabled(False)
        self._record_export_analysis_btn.setToolTip("需要先在直播录制页完成高光分析后再导出")
        card.add_widget(self._record_export_analysis_btn)

        self._record_param.selection_changed.connect(self._on_record_param_changed)
        self._record_encoder.selection_changed.connect(lambda _value: self._on_record_param_changed(self._record_param.selected))
        self._on_record_param_changed(self._record_param.selected)
        return card

    def _on_record_param_changed(self, value: str) -> None:
        panel = getattr(self, "_record_param_panel", None)
        if panel is None:
            return
        mode_map = {"CRF 质量": 0, "码率限制": 1, "不限制": 2}
        panel.set_mode(mode_map.get(value, 0))
        encoder = getattr(getattr(self, "_record_encoder", None), "selected", "")
        panel.setEnabled(encoder != "Copy" and value != "不限制")

    def _build_ui(self) -> None:
        # ── Root: page-level scroll area containing body ──
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._page_scroll = QScrollArea()
        self._page_scroll.setWidgetResizable(True)
        self._page_scroll.setFrameShape(QScrollArea.NoFrame)
        self._page_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._page_scroll.verticalScrollBar().setStyleSheet("QScrollBar { width: 0; }")
        self._page_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self._page_body = QWidget()
        self._page_body.setStyleSheet("background:transparent;")
        page_layout = QHBoxLayout(self._page_body)
        page_layout.setContentsMargins(24, 24, 24, 24)
        page_layout.setSpacing(20)

        self._page_scroll.setWidget(self._page_body)
        root.addWidget(self._page_scroll)

        # ── Splitter for left/right panels ──
        self._splitter = QSplitter(Qt.Horizontal, self._page_body)
        self._splitter.setHandleWidth(8)
        self._splitter.setChildrenCollapsible(False)
        page_layout.addWidget(self._splitter)

        # ── Left side: toolbar, card grid, bottom control/status bar ──
        left_widget = QWidget()
        left_widget.setStyleSheet("background:transparent;")
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(16)
        self._left_layout = left_layout
        self._splitter.addWidget(left_widget)

        # ── Top toolbar ──
        toolbar = QWidget()
        toolbar.setFixedHeight(56)
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(20, 0, 20, 0)
        toolbar_layout.setSpacing(12)

        title_group = QHBoxLayout()
        title_group.setSpacing(8)
        self._title = QLabel("多房间工作台")
        self._title.setObjectName("page_title")
        title_group.addWidget(self._title)

        self._room_limit_label = QLabel("")
        self._room_limit_label.setObjectName("roomLimitBadge")
        self._room_limit_label.setAlignment(Qt.AlignCenter)
        title_group.addWidget(self._room_limit_label)
        toolbar_layout.addLayout(title_group)

        toolbar_layout.addStretch()

        # 分隔
        self._sep1 = QFrame()
        self._sep1.setObjectName("v_line")
        self._sep1.setFrameShape(QFrame.NoFrame)
        self._sep1.setFixedWidth(1)
        self._sep1.setFixedHeight(24)
        toolbar_layout.addWidget(self._sep1)

        self._batch_record_btn = QPushButton("批量录制")
        self._batch_record_btn.setObjectName("btnSuccess")
        self._batch_record_btn.setAccessibleName("批量录制")
        self._batch_record_btn.setFixedHeight(36)
        self._batch_record_btn.setToolTip("对所有已连接且未录制的房间开始录制")
        self._batch_record_btn.clicked.connect(self._on_batch_record)
        toolbar_layout.addWidget(self._batch_record_btn)

        self._batch_stop_btn = QPushButton("批量停止")
        self._batch_stop_btn.setObjectName("btnDanger")
        self._batch_stop_btn.setAccessibleName("批量停止")
        self._batch_stop_btn.setFixedHeight(36)
        self._batch_stop_btn.setToolTip("停止所有正在录制的房间")
        self._batch_stop_btn.clicked.connect(self._on_batch_stop)
        toolbar_layout.addWidget(self._batch_stop_btn)

        # 分隔
        self._sep2 = QFrame()
        self._sep2.setObjectName("v_line")
        self._sep2.setFrameShape(QFrame.NoFrame)
        self._sep2.setFixedWidth(1)
        self._sep2.setFixedHeight(24)
        toolbar_layout.addWidget(self._sep2)

        self._mute_all_btn = QPushButton("全部静音")
        self._mute_all_btn.setObjectName("btnSecondary")
        self._mute_all_btn.setCheckable(True)
        self._mute_all_btn.setFixedHeight(36)
        self._mute_all_btn.setToolTip("切换所有房间预览的静音状态")
        self._mute_all_btn.clicked.connect(self._on_mute_all)
        toolbar_layout.addWidget(self._mute_all_btn)

        self._align_live_btn = QPushButton("对齐直播")
        self._align_live_btn.setObjectName("btnSecondary")
        self._align_live_btn.setFixedHeight(36)
        self._align_live_btn.setToolTip("将所有预览对齐到最新直播画面")
        self._align_live_btn.clicked.connect(self._on_align_live)
        toolbar_layout.addWidget(self._align_live_btn)

        left_layout.addWidget(toolbar)

        # ── Card grid ──
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self._card_container = QWidget()
        self._card_container.setStyleSheet("background:transparent;")
        self._card_layout = FlowLayout(self._card_container, spacing=_GRID_H_SPACING)
        self._card_layout.setContentsMargins(_GRID_HMARGIN, _GRID_VMARGIN, _GRID_HMARGIN, _GRID_VMARGIN)
        self._card_layout.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        # FlowLayout 依赖 heightForWidth 在 QScrollArea 内自适应高度
        _sp = self._card_container.sizePolicy()
        _sp.setHeightForWidth(True)
        self._card_container.setSizePolicy(_sp)
        self._scroll.setWidget(self._card_container)

        # 空状态覆盖层，居中显示于滚动区域
        self._empty_label = QLabel("暂无房间\n在右侧「添加直播间」卡片输入链接开始管理", self._scroll.viewport())
        self._empty_label.setAlignment(Qt.AlignCenter)
        self._empty_label.setObjectName("empty_state")
        self._empty_label.setVisible(False)

        left_layout.addWidget(self._scroll, 1)

        # ── Bottom control/status bar ──
        self._bottom_bar = _BottomBar()
        self._controls = self._bottom_bar.controls
        self._statusbar = self._bottom_bar.status
        left_layout.addWidget(self._bottom_bar)

        self._clip_card = Card()
        self._clip_list = ClipListWidget()
        self._clip_list.set_add_enabled(False)
        self._clip_card.add_widget(self._clip_list)
        left_layout.addWidget(self._clip_card)

        # ── Right side: scrollable detail + clip list cards ──
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QScrollArea.NoFrame)
        right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        right_scroll.setMinimumWidth(360)
        right_scroll.setMaximumWidth(400)
        right_scroll.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)

        right_widget = QWidget()
        right_widget.setStyleSheet("background:transparent;")
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(16)

        # 添加直播间 Card（与录制页配置面板风格一致）
        add_card = Card()
        add_title = QLabel("添加直播间")
        add_title.setObjectName("card_title")
        add_card.add_widget(add_title)
        add_card.add_widget(QLabel("直播间链接"))
        self._url_input = InputField()
        self._url_input.setPlaceholderText("粘贴直播间链接...")
        self._url_input.setAccessibleName("直播间链接输入框")
        self._url_input.returnPressed.connect(self._on_add_room)
        add_card.add_widget(self._url_input)
        self._add_btn = QPushButton("+ 添加房间")
        self._add_btn.setObjectName("addRoomButton")
        self._add_btn.setAccessibleName("添加房间")
        self._add_btn.setFixedHeight(36)
        self._add_btn.setToolTip("添加直播间到工作台")
        self._add_btn.clicked.connect(self._on_add_room)
        add_card.add_widget(self._add_btn)
        right_layout.addWidget(add_card)

        output_card = Card()
        output_title = QLabel("输出目录")
        output_title.setObjectName("card_title")
        output_card.add_widget(output_title)
        settings = QSettings("LSC", "LiveStreamClipper")
        default_output = os.path.join(os.path.expanduser("~"), "LSC", "output")
        self._output_input = InputField()
        self._output_input.setPlaceholderText(default_output)
        self._output_input.setText(str(settings.value("output_dir", default_output)))
        self._output_input.setAccessibleName("多房间输出目录")
        output_row = QHBoxLayout()
        output_row.setContentsMargins(0, 0, 0, 0)
        output_row.setSpacing(8)
        output_row.addWidget(self._output_input, 1)
        self._output_browse_btn = QPushButton("选择")
        self._output_browse_btn.setObjectName("btnSecondary")
        self._output_browse_btn.setFixedHeight(36)
        self._output_browse_btn.setToolTip("选择多房间录制和导出的输出目录")
        self._output_browse_btn.clicked.connect(self._on_browse_output_dir)
        output_row.addWidget(self._output_browse_btn)
        output_card.add_layout(output_row)
        right_layout.addWidget(output_card)

        # 录制信息 Card（与直播录制页右侧信息块保持一致）
        detail_card = Card()
        detail_title = QLabel("录制信息")
        detail_title.setObjectName("card_title")
        detail_card.add_widget(detail_title)
        self._detail = DetailPanel()
        detail_card.add_widget(self._detail)
        right_layout.addWidget(detail_card)

        # 切片列表 Card（ClipListWidget 内部已有标题，这里不再重复添加）
        self._record_settings_card = self._build_record_settings_card()
        right_layout.addWidget(self._record_settings_card)

        right_layout.addStretch()
        right_scroll.setWidget(right_widget)
        self._right_scroll = right_scroll
        self._splitter.addWidget(right_scroll)

        # Set initial splitter sizes (left: 60%, right: 40%)
        self._splitter.setSizes([600, 400])

    def _connect_signals(self) -> None:
        self._controls.timeline.position_changed.connect(self._on_timeline_seek)
        self._controls.mark_in_clicked.connect(self._on_mark_in)
        self._controls.mark_out_clicked.connect(self._on_mark_out)
        self._controls.export_clicked.connect(self._on_export)
        self._controls.play_pause.connect(self._on_play_pause)
        self._controls.seek_back.connect(self._on_seek_back)
        self._controls.seek_fwd.connect(self._on_seek_fwd)
        # Previously dead buttons — now wired to real handlers.
        self._controls.return_live_clicked.connect(self._on_return_live)
        self._controls.fullscreen_clicked.connect(self._on_fullscreen)
        self._controls.preview_range_clicked.connect(self._on_preview_range_clicked)

        # Clip list: add current selection / export all segments
        self._clip_list._add_btn.clicked.connect(self._on_add_clip)
        self._clip_list.export_all_clicked.connect(self._on_export_all_clips)

        # Listen for async connect completion
        self._manager.room_connect_finished.connect(self._on_room_connect_finished)
        # Listen for async batch recording progress/completion
        self._manager.batch_record_progress.connect(self._on_batch_record_progress)
        self._manager.batch_record_finished.connect(self._on_batch_record_finished)
        # Drive timeline/statusbar refresh from the manager's 1s heartbeat
        # instead of relying on manual card clicks to update elapsed time.
        self._manager.global_tick.connect(self._on_global_tick)

    # ── Room management ──────────────────────────────────────

    def _toast(
        self, message: str, *, toast_type: str = "info", title: str = ""
    ):
        """显示一个 Toast 通知。

        返回 Toast 对象（可用 add_action 添加动作按钮），或 None。
        """
        try:
            import main as _main

            return _main.show_toast(message, toast_type=toast_type, title=title)
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning("Toast 显示失败: %s", exc)
            try:
                self._statusbar.show_message(message)
            except Exception:
                pass
        return None

    @staticmethod
    def _open_in_explorer(path: str) -> None:
        """在文件管理器中打开指定目录。"""
        import subprocess
        import sys

        if not path:
            return
        folder = path if os.path.isdir(path) else os.path.dirname(path)
        if sys.platform == "win32":
            os.startfile(folder)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", folder])
        else:
            subprocess.Popen(["xdg-open", folder])

    def _on_add_room(self) -> None:
        url = self._url_input.text().strip()
        if not url:
            return

        if self._manager.room_count() >= MAX_ROOMS:
            self._statusbar.show_message(f"已达最大房间数 ({MAX_ROOMS})")
            self._toast(f"最多支持 {MAX_ROOMS} 个房间", toast_type="warning", title="无法添加")
            return

        room = self._manager.add_room(url)
        if room is None:
            self._statusbar.show_message("无法添加更多房间")
            self._toast("无法添加更多房间", toast_type="error")
            return

        self._url_input.clear()
        self._add_card(room)
        self._refresh()
        self._statusbar.show_message(f"已添加房间: {url}")
        self._toast(f"已添加房间: {url}", toast_type="success")

        # 首次添加房间时显示新手引导
        settings = QSettings("LSC", "LiveStreamClipper")
        if not settings.value("onboarding_shown", False, type=bool):
            settings.setValue("onboarding_shown", True)
            from PySide6.QtCore import QTimer
            QTimer.singleShot(1500, lambda: self._toast(
                "连接直播间后可开启预览、标记选区并导出精彩片段。"
                "勾选「参与批量导出」的房间会一并导出。",
                toast_type="info",
                title="快速上手",
            ))

    def _add_card(self, room: RoomSession) -> None:
        card = RoomCard(room, self._card_container)
        card.selected.connect(self._on_room_selected)
        card.connect_clicked.connect(self._on_connect)
        card.disconnect_clicked.connect(self._on_disconnect)
        card.record_clicked.connect(self._on_record)
        card.stop_clicked.connect(self._on_stop)
        card.remove_clicked.connect(self._on_remove)
        card.preview_clicked.connect(self._on_preview)
        card.pause_clicked.connect(self._on_pause)
        card.resume_clicked.connect(self._on_resume)
        card.fullscreen_clicked.connect(self._enter_fullscreen)
        card.timeline_seek_requested.connect(self._on_card_timeline_seek)
        card.mute_toggled.connect(self._manager.set_preview_muted)
        card.include_toggled.connect(self._on_include_toggled)
        self._cards[room.room_id] = card

        # FlowLayout 自动按卡片自身宽度换行,无需手动指定行列。
        self._card_layout.addWidget(card)
        # 关键修复:新创建的 QWidget 默认是 hidden 状态,而 FlowLayout.doLayout
        # 会用 QWidgetItem.isEmpty()(返回 widget->isHidden())过滤掉 hidden 的
        # 卡片。若不显式 show(),新卡片会被 FlowLayout 跳过、永不排布,表现为
        # 「添加房间后卡片不显示,切到其他页面再切回来才出现」(切页触发的 show
        # 级联让 isHidden() 变 False,FlowLayout 才纳入它)。显式 show() 一行解决。
        card.show()
        card.restore_saved_size()
        self._card_layout.invalidate()
        self._card_container.updateGeometry()
        # Deferred sync: wait for Qt to process the layout before calculating
        # scroll area heights. layout.activate() alone is insufficient because
        # child widget size hints may not be resolved yet.
        QTimer.singleShot(50, lambda c=card: self._deferred_after_add(c))

    def _deferred_after_add(self, card: RoomCard) -> None:
        """Called after layout settles: sync heights, update columns, scroll to card."""
        self._update_grid_columns()
        self._scroll.ensureWidgetVisible(card, 0, 0)

    def _save_timeline_marks_to_room(self, room_id: str | None) -> None:
        """将当前 timeline 的入/出点保存到指定房间（选区独立化的关键）。"""
        if not room_id:
            return
        room = self._manager.get_room(room_id)
        if room is None:
            return
        tl = self._controls.timeline
        room.mark_in = tl.get_in_point()
        room.mark_out = tl.get_out_point()

    def _on_room_selected(self, room_id: str) -> None:
        """Handle card selection with Ctrl/Shift multi-select support.

        - Plain click: single-select (clears previous selection)
        - Ctrl+click: toggle this room in the selection set
        - Shift+click: range-select from the last anchor to this room
        """
        # 切房前：把当前 timeline 选区保存到旧房间
        old_primary = self._selected_room_id

        from PySide6.QtGui import QGuiApplication
        mods = QGuiApplication.keyboardModifiers()
        ctrl = bool(mods & Qt.ControlModifier)
        shift = bool(mods & Qt.ShiftModifier)

        if ctrl:
            # Toggle membership in the multi-selection set.
            if room_id in self._selected_room_ids:
                self._selected_room_ids.discard(room_id)
            else:
                self._selected_room_ids.add(room_id)
            # Anchor stays the last-clicked room for Shift-range.
            self._selected_room_id = room_id
        elif shift and self._selected_room_id is not None:
            # Range-select from anchor to clicked room (by insertion order).
            ordered = list(self._cards.keys())
            try:
                start_idx = ordered.index(self._selected_room_id)
                end_idx = ordered.index(room_id)
            except ValueError:
                start_idx, end_idx = 0, 0
            lo, hi = min(start_idx, end_idx), max(start_idx, end_idx)
            self._selected_room_ids = set(ordered[lo:hi + 1])
        else:
            # Plain click — single selection.
            self._selected_room_ids = {room_id}
            self._selected_room_id = room_id

        # If only one room is selected, treat it as the focused room so the
        # detail panel and timeline show its data.
        if len(self._selected_room_ids) == 1:
            self._selected_room_id = next(iter(self._selected_room_ids))
        elif len(self._selected_room_ids) == 0:
            self._selected_room_id = None

        # 选区独立化：将旧房间的 timeline 选区保存，然后恢复新房间的选区
        self._save_timeline_marks_to_room(old_primary)

        # Update card visual states.
        for rid, card in self._cards.items():
            card.set_selected(rid in self._selected_room_ids)

        # Update sync mode indicator in control bar.
        self._controls.set_sync_count(len(self._selected_room_ids))

        # Detail panel shows the anchor room (or None if multi-selected).
        room = self._manager.get_room(self._selected_room_id) if self._selected_room_id else None
        self._detail.show_room(room)
        self._update_timeline()
        self._update_multi_select_badges()
        self.room_selected.emit(room_id)

    def _update_multi_select_badges(self) -> None:
        """多选时在所有选中卡片上显示同步时间戳徽章。"""
        if len(self._selected_room_ids) <= 1:
            for card in self._cards.values():
                card.hide_sync_badge()
            return
        # 取主房间的当前位置作为同步时间戳
        pos = 0.0
        if self._selected_room_id:
            pos = self._manager.get_preview_position(self._selected_room_id)
        time_text = fmt_time(pos)
        for rid in self._selected_room_ids:
            card = self._cards.get(rid)
            if card:
                card.show_sync_badge(time_text)
        # 未选中的卡片隐藏徽章
        for rid, card in self._cards.items():
            if rid not in self._selected_room_ids:
                card.hide_sync_badge()

    def _on_align_live(self) -> None:
        """Align all active previews to their live edge in one click."""
        count = self._manager.align_previews_to_live()
        if count:
            msg = f"已对齐 {count} 路预览到直播画面"
            self._statusbar.show_message(msg)
            self._toast(msg, toast_type="success")
        else:
            self._statusbar.show_message("没有正在预览的房间")
            self._toast("没有正在预览的房间", toast_type="warning", title="无法对齐")
        self._update_timeline()

    def _on_connect(self, room_id: str) -> None:
        """Start async connection to avoid blocking the UI thread."""
        room = self._manager.get_room(room_id)
        if room is None:
            return
        room.is_connecting = True
        room.last_error = ""
        self._refresh_card(room_id)
        quality_widget = getattr(self, "_record_quality", None)
        quality = quality_widget.selected if quality_widget is not None else "原画"
        self._manager.connect_room(room_id, async_mode=True, quality_preset=quality)
        self._statusbar.show_message("正在连接...")

    def _on_room_connect_finished(self, room_id: str, success: bool, error: str) -> None:
        """Callback when async room connection completes."""
        room = self._manager.get_room(room_id)
        if room is None:
            return
        if success:
            name = room.streamer_name or room.room_url
            self._statusbar.show_message(f"连接成功: {name}")
            self._toast(f"连接成功: {name}", toast_type="success")
        else:
            from lsc.utils.error_messages import humanize_error
            friendly = humanize_error(error)
            self._statusbar.show_message(f"连接失败: {friendly}")
            self._toast(friendly, toast_type="error", title="连接失败")
        self._refresh_card(room_id)

    def _on_disconnect(self, room_id: str) -> None:
        card = self._cards.get(room_id)
        if card:
            card.remove_preview_widget()
        self._manager.disconnect_room(room_id)
        self._refresh_card(room_id)
        self._statusbar.show_message("已断开连接")

    def _on_record(self, room_id: str) -> None:
        room = self._manager.get_room(room_id)
        if room is not None:
            room.last_error = ""
        output_dir, encoder, crf, param_mode, bitrate, bitrate_unit = self._get_recording_profile()
        self._manager.start_recording(
            room_id,
            output_dir,
            encoder,
            crf,
            param_mode=param_mode,
            bitrate=bitrate,
            bitrate_unit=bitrate_unit,
        )
        self._refresh_card(room_id)
        room = self._manager.get_room(room_id)
        if room and room.is_recording:
            self._statusbar.show_message("录制已开始")
            self._toast("录制已开始", toast_type="success")
        elif room and room.last_error:
            self._statusbar.show_message(f"录制失败: {room.friendly_error}")
            self._toast(room.friendly_error, toast_type="error", title="录制失败")
        else:
            self._statusbar.show_message("录制启动失败")
            self._toast("录制启动失败", toast_type="error")

    def _on_stop(self, room_id: str) -> None:
        self._manager.stop_recording(room_id)
        self._refresh_card(room_id)
        self._statusbar.show_message("录制已停止")

    def _on_remove(self, room_id: str) -> None:
        """删除房间，并压入撤销栈。"""
        room = self._manager.get_room(room_id)
        if room is None:
            return
        # 保存可重建状态
        saved_url = room.room_url
        saved_include = room.include_in_cut

        def _do_remove() -> None:
            card = self._cards.pop(room_id, None)
            if card:
                card.remove_preview_widget()
                self._card_layout.removeWidget(card)
                card.deleteLater()
            self._manager.remove_room(room_id)
            if self._selected_room_id == room_id:
                self._selected_room_id = None
                self._detail.show_room(None)
            self._rebuild_grid()
            self._refresh()

        def _do_restore() -> None:
            new_room = self._manager.add_room(saved_url)
            if new_room is not None:
                new_room.include_in_cut = saved_include
                self._add_card(new_room)
                self._refresh()
                self._statusbar.show_message(f"已撤销删除: {saved_url}")

        cmd = Command(
            description=f"删除房间 {room.streamer_name or room.room_url}",
            undo=_do_restore,
            redo=_do_remove,
        )
        self._undo.execute(cmd)

    def _on_preview(self, room_id: str) -> None:
        ok = self._manager.start_preview(room_id)
        if ok:
            room = self._manager.get_room(room_id)
            card = self._cards.get(room_id)
            if room and card and room.preview_widget is not None:
                card.set_preview_widget(room.preview_widget)
        self._refresh_card(room_id)
        if not ok:
            room = self._manager.get_room(room_id)
            msg = room.preview_error if room and room.preview_error else "无法开启预览"
            self._statusbar.show_message(msg)
            self._toast(msg, toast_type="error", title="预览失败")

    def _on_pause(self, room_id: str) -> None:
        self._manager.pause_preview(room_id)
        self._refresh_card(room_id)

    def _on_resume(self, room_id: str) -> None:
        self._manager.resume_preview(room_id)
        self._refresh_card(room_id)

    def _on_include_toggled(self, room_id: str, checked: bool) -> None:
        room = self._manager.get_room(room_id)
        if room:
            room.include_in_cut = checked

    def _on_batch_record(self) -> None:
        rooms = self._manager.list_rooms()
        candidates = [r for r in rooms if r.is_connected and not r.is_recording]
        if not candidates:
            self._statusbar.show_message("没有可录制的房间")
            self._toast("没有可录制的房间", toast_type="warning", title="无法录制")
            return
        # 显示即将录制的房间数作为反馈
        self._statusbar.show_message(f"正在启动 {len(candidates)} 路录制...")
        self._toast(f"正在启动 {len(candidates)} 路录制", toast_type="info")
        # Run batch recording in a background thread to avoid blocking the UI
        # while FFmpeg starts up for each room.
        output_dir, encoder, crf, param_mode, bitrate, bitrate_unit = self._get_recording_profile()
        started = self._manager.start_recording_all_async(
            output_dir,
            encoder,
            crf,
            param_mode=param_mode,
            bitrate=bitrate,
            bitrate_unit=bitrate_unit,
        )
        if not started:
            self._statusbar.show_message("批量录制启动失败（磁盘空间不足或已在进行中）")
            self._toast("批量录制启动失败", toast_type="error")

    def _on_batch_record_progress(self, room_id: str, success: bool) -> None:
        self._refresh_card(room_id)

    def _on_batch_record_finished(self, started_count: int, total_count: int) -> None:
        self._refresh_all()
        msg = f"批量录制: {started_count}/{total_count} 路已启动"
        self._statusbar.show_message(msg)
        if started_count == total_count:
            self._toast(msg, toast_type="success")
        elif started_count > 0:
            self._toast(msg, toast_type="warning")
        else:
            self._toast(msg, toast_type="error")

    def _on_batch_stop(self) -> None:
        count = 0
        for room in self._manager.list_rooms():
            if room.is_recording:
                self._manager.stop_recording(room.room_id)
                count += 1
        self._refresh_all()
        msg = f"批量停止: {count} 路已停止"
        self._statusbar.show_message(msg)
        self._toast(msg, toast_type="success" if count > 0 else "warning")

    def _on_mute_all(self) -> None:
        all_muted = all(r.preview_muted for r in self._manager.list_rooms())
        new_muted = not all_muted
        for room in self._manager.list_rooms():
            self._manager.set_preview_muted(room.room_id, new_muted)
        self._refresh_all()
        self._mute_all_btn.setText("取消静音" if new_muted else "全部静音")
        self._mute_all_btn.setChecked(new_muted)
        self._statusbar.show_message("全部静音" if new_muted else "已取消全部静音")

    # ── Timeline ─────────────────────────────────────────────

    def _update_timeline(self) -> None:
        room = self._manager.get_room(self._selected_room_id) if self._selected_room_id else None
        if room and room.controller:
            # Duration: prefer the preview widget's reported duration, fall
            # back to recording elapsed time or the recorded file's actual duration.
            duration = self._manager.get_preview_duration(room.room_id)
            if duration <= 0:
                duration = getattr(room.controller, "total_sec", 0) or 0
            if duration <= 0:
                duration = self._room_video_duration(room)
            position = self._manager.get_preview_position(room.room_id)
            # 选区独立化：从房间状态恢复入/出点到 timeline
            self._controls.timeline.set_data(
                duration=duration, position=position,
                start=room.mark_in, end=room.mark_out
            )
            self._controls.set_time(position, duration)
            # 只要有可导出的录制文件，就启用 timeline 控制（入/出点、导出）
            has_video = self._room_has_exportable_video(room)
            self._controls.set_recording(room.is_recording or has_video)
            # Reflect live availability for the "return to live" button.
            self._controls.set_live_available(room.is_connected and room.preview_enabled)
        else:
            self._controls.timeline.set_data(duration=0, position=0)
            self._controls.set_time(0, 0)
            self._controls.set_recording(False)
            self._controls.set_live_available(False)
        self._sync_range_state()

    @staticmethod
    def _room_has_exportable_video(room) -> bool:
        controller = room.controller if room else None
        if controller is None:
            return False
        path = getattr(controller, "video_path", "") or ""
        return bool(path and os.path.isfile(path))

    def _room_video_duration(self, room) -> float:
        controller = room.controller if room else None
        if controller is None:
            return 0.0
        path = getattr(controller, "video_path", "") or ""
        if not path or not os.path.isfile(path):
            return 0.0
        return controller.probe_video_duration()

    def _on_timeline_seek(self, position: float) -> None:
        # When multiple rooms are selected, seek all of them in lockstep so
        # the user can scrub several streams at the same timestamp.
        if len(self._selected_room_ids) > 1:
            self._manager.seek_selected_previews(list(self._selected_room_ids), position)
        else:
            room = self._manager.get_room(self._selected_room_id) if self._selected_room_id else None
            if room and room.controller:
                self._manager.seek_preview(room.room_id, position)
        self._update_timeline()

    def _on_card_timeline_seek(self, room_id: str, position: float) -> None:
        room = self._manager.get_room(room_id)
        if room is None or not room.preview_enabled:
            return
        self._manager.seek_preview(room_id, position)
        if self._selected_room_id == room_id:
            self._update_timeline()
        self._update_card_timeline(room_id)

    def _on_mark_in(self) -> None:
        room = self._manager.get_room(self._selected_room_id) if self._selected_room_id else None
        if not room or not room.controller:
            return
        pos = self._manager.get_preview_position(room.room_id)
        tl = self._controls.timeline
        end = tl.get_out_point()
        if end is not None and pos > end:
            # 入点超过出点时，自动交换
            tl.set_data(start=end, end=pos)
            # 写回所有选中房间
            for rid in self._selected_room_ids:
                r = self._manager.get_room(rid)
                if r:
                    r.mark_in, r.mark_out = end, pos
        else:
            tl.set_in_point(pos)
            for rid in self._selected_room_ids:
                r = self._manager.get_room(rid)
                if r:
                    r.mark_in = pos
        self._sync_range_state()

    def _on_mark_out(self) -> None:
        room = self._manager.get_room(self._selected_room_id) if self._selected_room_id else None
        if not room or not room.controller:
            return
        pos = self._manager.get_preview_position(room.room_id)
        tl = self._controls.timeline
        start = tl.get_in_point()
        if start is not None and pos < start:
            # 出点小于入点时，自动交换
            tl.set_data(start=pos, end=start)
            for rid in self._selected_room_ids:
                r = self._manager.get_room(rid)
                if r:
                    r.mark_in, r.mark_out = pos, start
        else:
            tl.set_out_point(pos)
            for rid in self._selected_room_ids:
                r = self._manager.get_room(rid)
                if r:
                    r.mark_out = pos
        self._sync_range_state()

    def _sync_range_state(self) -> None:
        """同步入/出点状态到控制栏和切片列表的"添加选区"按钮。"""
        tl = self._controls.timeline
        has_in = tl.get_in_point() is not None
        has_out = tl.get_out_point() is not None
        self._controls.set_range_state(has_in, has_out)
        self._controls.set_export_enabled(has_in and has_out)
        self._clip_list.set_add_enabled(has_in and has_out)

    # ── Export progress ──────────────────────────────────────

    def _on_export_progress(self, room_id: str, percent: float, elapsed: float, total: float) -> None:
        """单个房间导出进度回调，聚合后更新状态栏进度条。"""
        self._export_progress[room_id] = percent
        if self._export_total_tasks > 0:
            # 基于已完成任务数的加权进度
            completed_ratio = self._export_completed_tasks / self._export_total_tasks
            # 加上当前进行中的任务的进度贡献
            in_progress_ratio = sum(self._export_progress.values()) / (self._export_total_tasks * 100)
            overall = (completed_ratio + in_progress_ratio) * 100
            self._statusbar.show_progress(min(overall, 100))
        elif self._export_progress:
            avg = sum(self._export_progress.values()) / len(self._export_progress)
            self._statusbar.show_progress(avg)

    def _finish_export_progress(self, room_id: str) -> None:
        """某个房间导出完成后从跟踪中移除，全部完成则隐藏进度条。"""
        self._export_progress.pop(room_id, None)
        self._export_completed_tasks += 1
        if not self._export_progress:
            self._statusbar.hide_progress()
            # 重置计数器
            self._export_total_tasks = 0
            self._export_completed_tasks = 0

    def _on_add_clip(self) -> None:
        """将当前入/出点选区添加为切片列表中的一个片段。"""
        tl = self._controls.timeline
        start = tl.get_in_point()
        end = tl.get_out_point()
        if start is None or end is None:
            self._toast("请先设置入点和出点", toast_type="warning", title="无法添加片段")
            return
        index = self._clip_list.add_segment(start, end)
        if index >= 0:
            self._toast(
                f"已添加片段 #{index + 1} ({fmt_time(end - start)})",
                toast_type="success",
            )
            # 添加后清除当前选区，方便标记下一段
            tl.clear_selection()
            self._sync_range_state()

    def _on_export_all_clips(self) -> None:
        """导出切片列表中的所有片段。"""
        segments = self._clip_list.segments()
        if not segments:
            self._toast("切片列表为空", toast_type="warning", title="无法导出")
            return
        rooms = self._manager.get_rooms_for_cut()
        if not rooms:
            self._toast("没有参与批量导出的房间", toast_type="warning", title="无法导出")
            return

        # 导出前弹出确认对话框（含编码参数配置）
        room_names = [r.streamer_name or r.platform or r.room_url for r in rooms]
        dlg = ExportConfirmDialog(segments, room_names, parent=self)
        if dlg.exec() != ExportConfirmDialog.Accepted:
            return
        export_profile = dlg.get_profile()

        total_segments = len(segments)
        pending: list[tuple[str, int]] = []  # (room_id, seg_index)
        succeeded: list[tuple[str, int, str, float]] = []
        failed: list[tuple[str, int, str]] = []

        for seg_idx, seg in enumerate(segments):
            for room in rooms:
                controller = room.controller
                video_path = controller.video_path if controller else ""
                if not video_path:
                    continue
                fallback_output, _encoder, _crf = self._get_recording_settings()
                output_dir = controller.output_dir if controller and controller.output_dir else fallback_output
                title = f"{room.streamer_name or room.platform}_clip{seg_idx + 1}_{seg.start:.0f}s-{seg.end:.0f}s"
                pending.append((room.room_id, seg_idx))

                def _on_done(success, path, error, size_mb, _rid=room.room_id, _si=seg_idx):
                    if success:
                        succeeded.append((_rid, _si, path, size_mb))
                    else:
                        failed.append((_rid, _si, error))
                    self._finish_export_progress(_rid)
                    if not pending:
                        return
                    # 移除当前任务
                    pending[:] = [p for p in pending if not (p[0] == _rid and p[1] == _si)]
                    if pending:
                        return
                    # 全部完成
                    total = len(succeeded) + len(failed)
                    if not failed:
                        total_size = sum(s for _, _, _, s in succeeded)
                        msg = f"批量导出完成: {len(succeeded)}/{total} 成功 ({total_size:.1f}MB)"
                        self._statusbar.show_message(msg)
                        toast = self._toast(msg, toast_type="success", title="导出完成")
                        if toast and succeeded:
                            first_path = succeeded[0][2]
                            toast.add_action("打开文件夹", lambda: self._open_in_explorer(first_path))
                    else:
                        msg = (
                            f"批量导出: {len(succeeded)}/{total} 成功, "
                            f"{len(failed)} 失败（首条: {failed[0][2]}）"
                        )
                        self._statusbar.show_message(msg)
                        self._toast(msg, toast_type="warning", title="导出完成")

                self._manager.start_export(
                    room.room_id, seg.start, seg.end, output_dir, title,
                    on_done=_on_done, profile=export_profile,
                    on_progress=lambda p, e, t, _rid=room.room_id: self._on_export_progress(_rid, p, e, t),
                )

        if pending:
            msg = f"正在导出 {len(pending)} 个片段（{total_segments} 段 × {len(rooms)} 房间）..."
            self._statusbar.show_message(msg)
            self._toast(msg, toast_type="info", title="开始导出")
            self._export_total_tasks = len(pending)
            self._export_completed_tasks = 0

    def _on_export(self) -> None:
        """Export selected time range from all rooms marked for cut.

        选区独立化后，每个房间使用自己的 mark_in/mark_out。
        若房间未设置选区，回退到 timeline 的当前值。
        """
        tl = self._controls.timeline
        # 默认使用当前 timeline 的入/出点（作为回退值）
        default_start = tl.get_in_point()
        default_end = tl.get_out_point()
        if default_start is None or default_end is None:
            self._statusbar.show_message("请先设置入点和出点")
            self._toast("请先设置入点和出点", toast_type="warning", title="无法导出")
            return

        rooms = self._manager.get_rooms_for_cut()
        if not rooms:
            self._statusbar.show_message("没有参与批量导出的房间")
            self._toast("没有参与批量导出的房间", toast_type="warning", title="无法导出")
            return

        if default_end <= default_start:
            self._statusbar.show_message("出点必须在入点之后")
            self._toast("出点必须在入点之后", toast_type="warning", title="无法导出")
            return

        # 导出前弹出确认对话框（含编码参数配置）
        single_segment = ClipSegment(start=default_start, end=default_end)
        room_names = [r.streamer_name or r.platform or r.room_url for r in rooms]
        dlg = ExportConfirmDialog([single_segment], room_names, parent=self)
        if dlg.exec() != ExportConfirmDialog.Accepted:
            return
        export_profile = dlg.get_profile()

        # Accumulate per-room export results and report a single summary
        # once all exports finish, instead of overwriting the status bar
        # with each completion.
        pending: list[str] = []
        succeeded: list[tuple[str, str, float]] = []  # room_id, path, size_mb
        failed: list[tuple[str, str]] = []  # room_id, error

        for room in rooms:
            controller = room.controller
            video_path = controller.video_path if controller else ""
            if not video_path:
                continue
            # 选区独立化：优先使用每个房间自己的入/出点，回退到 timeline 的默认值
            room_start = room.mark_in if room.mark_in is not None else default_start
            room_end = room.mark_out if room.mark_out is not None else default_end
            if room_end <= room_start:
                continue  # 跳过选区无效的房间
            fallback_output, _encoder, _crf = self._get_recording_settings()
            output_dir = controller.output_dir if controller and controller.output_dir else fallback_output
            title = f"{room.streamer_name or room.platform}_{room_start:.0f}s-{room_end:.0f}s"
            pending.append(room.room_id)

            def _on_done(success, path, error, size_mb, _room_id=room.room_id):
                if success:
                    succeeded.append((_room_id, path, size_mb))
                else:
                    failed.append((_room_id, error))
                self._finish_export_progress(_room_id)
                # Report unified summary when all exports have finished.
                if not pending:
                    return
                pending.remove(_room_id)
                if pending:
                    return
                # All done — emit a single consolidated message.
                total = len(succeeded) + len(failed)
                if not failed:
                    total_size = sum(s for _, _, s in succeeded)
                    summary = (
                        f"批量导出完成: {len(succeeded)}/{total} 成功 "
                        f"({total_size:.1f}MB)"
                    )
                    self._statusbar.show_message(summary)
                    toast = self._toast(summary, toast_type="success", title="导出完成")
                    if toast and succeeded:
                        first_path = succeeded[0][1]
                        toast.add_action("打开文件夹", lambda: self._open_in_explorer(first_path))
                else:
                    first_err = failed[0][1]
                    summary = (
                        f"批量导出: {len(succeeded)}/{total} 成功, "
                        f"{len(failed)} 失败（首条错误: {first_err}）"
                    )
                    self._statusbar.show_message(summary)
                    self._toast(summary, toast_type="warning", title="导出完成")

            if self._manager.start_export(
                room.room_id, room_start, room_end, output_dir, title,
                on_done=_on_done, profile=export_profile,
                on_progress=lambda p, e, t, _rid=room.room_id: self._on_export_progress(_rid, p, e, t),
            ):
                pass
            else:
                # start_export returned False synchronously — treat as failure.
                pending.remove(room.room_id)
                failed.append((room.room_id, "无法启动导出任务"))

        if pending:
            self._statusbar.show_message(f"正在导出 {len(pending)} 个片段...")
            self._export_total_tasks = len(pending)
            self._export_completed_tasks = 0
        elif succeeded or failed:
            total = len(succeeded) + len(failed)
            self._statusbar.show_message(
                f"批量导出: {len(succeeded)}/{total} 成功"
            )
        else:
            self._statusbar.show_message("没有可导出的录制文件")

    def _on_play_pause(self) -> None:
        """Toggle playback on the currently selected room's preview.

        Previously this only flipped a local boolean and never reached the
        mpv widget, so the button was purely decorative. Now it delegates
        to the manager so the video actually pauses/resumes.
        """
        room = self._manager.get_room(self._selected_room_id) if self._selected_room_id else None
        if room is None or not room.preview_enabled:
            self._statusbar.show_message("请先开启预览")
            # Keep the control bar icon in sync with reality.
            self._controls.set_playing(False)
            return
        if room.preview_paused:
            self._manager.resume_preview(room.room_id)
            self._controls.set_playing(True)
        else:
            self._manager.pause_preview(room.room_id)
            self._controls.set_playing(False)
        self._refresh_card(room.room_id)

    def _on_seek_back(self) -> None:
        # Multi-select: move every selected preview back 10 seconds.
        if len(self._selected_room_ids) > 1:
            for rid in self._selected_room_ids:
                pos = max(0.0, self._manager.get_preview_position(rid) - 10.0)
                self._manager.seek_preview(rid, pos)
            self._update_timeline()
            return
        room = self._manager.get_room(self._selected_room_id) if self._selected_room_id else None
        if room is None or not room.preview_enabled:
            return
        pos = max(0.0, self._manager.get_preview_position(room.room_id) - 10.0)
        self._manager.seek_preview(room.room_id, pos)
        self._update_timeline()

    def _on_seek_fwd(self) -> None:
        # Multi-select: move every selected preview forward 10 seconds.
        if len(self._selected_room_ids) > 1:
            for rid in self._selected_room_ids:
                duration = self._manager.get_preview_duration(rid)
                if duration <= 0:
                    r = self._manager.get_room(rid)
                    duration = float(getattr(r.controller, "total_sec", 0) or 0) if r and r.controller else 0.0
                pos = min(duration, self._manager.get_preview_position(rid) + 10.0)
                self._manager.seek_preview(rid, pos)
            self._update_timeline()
            return
        room = self._manager.get_room(self._selected_room_id) if self._selected_room_id else None
        if room is None or not room.preview_enabled:
            return
        duration = self._manager.get_preview_duration(room.room_id)
        if duration <= 0:
            duration = getattr(room.controller, "total_sec", 0) or 0
        pos = min(duration, self._manager.get_preview_position(room.room_id) + 10.0)
        self._manager.seek_preview(room.room_id, pos)
        self._update_timeline()

    def _on_return_live(self) -> None:
        """Jump the selected room's preview back to the live edge.

        For live streams mpv follows the growing file tail by default;
        seeking backwards then pressing 'return to live' should snap
        back to the latest available position.
        """
        room = self._manager.get_room(self._selected_room_id) if self._selected_room_id else None
        if room is None or not room.preview_enabled:
            return
        duration = self._manager.get_preview_duration(room.room_id)
        if duration <= 0:
            duration = getattr(room.controller, "total_sec", 0) or 0
        self._manager.seek_preview(room.room_id, duration)
        self._statusbar.show_message("已回到直播画面")
        self._update_timeline()

    def _on_preview_range_clicked(self) -> None:
        """切换选区试听：循环播放当前房间的 [mark_in, mark_out] 区间。"""
        if self._manager.is_range_loop_active():
            self._manager.stop_range_loop()
            self._controls.set_range_looping(False)
            return
        room = self._manager.get_room(self._selected_room_id) if self._selected_room_id else None
        if not room or not room.preview_enabled:
            self._statusbar.show_message("请先开启预览")
            return
        mark_in = room.mark_in
        mark_out = room.mark_out
        if mark_in is None or mark_out is None:
            self._statusbar.show_message("请先设置入点和出点")
            return
        start, end = min(mark_in, mark_out), max(mark_in, mark_out)
        if end - start < 0.5:
            self._statusbar.show_message("选区太短，无法试听")
            return
        self._manager.start_range_loop(room.room_id, start, end)
        self._controls.set_range_looping(True)
        self._statusbar.show_message(f"正在试听选区 ({fmt_time(end - start)})")

    def _on_fullscreen(self) -> None:
        """Toggle a fullscreen preview for the currently selected room.

        Reparents the room's MpvWidget into a temporary top-level window so
        the user gets an immersive view of a single stream. On exit the
        widget is reparented back into its RoomCard. This avoids creating a
        second libmpv instance / stream connection.
        """
        room = self._manager.get_room(self._selected_room_id) if self._selected_room_id else None
        if room is None or room.preview_widget is None:
            self._statusbar.show_message("请先选择一个有预览的房间")
            return
        self._enter_fullscreen(room.room_id)

    def _enter_fullscreen(self, room_id: str) -> None:
        """Enter fullscreen preview for the given room with player controls."""
        if self._fullscreen_window is not None:
            self._fullscreen_window.close()
            return

        room = self._manager.get_room(room_id)
        card = self._cards.get(room_id)
        if room is None or card is None or room.preview_widget is None:
            return

        widget = room.preview_widget
        card.remove_preview_widget()

        win = QWidget(self, Qt.Window)
        win.setWindowTitle(f"全屏预览 - {room.streamer_name or room.platform_name or room.room_url}")
        win.setObjectName("fullscreenPreviewWindow")
        win.setWindowFlags(Qt.Window | Qt.WindowMinimizeButtonHint | Qt.WindowCloseButtonHint)

        lay = QVBoxLayout(win)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        win.setMouseTracking(True)

        surface = QWidget(win)
        surface.setObjectName("fullscreenPreviewSurface")
        surface.setMouseTracking(True)
        surface_layout = QVBoxLayout(surface)
        surface_layout.setContentsMargins(0, 0, 0, 0)
        surface_layout.setSpacing(0)

        widget.setMouseTracking(True)
        widget.setParent(surface)
        widget.setMinimumSize(0, 0)
        widget.show()
        surface_layout.addWidget(widget, 1)
        rebind_fn = getattr(widget, "rebind_video_output", None)
        if callable(rebind_fn):
            rebind_fn()
        lay.addWidget(surface, 1)

        controls_height = 74
        controls = QWidget(surface)
        controls.setObjectName("fullscreenPlayerControls")
        controls.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        controls.setMouseTracking(True)
        controls.setFixedHeight(controls_height)
        controls_layout = QVBoxLayout(controls)
        controls_layout.setContentsMargins(14, 0, 14, 9)
        controls_layout.setSpacing(4)

        progress = QSlider(Qt.Orientation.Horizontal, controls)
        progress.setObjectName("fullscreenProgressSlider")
        progress.setMouseTracking(True)
        progress.setMaximumHeight(12)
        progress.setRange(0, 0)
        controls_layout.addWidget(progress)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        play_btn = _FullscreenIconButton("pause", controls)
        play_btn.setObjectName("fullscreenPlayButton")
        play_btn.setFixedSize(40, 34)
        play_btn.setToolTip("播放/暂停")
        row.addWidget(play_btn)

        time_label = QLabel("00:00 / 00:00", controls)
        time_label.setObjectName("fullscreenTimeLabel")
        row.addWidget(time_label)
        row.addStretch(1)

        mute_btn = QCheckBox("静音", controls)
        mute_btn.setObjectName("fullscreenMuteButton")
        mute_btn.setChecked(bool(room.preview_muted))
        row.addWidget(mute_btn)

        min_btn = _FullscreenIconButton("minimize", controls)
        min_btn.setObjectName("fullscreenMinimizeButton")
        min_btn.setFixedSize(40, 34)
        min_btn.setToolTip("最小化")
        min_btn.clicked.connect(win.showMinimized)
        row.addWidget(min_btn)

        exit_btn = _FullscreenIconButton("exit_fullscreen", controls)
        exit_btn.setObjectName("fullscreenExitButton")
        exit_btn.setFixedSize(40, 34)
        exit_btn.setToolTip("退出全屏")
        exit_btn.clicked.connect(win.close)
        row.addWidget(exit_btn)

        controls_layout.addLayout(row)

        self._fullscreen_surface = surface
        self._fullscreen_controls = controls
        self._fullscreen_progress = progress
        self._fullscreen_play_btn = play_btn
        self._fullscreen_mute_btn = mute_btn
        self._fullscreen_minimize_btn = min_btn
        self._fullscreen_exit_btn = exit_btn
        self._fullscreen_controls_height = controls_height

        syncing_progress = {"active": False}

        def _position_seconds() -> float:
            pos_fn = getattr(widget, "position_sec", None)
            if callable(pos_fn):
                try:
                    return float(pos_fn() or 0.0)
                except Exception:
                    return 0.0
            return self._manager.get_preview_position(room_id)

        def _duration_seconds() -> float:
            dur_fn = getattr(widget, "duration_sec", None)
            if callable(dur_fn):
                try:
                    return float(dur_fn() or 0.0)
                except Exception:
                    return 0.0
            return self._manager.get_preview_duration(room_id)

        def _sync_fullscreen_controls() -> None:
            duration = max(0.0, _duration_seconds())
            position = max(0.0, min(_position_seconds(), duration if duration > 0 else _position_seconds()))
            syncing_progress["active"] = True
            progress.setRange(0, max(0, int(duration)))
            progress.setValue(max(0, int(position)))
            syncing_progress["active"] = False
            time_label.setText(f"{fmt_time(position)} / {fmt_time(duration)}")
            play_btn.set_icon_kind("play" if room.preview_paused else "pause")
            mute_btn.blockSignals(True)
            mute_btn.setChecked(bool(room.preview_muted))
            mute_btn.blockSignals(False)

        def _toggle_fullscreen_play() -> None:
            if room.preview_paused:
                resume_fn = getattr(widget, "resume", None)
                if callable(resume_fn):
                    self._manager.resume_preview(room_id)
                else:
                    toggle_fn = getattr(widget, "toggle_play_pause", None)
                    if callable(toggle_fn):
                        toggle_fn()
                    room.preview_paused = False
            else:
                pause_fn = getattr(widget, "pause", None)
                if callable(pause_fn):
                    self._manager.pause_preview(room_id)
                else:
                    toggle_fn = getattr(widget, "toggle_play_pause", None)
                    if callable(toggle_fn):
                        toggle_fn()
                    room.preview_paused = True
            _sync_fullscreen_controls()
            self._refresh_card(room_id)

        def _set_fullscreen_muted(muted: bool) -> None:
            self._manager.set_preview_muted(room_id, muted)
            _sync_fullscreen_controls()
            self._refresh_card(room_id)

        def _seek_fullscreen(value: int) -> None:
            if syncing_progress["active"]:
                return
            seek_to_fn = getattr(widget, "seek_to", None)
            if callable(seek_to_fn):
                seek_to_fn(float(value))
            else:
                seek_fn = getattr(widget, "seek", None)
                if callable(seek_fn):
                    seek_fn(float(value))
            controller = getattr(room, "controller", None)
            if controller is not None:
                controller.current_sec = float(value)
            _sync_fullscreen_controls()
            self._update_card_timeline(room_id)

        play_btn.clicked.connect(_toggle_fullscreen_play)
        mute_btn.toggled.connect(_set_fullscreen_muted)
        progress.valueChanged.connect(_seek_fullscreen)

        fullscreen_timer = QTimer(win)
        fullscreen_timer.setInterval(500)
        fullscreen_timer.timeout.connect(_sync_fullscreen_controls)
        fullscreen_timer.start()

        def _place_controls() -> None:
            controls.setGeometry(
                0,
                max(0, surface.height() - controls_height),
                max(1, surface.width()),
                controls_height,
            )
            controls.raise_()

        def _surface_resize_event(event) -> None:
            QWidget.resizeEvent(surface, event)
            _place_controls()

        surface.resizeEvent = _surface_resize_event

        def _hide_controls() -> None:
            controls.setVisible(False)

        def _show_controls() -> None:
            controls.setVisible(True)
            controls.setFixedHeight(controls_height)
            _place_controls()
            auto_hide_timer.start()

        auto_hide_timer = QTimer(win)
        auto_hide_timer.setSingleShot(True)
        auto_hide_timer.setInterval(2400)
        auto_hide_timer.timeout.connect(_hide_controls)
        activity_filter = _FullscreenActivityFilter(_show_controls, win)
        for watched in (win, surface, widget, controls, progress, play_btn, mute_btn, min_btn, exit_btn):
            watched.installEventFilter(activity_filter)
        self._fullscreen_hide_controls = _hide_controls
        self._fullscreen_show_controls = _show_controls
        self._fullscreen_auto_hide_timer = auto_hide_timer
        self._fullscreen_activity_filter = activity_filter

        def _close_fullscreen() -> None:
            win.close()

        esc_shortcut = QShortcut(QKeySequence(Qt.Key_Escape), win)
        esc_shortcut.activated.connect(_close_fullscreen)
        self._fullscreen_esc_shortcut = esc_shortcut

        def _key_press_event(event) -> None:
            if event.key() == Qt.Key_Escape:
                event.accept()
                win.close()
                return
            QWidget.keyPressEvent(win, event)

        win.keyPressEvent = _key_press_event
        _sync_fullscreen_controls()
        _place_controls()
        auto_hide_timer.start()

        def _restore() -> None:
            auto_hide_timer.stop()
            fullscreen_timer.stop()
            widget.setParent(card)
            card.set_preview_widget(widget)
            self._fullscreen_window = None
            self._fullscreen_surface = None
            self._fullscreen_controls = None
            self._fullscreen_progress = None
            self._fullscreen_play_btn = None
            self._fullscreen_mute_btn = None
            self._fullscreen_minimize_btn = None
            self._fullscreen_exit_btn = None
            self._fullscreen_esc_shortcut = None
            self._fullscreen_auto_hide_timer = None
            self._fullscreen_activity_filter = None
            self._fullscreen_hide_controls = None
            self._fullscreen_show_controls = None
            self._fullscreen_controls_height = 0
            self._controls.set_fullscreen(False)

        win.closeEvent = lambda e: (_restore(), QWidget.closeEvent(win, e))
        win.showFullScreen()
        self._fullscreen_window = win
        self._controls.set_fullscreen(True)

    def _on_global_tick(self) -> None:
        """Refresh timeline and statusbar on every manager heartbeat.

        Replaces the old behaviour where elapsed time only updated when
        the user clicked a card.
        """
        if self._selected_room_id is not None:
            self._update_timeline()
        for room_id in list(self._cards.keys()):
            self._update_card_timeline(room_id)
        self._update_statusbar()
        # 多选时刷新同步时间戳徽章
        if len(self._selected_room_ids) > 1:
            self._update_multi_select_badges()

    # ── Grid rebuild ─────────────────────────────────────────

    def _rebuild_grid(self) -> None:
        """FlowLayout 自身负责换行,这里只需触发一次重排。"""
        self._card_layout.invalidate()

    def _update_grid_columns(self) -> None:
        """计算「名义列数」,仅供方向键导航使用。

        实际换行由 FlowLayout 按每张卡片自身宽度完成,与该值无关。
        保留该方法以兼容响应式测试。
        """
        viewport = self._scroll.viewport()
        if viewport is None:
            return
        available = viewport.width() - _GRID_HMARGIN * 2
        new_columns = max(1, min(4, available // (_CARD_MIN_WIDTH + _GRID_H_SPACING)))
        if new_columns != self._grid_columns:
            self._grid_columns = new_columns

    # ── Refresh ──────────────────────────────────────────────

    def _refresh_card(self, room_id: str) -> None:
        card = self._cards.get(room_id)
        if card:
            card.refresh()
            self._update_card_timeline(room_id)
        if self._selected_room_id == room_id:
            room = self._manager.get_room(room_id)
            self._detail.show_room(room)
            self._update_timeline()
        self._update_statusbar()

    def _refresh_all(self) -> None:
        for room_id, card in self._cards.items():
            card.refresh()
            self._update_card_timeline(room_id)
        if self._selected_room_id:
            room = self._manager.get_room(self._selected_room_id)
            self._detail.show_room(room)
        self._update_statusbar()

    def _update_card_timeline(self, room_id: str) -> None:
        room = self._manager.get_room(room_id)
        card = self._cards.get(room_id)
        if room is None or card is None:
            return
        duration = self._manager.get_preview_duration(room_id)
        if duration <= 0 and room.controller:
            duration = float(getattr(room.controller, "total_sec", 0) or 0)
        if duration <= 0:
            duration = self._room_video_duration(room)
        position = self._manager.get_preview_position(room_id)
        card.set_timeline_data(position, duration)

    def _refresh(self) -> None:
        has_cards = bool(self._cards)
        self._empty_label.setVisible(not has_cards)
        if not has_cards:
            self._update_empty_label_geometry()
        self._scroll.setVisible(True)
        self._update_room_limit_label()
        self._update_statusbar()

    def _update_empty_label_geometry(self) -> None:
        viewport = self._scroll.viewport()
        if viewport is None:
            return
        margin = 40
        w = max(viewport.width() - margin * 2, 200)
        h = self._empty_label.sizeHint().height()
        x = (viewport.width() - w) // 2
        y = (viewport.height() - h) // 2
        self._empty_label.setGeometry(x, y, w, h)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._update_grid_columns()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # 使用防抖定时器，拖拽窗口时仅在停止 150ms 后更新网格列数
        self._grid_debounce.start()
        if self._empty_label.isVisible():
            self._update_empty_label_geometry()

    def _update_room_limit_label(self) -> None:
        count = self._manager.room_count()
        limit = self._manager.max_rooms()
        self._room_limit_label.setText(f"{count}/{limit}")

    def _update_statusbar(self) -> None:
        rooms = self._manager.list_rooms()
        self._statusbar.update_stats(
            total=len(rooms),
            connected=sum(1 for r in rooms if r.is_connected),
            recording=sum(1 for r in rooms if r.is_recording),
            previewing=sum(1 for r in rooms if r.preview_enabled and not r.preview_paused),
            errors=sum(1 for r in rooms if r.last_error),
        )

    def _refresh_theme(self) -> None:
        self._detail.refresh_theme()
        # 主题切换时若详情面板正在显示房间信息，则重建以刷新颜色
        if self._selected_room_id:
            self._detail.show_room(self._manager.get_room(self._selected_room_id))
        self._bottom_bar.refresh_theme()
        for card in self._cards.values():
            card.refresh_theme()
        self._clip_list.refresh_theme()
        self.update()

    @property
    def manager(self) -> MultiRoomManager:
        return self._manager

    # ── Keyboard navigation ────────────────────────────────────

    def keyPressEvent(self, event) -> None:
        key = event.key()
        mods = event.modifiers()
        if key == Qt.Key_Delete and self._selected_room_id:
            self._on_remove(self._selected_room_id)
        elif key == Qt.Key_Z and mods & Qt.ControlModifier:
            if self._undo.undo():
                desc = self._undo.redo_description()
                self._statusbar.show_message(f"已撤销: {desc}" if desc else "已撤销")
        elif key == Qt.Key_Y and mods & Qt.ControlModifier:
            if self._undo.redo():
                desc = self._undo.undo_description()
                self._statusbar.show_message(f"已重做: {desc}" if desc else "已重做")
        elif key == Qt.Key_Escape:
            # 取消多选，恢复单选当前房间
            if len(self._selected_room_ids) > 1:
                self._selected_room_ids = {self._selected_room_id} if self._selected_room_id else set()
                for rid, card in self._cards.items():
                    card.set_selected(rid in self._selected_room_ids)
                self._controls.set_sync_count(len(self._selected_room_ids))
        elif key in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down):
            self._navigate_cards(key)
        else:
            super().keyPressEvent(event)

    def _navigate_cards(self, key: int) -> None:
        """用方向键在卡片间导航。"""
        if not self._cards:
            return
        ordered = list(self._cards.keys())
        if self._selected_room_id is None:
            # 选中第一个
            self._on_room_selected(ordered[0])
            return
        try:
            idx = ordered.index(self._selected_room_id)
        except ValueError:
            idx = 0
        if key == Qt.Key_Right:
            idx = min(idx + 1, len(ordered) - 1)
        elif key == Qt.Key_Left:
            idx = max(idx - 1, 0)
        elif key == Qt.Key_Down:
            idx = min(idx + self._grid_columns, len(ordered) - 1)
        elif key == Qt.Key_Up:
            idx = max(idx - self._grid_columns, 0)
        if ordered[idx] != self._selected_room_id:
            self._on_room_selected(ordered[idx])
