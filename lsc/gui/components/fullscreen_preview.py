"""沉浸式全屏预览播放器组件。

从多房间工作台提炼的共享全屏逻辑:把一个视频 widget(通常是 ``MpvWidget``)
reparent 进顶层全屏窗口,叠加自动隐藏的控制条,Esc/关闭时 reparent 回原宿主。

两种控制条模式:
- ``builtin``:组件自建极简播放条(进度 + 播放/静音/最小化/退出),供
  多房间页等"只需沉浸观看"的场景使用。
- ``external``:reparent 调用方传入的控制栏(如录制页的 ``SharedControlBar``),
  保留编辑工作流(入/出点、导出)在全屏下的可用性,仍自动隐藏。

避免创建第二个 libmpv 实例 / 流连接 —— 复用原 widget,退出时 reparent 回去。
"""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject, QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPen, QPolygonF, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from lsc.utils.helpers import fmt_time


class _FullscreenIconButton(QPushButton):
    """字体无关的全屏播放器图标按钮,避免系统缺字导致图标乱码。"""

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
    """捕获全屏窗口和控件上的鼠标活动,用于唤醒自动隐藏的底栏。"""

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


class FullscreenPreview(QObject):
    """沉浸式全屏预览播放器。

    用法:
        fp = FullscreenPreview(parent,
            get_widget=lambda: room.preview_widget,
            get_controls=lambda: None,           # None = 用内置极简条
            get_position=lambda: manager.get_preview_position(rid),
            get_duration=lambda: manager.get_preview_duration(rid),
            is_paused=lambda: room.preview_paused,
            is_muted=lambda: room.preview_muted,
            on_toggle_play=lambda: toggle_play(),
            on_toggle_mute=lambda: toggle_mute(),
            on_seek=lambda v: seek(v),
            on_restore=lambda w, c: card.set_preview_widget(w),
            title="全屏预览 - 主播",
        )
        fp.enter()

    ``get_controls`` 返回非 None 时使用 external 模式(reparent 调用方控制栏),
    返回 None 时使用 builtin 极简条。
    """

    def __init__(
        self,
        parent: QWidget,
        *,
        get_widget: Callable[[], QWidget | None],
        get_controls: Callable[[], QWidget | None],
        get_position: Callable[[], float],
        get_duration: Callable[[], float],
        is_paused: Callable[[], bool],
        is_muted: Callable[[], bool],
        on_toggle_play: Callable[[], None],
        on_toggle_mute: Callable[[], bool],
        on_seek: Callable[[int], None],
        on_restore: Callable[[QWidget | None, QWidget | None], None],
        title: str = "全屏预览",
        auto_hide: bool = True,
    ) -> None:
        super().__init__(parent)
        self._parent = parent
        self._get_widget = get_widget
        self._get_controls = get_controls
        self._get_position = get_position
        self._get_duration = get_duration
        self._is_paused = is_paused
        self._is_muted = is_muted
        self._on_toggle_play = on_toggle_play
        self._on_toggle_mute = on_toggle_mute
        self._on_seek = on_seek
        self._on_restore = on_restore
        self._title = title
        self._auto_hide = auto_hide
        self._win: QWidget | None = None
        self._surface: QWidget | None = None
        self._builtin_controls: QWidget | None = None
        self._external_controls: QWidget | None = None
        self._progress: QSlider | None = None
        self._play_btn: _FullscreenIconButton | None = None
        self._mute_btn: QCheckBox | None = None
        self._time_label: QLabel | None = None
        self._controls_height = 74
        self._timer: QTimer | None = None
        self._auto_hide_timer: QTimer | None = None
        self._activity_filter: _FullscreenActivityFilter | None = None
        self._esc: QShortcut | None = None

    # ── public ──────────────────────────────────────────────────

    def is_active(self) -> bool:
        return self._win is not None

    def window(self) -> QWidget | None:
        """返回全屏顶层窗口(供测试与外部查询),未进入时为 None。"""
        return self._win

    def enter(self) -> None:
        """进入全屏。已在全屏则关闭(切换语义)。"""
        if self._win is not None:
            self._win.close()
            return
        widget = self._get_widget()
        if widget is None:
            return

        # external 控制栏:调用方提供则一并 reparent,否则用内置极简条
        ext_controls = self._get_controls()
        # 调用方应在调用 enter() 前已 detach widget/controls(从原布局移除)。
        # 这里只负责把它们 reparent 进全屏窗口。
        if ext_controls is not None:
            ext_controls.setParent(None)

        win = QWidget(self._parent, Qt.Window)
        win.setWindowTitle(self._title)
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

        self._surface = surface
        self._external_controls = ext_controls

        if ext_controls is not None:
            # external 模式:reparent 调用方控制栏到底部,设为自动隐藏
            self._builtin_controls = None
            ext_controls.setParent(surface)
            ext_controls.setMouseTracking(True)
            self._controls_height = ext_controls.sizeHint().height() or 74
            surface_layout.addWidget(ext_controls)
            controls = ext_controls
        else:
            # builtin 模式:自建极简播放条
            controls = self._build_builtin_controls(surface)
            self._builtin_controls = controls
            surface_layout.addWidget(controls)

        self._place_controls(surface, controls)

        def _surface_resize_event(event) -> None:
            QWidget.resizeEvent(surface, event)
            self._place_controls(surface, controls)

        surface.resizeEvent = _surface_resize_event

        # Esc 退出
        self._esc = QShortcut(QKeySequence(Qt.Key_Escape), win)
        self._esc.activated.connect(self._close)

        def _key_press_event(event) -> None:
            if event.key() == Qt.Key_Escape:
                event.accept()
                self._close()
                return
            QWidget.keyPressEvent(win, event)

        win.keyPressEvent = _key_press_event

        # 自动隐藏控制条 + 鼠标唤醒
        if self._auto_hide:
            self._auto_hide_timer = QTimer(win)
            self._auto_hide_timer.setSingleShot(True)
            self._auto_hide_timer.setInterval(2400)
            self._auto_hide_timer.timeout.connect(lambda: controls.setVisible(False))
            self._activity_filter = _FullscreenActivityFilter(
                lambda: self._show_controls(controls), win
            )
            watched = [win, surface, widget, controls]
            if ext_controls is None:
                watched += [self._progress, self._play_btn, self._mute_btn]
            for w in watched:
                if w is not None:
                    w.installEventFilter(self._activity_filter)

        # builtin 模式下需要 timer 同步进度/按钮态;external 模式由调用方控制栏自驱
        if ext_controls is None:
            self._timer = QTimer(win)
            self._timer.setInterval(500)
            self._timer.timeout.connect(self._sync_builtin)
            self._timer.start()

        win.closeEvent = lambda e: (self._restore(), QWidget.closeEvent(win, e))

        self._win = win
        if ext_controls is None:
            self._sync_builtin()
        self._show_controls(controls)
        win.showFullScreen()

    def _close(self) -> None:
        if self._win is not None:
            self._win.close()

    # ── builtin controls ────────────────────────────────────────

    def _build_builtin_controls(self, surface: QWidget) -> QWidget:
        controls = QWidget(surface)
        controls.setObjectName("fullscreenPlayerControls")
        controls.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        controls.setMouseTracking(True)
        controls.setFixedHeight(self._controls_height)
        cl = QVBoxLayout(controls)
        cl.setContentsMargins(14, 0, 14, 9)
        cl.setSpacing(4)

        progress = QSlider(Qt.Orientation.Horizontal, controls)
        progress.setObjectName("fullscreenProgressSlider")
        progress.setMouseTracking(True)
        progress.setMaximumHeight(12)
        progress.setRange(0, 0)
        cl.addWidget(progress)
        self._progress = progress

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        play_btn = _FullscreenIconButton("pause", controls)
        play_btn.setObjectName("fullscreenPlayButton")
        play_btn.setFixedSize(40, 34)
        play_btn.setToolTip("播放/暂停")
        row.addWidget(play_btn)
        self._play_btn = play_btn

        time_label = QLabel("00:00 / 00:00", controls)
        time_label.setObjectName("fullscreenTimeLabel")
        row.addWidget(time_label)
        row.addStretch(1)
        self._time_label = time_label

        mute_btn = QCheckBox("静音", controls)
        mute_btn.setObjectName("fullscreenMuteButton")
        row.addWidget(mute_btn)
        self._mute_btn = mute_btn

        min_btn = _FullscreenIconButton("minimize", controls)
        min_btn.setObjectName("fullscreenMinimizeButton")
        min_btn.setFixedSize(40, 34)
        min_btn.setToolTip("最小化")
        min_btn.clicked.connect(self._minimize)
        row.addWidget(min_btn)

        exit_btn = _FullscreenIconButton("exit_fullscreen", controls)
        exit_btn.setObjectName("fullscreenExitButton")
        exit_btn.setFixedSize(40, 34)
        exit_btn.setToolTip("退出全屏")
        exit_btn.clicked.connect(self._close)
        row.addWidget(exit_btn)

        cl.addLayout(row)

        syncing = {"active": False}

        def _seek(value: int) -> None:
            if syncing["active"]:
                return
            self._on_seek(int(value))

        play_btn.clicked.connect(lambda: self._on_toggle_play())
        mute_btn.toggled.connect(lambda v: self._on_toggle_mute())
        progress.valueChanged.connect(_seek)
        self._syncing = syncing
        return controls

    def _sync_builtin(self) -> None:
        if self._progress is None:
            return
        duration = max(0.0, float(self._get_duration() or 0.0))
        position = max(0.0, float(self._get_position() or 0.0))
        if duration > 0:
            position = min(position, duration)
        self._syncing["active"] = True
        self._progress.setRange(0, max(0, int(duration)))
        self._progress.setValue(max(0, int(position)))
        self._syncing["active"] = False
        if self._time_label is not None:
            self._time_label.setText(f"{fmt_time(position)} / {fmt_time(duration)}")
        if self._play_btn is not None:
            self._play_btn.set_icon_kind("play" if self._is_paused() else "pause")
        if self._mute_btn is not None:
            self._mute_btn.blockSignals(True)
            self._mute_btn.setChecked(bool(self._is_muted()))
            self._mute_btn.blockSignals(False)

    def _show_controls(self, controls: QWidget) -> None:
        controls.setVisible(True)
        if self._surface is not None:
            self._place_controls(self._surface, controls)
        if self._auto_hide and self._auto_hide_timer is not None:
            self._auto_hide_timer.start()

    def _place_controls(self, surface: QWidget, controls: QWidget) -> None:
        controls.setGeometry(
            0,
            max(0, surface.height() - self._controls_height),
            max(1, surface.width()),
            self._controls_height,
        )
        controls.raise_()

    def _minimize(self) -> None:
        if self._win is not None:
            self._win.showMinimized()

    # ── teardown ────────────────────────────────────────────────

    def _restore(self) -> None:
        """关闭窗口时把 widget(与 external 控制栏)reparent 回原宿主。"""
        if self._win is None:
            return
        if self._auto_hide_timer is not None:
            self._auto_hide_timer.stop()
            self._auto_hide_timer = None
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        self._activity_filter = None
        self._esc = None

        widget = self._get_widget()
        ext = self._external_controls
        self._external_controls = None
        self._builtin_controls = None
        self._progress = None
        self._play_btn = None
        self._mute_btn = None
        self._time_label = None
        self._surface = None
        self._win = None
        # 调用方负责把 widget / controls 放回原容器
        self._on_restore(widget, ext)
