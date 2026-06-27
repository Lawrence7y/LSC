"""预览容器共享组件。

从房间卡片提炼的 overlay 容器:承载占位/嵌入预览(MpvWidget),并在右上角
叠加徽章(REC/静音/同步)、右下角角标(全屏)、底部 hover 覆盖层(播放/静音)。

调用方通过 ``set_badge_widget`` / ``set_corner_widget`` / ``set_controls_widget``
注入 overlay 控件,容器负责几何定位与 hover 展开。底部控制条默认收起,鼠标
进入容器(或控制条)时展开,离开后收起。

被多房间卡片与直播录制页复用,统一预览交互。
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QFrame, QSizePolicy, QVBoxLayout, QWidget


class PreviewSurface(QFrame):
    """预览区域:承载占位/嵌入预览,并在角落叠加徽章与控制条。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._badge_widget: QWidget | None = None
        self._corner_widget: QWidget | None = None
        self._controls_widget: QWidget | None = None
        self._return_live_widget: QWidget | None = None
        self._overlay_expanded = False
        self.setObjectName("previewArea")
        self.setMouseTracking(True)
        # 承载占位/嵌入预览的布局
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)

    # ── content ─────────────────────────────────────────────────

    def set_content_widget(self, widget: QWidget) -> None:
        """设置主内容(占位或嵌入预览),撑满容器。"""
        self.clear_content()
        widget.setParent(self)
        widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._layout.addWidget(widget)

    def clear_content(self) -> None:
        """移除当前主内容(不销毁)。"""
        while self._layout.count():
            item = self._layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)

    @property
    def content_layout(self) -> QVBoxLayout:
        return self._layout

    # ── overlays ────────────────────────────────────────────────

    def set_badge_widget(self, widget: QWidget) -> None:
        self._badge_widget = widget
        widget.setParent(self)
        widget.show()
        self._update_badge_geometry()

    def set_corner_widget(self, widget: QWidget) -> None:
        self._corner_widget = widget
        widget.setParent(self)
        widget.show()
        self._update_overlay_geometry()

    def set_controls_widget(self, widget: QWidget) -> None:
        """注入底部 hover 覆盖层控制条。"""
        self._controls_widget = widget
        widget.setParent(self)
        # WA_StyledBackground 确保 QSS 的 rgba 背景生效，否则 QWidget 会使用
        # 默认调色板背景（浅色模式下接近白色），表现为"白色遮挡预览区"。
        widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # 初始隐藏，鼠标进入预览区时才显示
        widget.setVisible(False)
        widget.installEventFilter(self)
        self._update_overlay_geometry()

    def set_return_live_widget(self, widget: QWidget) -> None:
        """注入"回直播"浮窗按钮，定位在右上角徽章下方。"""
        self._return_live_widget = widget
        widget.setParent(self)
        widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        widget.setVisible(False)
        self._update_return_live_geometry()

    # ── geometry & hover ───────────────────────────────────────

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_badge_geometry()
        self._update_overlay_geometry()
        self._update_return_live_geometry()

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        self._set_overlay_expanded(True)

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self._set_overlay_expanded(False)

    def eventFilter(self, obj, event) -> bool:
        if obj is self._controls_widget and event.type() in (QEvent.Type.Enter, QEvent.Type.MouseMove):
            self._set_overlay_expanded(True)
        return super().eventFilter(obj, event)

    def _update_badge_geometry(self) -> None:
        if self._badge_widget is None:
            return
        self._badge_widget.adjustSize()
        w = self._badge_widget.width()
        h = self._badge_widget.height()
        margin = 6
        self._badge_widget.setGeometry(self.width() - w - margin, margin, w, h)

    def _set_overlay_expanded(self, expanded: bool) -> None:
        if self._overlay_expanded == expanded:
            return
        self._overlay_expanded = expanded
        # 用 setVisible 真正隐藏/显示控件，而非移出屏幕。
        # 旧实现把控件移到 y=height+margin 处，但 QFrame 默认不裁剪子控件，
        # 导致"隐藏"的控件仍渲染在预览区下方，表现为白色/灰色条遮挡。
        if self._controls_widget is not None:
            self._controls_widget.setVisible(expanded)
        self._update_overlay_geometry()

    def _update_overlay_geometry(self) -> None:
        margin = 8
        expanded_h = 34

        # 角标按钮始终固定在右下角，不受 hover 状态影响
        if self._corner_widget is not None:
            cw = self._corner_widget.width()
            ch = self._corner_widget.height()
            self._corner_widget.setGeometry(
                self.width() - cw - margin,
                self.height() - ch - margin,
                cw, ch,
            )
            self._corner_widget.raise_()

        # 控制条仅在展开时定位（已通过 setVisible 控制可见性）
        if self._controls_widget is not None and self._overlay_expanded:
            y = max(margin, self.height() - expanded_h - margin)
            corner_w = self._corner_widget.width() if self._corner_widget is not None else 0
            controls_w = max(120, self.width() - corner_w - margin * 3)
            self._controls_widget.setGeometry(margin, y, controls_w, expanded_h)
            self._controls_widget.raise_()

    def _update_return_live_geometry(self) -> None:
        """定位"回直播"浮窗按钮在右上角徽章下方。"""
        if self._return_live_widget is None:
            return
        self._return_live_widget.adjustSize()
        w = self._return_live_widget.width()
        h = self._return_live_widget.height()
        margin = 6
        # 徽章高度 + 间距，使浮窗位于徽章下方
        badge_bottom = 0
        if self._badge_widget is not None:
            badge_bottom = self._badge_widget.geometry().bottom() + margin
        y = max(margin, badge_bottom)
        self._return_live_widget.setGeometry(self.width() - w - margin, y, w, h)
        self._return_live_widget.raise_()
