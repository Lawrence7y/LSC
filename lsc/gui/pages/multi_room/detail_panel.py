"""Detail panel showing room information in the multi-room workbench."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QGridLayout, QLabel, QSizePolicy,
)
from PySide6.QtCore import Qt

from lsc.gui.components.widgets import EmptyState
from lsc.gui.multi_room.session import RoomSession
from lsc.utils.helpers import fmt_time


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
