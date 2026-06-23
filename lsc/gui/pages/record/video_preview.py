"""Video preview components for the record page."""
from __future__ import annotations

import os

from PySide6.QtCore import (
    QEasingCurve,
    QPointF,
    QPropertyAnimation,
    QRect,
    QSize,
    Qt,
    Signal,
    Property,
)
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QWidget,
)

from lsc.gui.components.mpv_widget import MpvWidget
from lsc.gui.components.preview_surface import PreviewSurface
from lsc.gui.theme import get_theme


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
