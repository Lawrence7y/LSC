"""切片列表组件 — 支持多段标记与管理。

允许用户将当前入/出点选区添加为片段，查看所有片段列表，
单独删除片段，或一次性导出全部片段。
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QEvent, QSettings, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from lsc.gui.components.widgets import EmptyState
from lsc.gui.theme import connect_theme_changed
from lsc.utils.helpers import fmt_time


@dataclass(frozen=True)
class ClipSegment:
    """单个切片片段。"""

    start: float
    end: float
    label: str = ""
    thumbnail_path: str = ""

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


class ClipItem(QFrame):
    """切片列表中的单行片段卡片。"""

    remove_clicked = Signal(int)  # 发射自身索引
    clicked = Signal()  # 点击卡片（除删除按钮外）

    _THUMB_W = 64
    _THUMB_H = 36

    def __init__(self, index: int, segment: ClipSegment, parent=None):
        super().__init__(parent)
        self._index = index
        self._segment = segment
        self._thumbnail_pixmap: QPixmap | None = None
        self._hovering = False
        self._build()
        self._apply_style()
        self.setMouseTracking(True)
        self.setCursor(QCursor(Qt.PointingHandCursor))

    def _build(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 8, 6)
        layout.setSpacing(10)

        # ── 缩略图区域（stacked: 底层图片/占位符 + 顶层悬浮播放按钮） ──
        self._thumb_container = QWidget()
        self._thumb_container.setFixedSize(self._THUMB_W, self._THUMB_H)
        self._thumb_container.setObjectName("clipThumbContainer")
        thumb_stack = QStackedLayout(self._thumb_container)
        thumb_stack.setStackingMode(QStackedLayout.StackAll)

        # 底层：缩略图 or 占位符
        self._thumb_lbl = QLabel()
        self._thumb_lbl.setFixedSize(self._THUMB_W, self._THUMB_H)
        self._thumb_lbl.setAlignment(Qt.AlignCenter)
        self._thumb_lbl.setObjectName("clipThumbPlaceholder")
        self._set_placeholder()
        thumb_stack.addWidget(self._thumb_lbl)

        # 顶层：悬浮播放按钮覆盖层
        self._play_overlay = QLabel("▶")
        self._play_overlay.setFixedSize(self._THUMB_W, self._THUMB_H)
        self._play_overlay.setAlignment(Qt.AlignCenter)
        self._play_overlay.setObjectName("clipPlayOverlay")
        self._play_overlay.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._play_overlay.setVisible(False)
        thumb_stack.addWidget(self._play_overlay)

        layout.addWidget(self._thumb_container)

        # 索引徽章
        self._index_lbl = QLabel(f"#{self._index + 1}")
        self._index_lbl.setFixedWidth(28)
        self._index_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._index_lbl)

        # 时间范围
        time_text = f"{fmt_time(self._segment.start)} → {fmt_time(self._segment.end)}"
        self._time_lbl = QLabel(time_text)
        self._time_lbl.setObjectName("clipTimeLabel")
        layout.addWidget(self._time_lbl, 1)

        # 时长
        dur_text = f"{fmt_time(self._segment.duration)}"
        self._dur_lbl = QLabel(dur_text)
        self._dur_lbl.setFixedWidth(70)
        self._dur_lbl.setAlignment(Qt.AlignCenter)
        self._dur_lbl.setObjectName("clipTimeLabel")
        layout.addWidget(self._dur_lbl)

        # 删除按钮
        del_btn = QPushButton("×")
        del_btn.setFixedSize(22, 22)
        del_btn.setCursor(Qt.PointingHandCursor)
        del_btn.setToolTip("删除此片段")
        del_btn.clicked.connect(lambda: self.remove_clicked.emit(self._index))
        layout.addWidget(del_btn)

    def _set_placeholder(self) -> None:
        """显示灰色占位符 + 🎬 图标。"""
        pix = QPixmap(self._THUMB_W, self._THUMB_H)
        pix.fill(QColor(60, 60, 60))
        painter = QPainter(pix)
        painter.setPen(QColor(160, 160, 160))
        font = painter.font()
        font.setPixelSize(18)
        painter.setFont(font)
        painter.drawText(pix.rect(), Qt.AlignCenter, "🎬")
        painter.end()
        self._thumb_lbl.setPixmap(pix)

    def set_thumbnail(self, pixmap: QPixmap | None) -> None:
        """设置缩略图；传 None 恢复占位符。"""
        if pixmap is None or pixmap.isNull():
            self._thumbnail_pixmap = None
            self._set_placeholder()
        else:
            self._thumbnail_pixmap = pixmap.scaled(
                self._THUMB_W, self._THUMB_H,
                Qt.KeepAspectRatio, Qt.SmoothTransformation,
            )
            self._thumb_lbl.setPixmap(self._thumbnail_pixmap)

    def enterEvent(self, event: QEvent) -> None:  # noqa: N802
        self._hovering = True
        self._play_overlay.setVisible(True)
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802
        self._hovering = False
        self._play_overlay.setVisible(False)
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def _apply_style(self) -> None:
        # 样式已迁移到 theme.py generate_stylesheet，使用 objectName 驱动
        self.setObjectName("clipItem")
        self._index_lbl.setObjectName("clipIdx")

    def update_index(self, index: int) -> None:
        """更新片段索引（删除其他片段后重新编号）。"""
        self._index = index
        self._index_lbl.setText(f"#{index + 1}")


class ClipListWidget(QWidget):
    """切片列表组件。

    管理多个 :class:`ClipSegment`，提供添加/删除/清空/全部导出功能。

    Signals:
        segments_changed: 片段列表发生变化时发射（参数为当前片段数）。
        export_all_clicked: 用户点击"全部导出"时发射。
    """

    segments_changed = Signal(int)
    export_all_clicked = Signal()
    add_current_clicked = Signal()
    clip_preview_requested = Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._segments: list[ClipSegment] = []
        self._items: list[ClipItem] = []
        self._current_room_url: str | None = None
        # 可选的撤销栈：设置后 add/remove/clear 会作为可撤销命令入栈，
        # 未设置时直接执行（保持组件可独立使用）。
        self._undo_stack = None
        self._build()
        self._update_state()
        connect_theme_changed(self.refresh_theme)

    def set_undo_stack(self, undo_stack) -> None:
        """注入撤销栈，使片段增删改可撤销/重做。传 None 关闭撤销。"""
        self._undo_stack = undo_stack

    def set_current_room_url(self, room_url: str | None) -> None:
        """设置当前直播间 URL，并自动加载其独立的片段列表。"""
        self._current_room_url = room_url
        self.load_segments()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        # ── Header ──
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        title = QLabel("切片列表")
        title.setObjectName("section_title")
        header.addWidget(title)
        self._count_badge = QLabel("0")
        self._count_badge.setFixedHeight(18)
        self._count_badge.setAlignment(Qt.AlignCenter)
        self._count_badge.setObjectName("clipCountBadge")
        header.addWidget(self._count_badge)
        header.addStretch()
        self._clear_btn = QPushButton("清空")
        self._clear_btn.setObjectName("btnSecondary")
        self._clear_btn.setCursor(Qt.PointingHandCursor)
        self._clear_btn.setToolTip("清空所有片段")
        self._clear_btn.clicked.connect(self._on_clear)
        header.addWidget(self._clear_btn)
        layout.addLayout(header)

        # ── Scrollable list ──
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setFixedHeight(120)

        self._list_container = QWidget()
        self._list_layout = QVBoxLayout(self._list_container)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(4)
        self._list_layout.addStretch()
        self._scroll.setWidget(self._list_container)
        layout.addWidget(self._scroll)

        # ── Empty state ──
        self._empty_state = EmptyState(
            "暂无片段", "设置入/出点后点击「添加选区」"
        )
        self._list_layout.insertWidget(0, self._empty_state)

        # ── Footer actions ──
        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(8)
        self._add_btn = QPushButton("+ 添加选区")
        self._add_btn.setObjectName("btnPrimary")
        self._add_btn.setToolTip("将当前入/出点选区添加为片段")
        self._add_btn.clicked.connect(self._on_add_current)
        footer.addWidget(self._add_btn)

        self._export_all_btn = QPushButton("全部导出")
        self._export_all_btn.setObjectName("btnSecondary")
        self._export_all_btn.setToolTip("导出列表中的所有片段")
        self._export_all_btn.clicked.connect(self.export_all_clicked.emit)
        footer.addWidget(self._export_all_btn)
        layout.addLayout(footer)

    # ── Public API ───────────────────────────────────────────

    def add_segment(self, start: float, end: float, label: str = "") -> int:
        """添加一个片段，返回新片段的索引。

        自动校正 start/end 顺序。若 start == end 则忽略。
        若已注入撤销栈，添加作为可撤销命令入栈。
        """
        if start == end:
            return -1
        s, e = (start, end) if start < end else (end, start)
        seg = ClipSegment(start=s, end=e, label=label)
        if self._undo_stack is not None:
            from lsc.gui.undo import Command

            inserted_index = len(self._segments)

            def _redo() -> None:
                self._segments.append(seg)
                self._rebuild_items()
                self._save_segments()
                self.segments_changed.emit(len(self._segments))

            def _undo() -> None:
                # seg 可能不在末尾（期间有其他添加），按值定位删除
                try:
                    self._segments.remove(seg)
                except ValueError:
                    pass
                self._rebuild_items()
                self._save_segments()
                self.segments_changed.emit(len(self._segments))

            self._undo_stack.execute(
                Command(description="添加切片片段", undo=_undo, redo=_redo)
            )
            return inserted_index
        self._segments.append(seg)
        self._rebuild_items()
        self._save_segments()
        self.segments_changed.emit(len(self._segments))
        return len(self._segments) - 1

    def remove_segment(self, index: int) -> None:
        """删除指定索引的片段。若已注入撤销栈，删除可撤销。"""
        if not (0 <= index < len(self._segments)):
            return
        seg = self._segments[index]
        if self._undo_stack is not None:
            from lsc.gui.undo import Command

            def _redo() -> None:
                # seg 仍可能因并发增删而位移，按值定位删除
                try:
                    self._segments.remove(seg)
                except ValueError:
                    pass
                self._rebuild_items()
                self._save_segments()
                self.segments_changed.emit(len(self._segments))

            def _undo() -> None:
                # 恢复到原位置（若越界则追加到末尾）
                if index <= len(self._segments):
                    self._segments.insert(index, seg)
                else:
                    self._segments.append(seg)
                self._rebuild_items()
                self._save_segments()
                self.segments_changed.emit(len(self._segments))

            self._undo_stack.execute(
                Command(description="删除切片片段", undo=_undo, redo=_redo)
            )
            return
        self._segments.pop(index)
        self._rebuild_items()
        self._save_segments()
        self.segments_changed.emit(len(self._segments))

    def clear(self) -> None:
        """清空所有片段。若已注入撤销栈，清空可撤销。"""
        if not self._segments:
            return
        snapshot = list(self._segments)
        if self._undo_stack is not None:
            from lsc.gui.undo import Command

            def _redo() -> None:
                self._segments.clear()
                self._rebuild_items()
                self._save_segments()
                self.segments_changed.emit(0)

            def _undo() -> None:
                self._segments = list(snapshot)
                self._rebuild_items()
                self._save_segments()
                self.segments_changed.emit(len(self._segments))

            self._undo_stack.execute(
                Command(description="清空切片列表", undo=_undo, redo=_redo)
            )
            return
        self._segments.clear()
        self._rebuild_items()
        self._save_segments()
        self.segments_changed.emit(0)

    def segments(self) -> list[ClipSegment]:
        """返回当前所有片段的副本。"""
        return list(self._segments)

    def count(self) -> int:
        return len(self._segments)

    def set_segment_thumbnail(self, index: int, pixmap) -> None:
        """设置指定片段的缩略图。"""
        if 0 <= index < len(self._items):
            self._items[index].set_thumbnail(pixmap)

    # ── Internal ─────────────────────────────────────────────

    def _on_add_current(self) -> None:
        """添加当前选区 — 发射 add_current_clicked 信号，由宿主页面响应。"""
        self.add_current_clicked.emit()

    def _on_clear(self) -> None:
        if self._segments:
            self.clear()

    def _rebuild_items(self) -> None:
        """重建列表 UI。"""
        # 移除旧 item
        for item in self._items:
            self._list_layout.removeWidget(item)
            item.deleteLater()
        self._items.clear()

        # 显示/隐藏空状态
        self._empty_state.setVisible(not self._segments)

        # 创建新 item（倒序插入，最新在顶部）
        for i, seg in enumerate(self._segments):
            item = ClipItem(i, seg, self._list_container)
            item.remove_clicked.connect(self._on_item_remove)
            item.clicked.connect(lambda idx=i: self._on_item_clicked(idx))
            # 插入到空状态标签之后、stretch 之前
            self._list_layout.insertWidget(self._list_layout.count() - 1, item)
            self._items.append(item)

        self._update_state()

    def _on_item_remove(self, index: int) -> None:
        self.remove_segment(index)

    def _on_item_clicked(self, index: int) -> None:
        seg = self._segments[index]
        self.clip_preview_requested.emit(seg.start, seg.end)

    def _update_state(self) -> None:
        """更新计数徽章和按钮启用状态。"""
        n = len(self._segments)
        self._count_badge.setText(str(n))
        self._clear_btn.setEnabled(n > 0)
        self._export_all_btn.setEnabled(n > 0)

    def set_add_enabled(self, enabled: bool) -> None:
        """控制"添加选区"按钮的启用状态。"""
        self._add_btn.setEnabled(enabled)

    # ── Persistence ────────────────────────────────────────

    def _save_segments(self) -> None:
        """Persist clip segments to QSettings."""
        import hashlib
        import json
        if self._current_room_url:
            url_hash = hashlib.md5(self._current_room_url.encode("utf-8")).hexdigest()
            key = f"clipList/{url_hash}/segments"
        else:
            key = "clipList/default/segments"
        data = [{"start": s.start, "end": s.end, "label": s.label, "thumbnail_path": s.thumbnail_path} for s in self._segments]
        QSettings("LSC", "LiveStreamClipper").setValue(key, json.dumps(data))

    def load_segments(self) -> None:
        """Restore clip segments from QSettings."""
        import hashlib
        import json
        self._segments.clear()
        if self._current_room_url:
            url_hash = hashlib.md5(self._current_room_url.encode("utf-8")).hexdigest()
            key = f"clipList/{url_hash}/segments"
        else:
            key = "clipList/default/segments"
        raw = QSettings("LSC", "LiveStreamClipper").value(key, "")
        if raw:
            try:
                data = json.loads(str(raw))
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict) and "start" in item and "end" in item:
                            self._segments.append(ClipSegment(
                                start=float(item["start"]),
                                end=float(item["end"]),
                                label=str(item.get("label", "")),
                                thumbnail_path=str(item.get("thumbnail_path", "")),
                            ))
            except (json.JSONDecodeError, TypeError):
                pass
        self._rebuild_items()

    def refresh_theme(self) -> None:
        # 全局样式表由 theme.py 统一管理，只需触发 repaint
        for item in self._items:
            item._apply_style()
        self.update()
