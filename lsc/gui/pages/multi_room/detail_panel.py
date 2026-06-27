"""Detail panel showing room information in the multi-room workbench."""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGridLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from lsc.gui.components.widgets import EmptyState
from lsc.gui.multi_room.session import RoomSession
from lsc.utils.helpers import fmt_time


class DetailPanel(QWidget):
    """右侧房间详情面板（不包含 Card 外壳，由宿主页面包装）。

    优化：首次创建后缓存 widget 引用，后续只 setText() 更新值，
    避免每秒重建数十个 QWidget 导致的性能问题和视觉闪烁。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_room_id: str | None = None
        # 缓存的值 QLabel，按 items 顺序索引
        self._value_labels: list[QLabel] = []
        self._info_grid: QWidget | None = None
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
        room_changed = new_id != self._current_room_id
        self._current_room_id = new_id

        if room is None:
            self._show_empty()
            return

        # 房间切换时重建结构，同一房间只更新值
        if room_changed or self._info_grid is None:
            self._rebuild_structure(room)
        else:
            self._update_values(room)

    def _show_empty(self) -> None:
        """显示空状态，移除信息网格。"""
        if self._info_grid is not None:
            self._info_grid.setParent(None)
            self._info_grid.deleteLater()
            self._info_grid = None
            self._value_labels = []
        if self._empty is None:
            self._empty = EmptyState("选择房间查看详情", "点击左侧房间卡片查看详细信息")
            self._body_layout.insertWidget(0, self._empty)

    def _rebuild_structure(self, room: RoomSession) -> None:
        """房间切换时重建信息网格结构。"""
        # 移除旧内容
        if self._empty is not None:
            self._empty.setParent(None)
            self._empty.deleteLater()
            self._empty = None
        if self._info_grid is not None:
            self._info_grid.setParent(None)
            self._info_grid.deleteLater()

        self._value_labels = []
        self._info_grid = QWidget()
        gl = QGridLayout(self._info_grid)
        gl.setContentsMargins(0, 0, 0, 0)
        gl.setHorizontalSpacing(18)
        gl.setVerticalSpacing(14)

        labels = [
            "分辨率", "帧率", "编码", "编码参数",
            "文件大小", "输出路径", "分析结果", "最近错误",
        ]

        for i, label_text in enumerate(labels):
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
            val.setWordWrap(True)
            val.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            il.addWidget(val)
            gl.addWidget(item, row, col)
            self._value_labels.append(val)

        self._body_layout.insertWidget(0, self._info_grid)
        self._update_values(room)

    def _update_values(self, room: RoomSession) -> None:
        """同一房间内只更新值，不重建 widget。"""
        if not self._value_labels:
            return

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
            elapsed = (datetime.now() - room.record_started_at).total_seconds()
            analysis_text = f"{analysis_text} · {fmt_time(elapsed)}"
        error_text = room.friendly_error if room.last_error else "--"

        values = [
            room.stream_resolution or "--",
            room.stream_fps or "--",
            codec or "--",
            param or "--",
            size_text,
            output_path,
            analysis_text or "--",
            error_text,
        ]

        for label, value in zip(self._value_labels, values):
            if label.text() != str(value):
                label.setText(str(value))
