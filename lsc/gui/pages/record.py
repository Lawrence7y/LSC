"""
Record page - 1:1 replica of ui-design-prototype.html
"""

import math
import os
import time as _time
from datetime import datetime, timezone

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen, QPolygon
from PySide6.QtWidgets import (
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from lsc.utils.helpers import fmt_time as _fmt_time

from ..components.mpv_widget import MpvWidget
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


class VideoPreview(QWidget):
    """Video preview / recording indicator.

    Uses mpv (libmpv-2) instead of QMediaPlayer.  mpv can natively
    play growing (fragmented-MP4) files without stalling at the old EOF,
    which fixes the black-screen issue completely.

    When recording: mpv plays the growing file in live mode (follows the
    write head).  When not recording: mpv plays completed files normally.
    A paintEvent overlay shows a pulsing dot + "正在录制" when recording.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._recording = False
        self._time = "00:00:00"
        self._connected = False
        self.setMinimumSize(400, 225)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        # mpv-based video playback (supports growing files)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._mpv_widget = MpvWidget()
        self._mpv_widget.setStyleSheet("border-radius:14px;")
        self._mpv_widget.hide()
        self._layout.addWidget(self._mpv_widget)

        # Animation timer for pulsing REC dot during recording
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(50)  # ~20 fps for smooth pulse
        self._anim_timer.timeout.connect(self.update)

    def set_state(self, recording=False, connected=False, time="00:00:00"):
        was_recording = self._recording
        self._recording = recording
        self._connected = connected
        self._time = time
        if recording and not was_recording:
            # Starting recording — start animation overlay
            self._anim_timer.start()
        elif not recording and was_recording:
            # Stopped recording — stop animation overlay
            self._anim_timer.stop()
        self.update()

    def play_video(self, path):
        """Play a completed (non-growing) recording."""
        if not os.path.isfile(path):
            return
        self.stop_video()
        self._mpv_widget.show()
        self._mpv_widget.play_video(path, live=False)
        self.update()

    def play_live(self, path):
        """Play a growing file during recording (live mode)."""
        self._mpv_widget.show()
        self._mpv_widget.play_live(path)
        self.update()

    def stop_video(self):
        """Stop video playback."""
        self._mpv_widget.stop_video()
        self.update()

    def is_playing(self):
        return self._mpv_widget.is_playing()

    def toggle_play_pause(self):
        self._mpv_widget.toggle_play_pause()

    def seek_to(self, sec):
        self._mpv_widget.seek_to(sec)

    def position_sec(self):
        return self._mpv_widget.position_sec()

    def duration_sec(self):
        return self._mpv_widget.duration_sec()

    def cleanup(self):
        """Clean up mpv resources."""
        self._mpv_widget.cleanup()

    def paintEvent(self, e):
        mpv_visible = self._mpv_widget.isVisible()

        # When mpv is showing video (recording or playback), avoid painting
        # an opaque background over it — let the video show through.
        if mpv_visible and not self._recording:
            return  # Playback mode — no overlay

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = get_theme()
        r = self.rect()

        if self._recording and mpv_visible:
            # Live preview is active — draw only a small semi-transparent
            # REC badge in the top-left corner so the video stays visible.
            p.setPen(Qt.NoPen)
            t = _time.monotonic()
            pulse = 0.5 + 0.5 * math.sin(t * 4.0)

            # Badge background
            badge_rect = QRect(12, 12, 160, 36)
            p.setBrush(QColor(0, 0, 0, 160))
            p.drawRoundedRect(badge_rect, 8, 8)

            # Pulsing red dot
            dot_x, dot_y = badge_rect.left() + 18, badge_rect.center().y()
            p.setBrush(QColor(220, 30, 30, int(100 + 120 * pulse)))
            p.drawEllipse(QPointF(dot_x, dot_y), 6, 6)

            # Time text
            p.setPen(QColor(255, 255, 255, 200))
            p.setFont(QFont("JetBrains Mono", 11))
            text_rect = QRect(badge_rect.left() + 32, badge_rect.top(),
                              badge_rect.width() - 40, badge_rect.height())
            p.drawText(text_rect, Qt.AlignVCenter, f"录制中 {self._time}")
        elif self._recording:
            # No live preview yet (mpv not visible) — full overlay
            p.setBrush(QColor("#000000"))
            p.setPen(QColor(c.border_subtle))
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 14, 14)

            cx, cy = r.center().x(), r.center().y()
            t = _time.monotonic()
            pulse = 0.5 + 0.5 * math.sin(t * 4.0)
            radius = 8 + 4 * pulse
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(220, 30, 30, int(100 + 120 * pulse)))
            p.drawEllipse(QPointF(cx, cy - 30), radius, radius)
            p.setPen(QColor(255, 255, 255, 180))
            p.setFont(QFont("Inter", 14))
            p.drawText(r.adjusted(0, 10, 0, 0), Qt.AlignCenter, "正在录制")
            p.setPen(QColor(255, 255, 255, 120))
            p.setFont(QFont("JetBrains Mono", 12))
            p.drawText(r.adjusted(0, 40, 0, 0), Qt.AlignCenter, self._time)
        else:
            # Idle state — no video, no recording
            p.setBrush(QColor("#000000"))
            p.setPen(QColor(c.border_subtle))
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 14, 14)
            p.setPen(QColor(c.text_tertiary))
            p.setFont(QFont("Inter", 13))
            p.drawText(r, Qt.AlignCenter, "直播画面预览区域")

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


TIMELINE_HEIGHT = 56
TIMELINE_TRACK_THICKNESS = 2
TIMELINE_SELECTION_HEIGHT = 12
TIMELINE_SELECTION_BORDER_WIDTH = 1
TIMELINE_CURSOR_LINE_WIDTH = 2
TIMELINE_CURSOR_DOT_SIZE = 8
TIMELINE_LABEL_HEIGHT = 18
TIMELINE_MARKER_WIDTH = 4
TIMELINE_MARKER_HEIGHT = 8
TIMELINE_SIDE_PADDING = 8
TIMELINE_HANDLE_HIT_RADIUS = 10


class InlineTimeline(QWidget):
    marker_clicked = Signal(int)
    position_changed = Signal(float)

    CURSOR_WHITE, CURSOR_GREEN, CURSOR_RED = 0, 1, 2

    def __init__(self, parent=None):
        super().__init__(parent)
        self._duration = 0
        self._position = 0
        self._start = None
        self._end = None
        self._markers = []
        self._offset = 0
        self._pixels_per_sec = 6
        self._drag_mode = None  # None, 'position', 'start', 'end', 'pan'
        self._drag_start_x = 0
        self._cursor_mode = self.CURSOR_WHITE
        self._drag_preview_time = None
        self._live_clock_text = ""
        self.setFixedHeight(TIMELINE_HEIGHT)
        self.setCursor(Qt.OpenHandCursor)
        self.setMouseTracking(True)

    def current_palette(self) -> dict[str, str]:
        c = get_theme()
        return {
            "container_fill": c.bg_tertiary,
            "container_border": c.border_subtle,
            "track": c.border_default,
            "progress": c.accent_primary,
            "selection_fill": c.accent_primary_dim,
            "selection_border": c.accent_primary,
            "marker": c.accent_primary,
            "label_fill": c.bg_elevated,
            "label_border": c.border_default,
            "label_text": c.text_primary,
        }

    def track_geometry(self) -> tuple[int, int, int]:
        r = self.rect()
        return (
            r.x() + TIMELINE_SIDE_PADDING,
            r.right() - TIMELINE_SIDE_PADDING,
            r.center().y(),
        )

    def selection_rect(self) -> QRect:
        track_left, track_right, mid_y = self.track_geometry()
        if self._start is None or self._end is None or self._duration <= 0:
            return QRect(track_left, mid_y - TIMELINE_SELECTION_HEIGHT // 2, 0, TIMELINE_SELECTION_HEIGHT)
        sx = self._time_to_x(self._start)
        ex = self._time_to_x(self._end)
        left = max(track_left, min(sx, ex))
        right = min(track_right, max(sx, ex))
        return QRect(left, mid_y - TIMELINE_SELECTION_HEIGHT // 2, max(0, right - left), TIMELINE_SELECTION_HEIGHT)

    def cursor_label_text(self) -> str:
        if self._drag_preview_time is not None:
            return _fmt_time(self._drag_preview_time)
        return _fmt_time(self._position)

    def cursor_label_rect(self) -> QRect:
        x = self._time_to_x(self._drag_preview_time if self._drag_preview_time is not None else self._position)
        fm = QFontMetrics(QFont("JetBrains Mono", 8, QFont.Weight.Medium))
        width = fm.horizontalAdvance(self.cursor_label_text()) + 10
        rect = QRect(x - width // 2, 2, width, TIMELINE_LABEL_HEIGHT)
        if rect.left() < TIMELINE_SIDE_PADDING:
            rect.moveLeft(TIMELINE_SIDE_PADDING)
        if rect.right() > self.rect().right() - TIMELINE_SIDE_PADDING:
            rect.moveRight(self.rect().right() - TIMELINE_SIDE_PADDING)
        return rect

    def cursor_color_name(self) -> str:
        c = get_theme()
        if self._cursor_mode == self.CURSOR_GREEN:
            return c.accent_success
        if self._cursor_mode == self.CURSOR_RED:
            return c.accent_error
        return "#ffffff"

    def set_data(self, duration=0, position=0, start=None, end=None):
        self._duration = duration
        self._position = position
        self._start = start
        self._end = end
        self.update()

    def set_cursor_mode(self, mode):
        self._cursor_mode = mode
        self.update()

    def set_live_clock(self, text):
        self._live_clock_text = text
        self.update()

    def _drag_preview_text(self):
        if self._drag_preview_time is None:
            return ""
        text = _fmt_time(self._drag_preview_time)
        if self._live_clock_text:
            text += f" / 直播 {self._live_clock_text}"
        return text

    def add_marker(self, start_sec, end_sec, label=""):
        self._markers.append((start_sec, end_sec, label))
        self.update()

    def clear_markers(self):
        self._markers.clear()
        self.update()

    def _time_to_x(self, t):
        track_left, track_right, _mid_y = self.track_geometry()
        if self._duration <= 0:
            return track_left
        return track_left + int((t / self._duration) * (track_right - track_left))

    def _x_to_time(self, x):
        track_left, track_right, _mid_y = self.track_geometry()
        if track_right <= track_left or self._duration <= 0:
            return 0
        ratio = max(0, min(1, (x - track_left) / (track_right - track_left)))
        return ratio * self._duration

    def _handle_at(self, x):
        if self._start is None or self._end is None or self._duration <= 0:
            return None
        sx = self._time_to_x(self._start)
        ex = self._time_to_x(self._end)
        if abs(x - sx) < TIMELINE_HANDLE_HIT_RADIUS:
            return 'start'
        if abs(x - ex) < TIMELINE_HANDLE_HIT_RADIUS:
            return 'end'
        return None

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton or self._duration <= 0:
            return
        x = int(e.position().x())
        handle = self._handle_at(x)
        if handle:
            self._drag_mode = handle
        elif self._start is not None and self._end is not None:
            sx = self._time_to_x(self._start)
            ex = self._time_to_x(self._end)
            if min(sx, ex) <= x <= max(sx, ex):
                self._drag_mode = 'range'
                self._drag_start_x = x
                self._drag_preview_time = self._x_to_time(x)
            else:
                self._drag_mode = 'position'
                self._position = self._x_to_time(x)
                self._drag_preview_time = self._position
                self.position_changed.emit(self._position)
        else:
            self._drag_mode = 'position'
            self._position = self._x_to_time(x)
            self._drag_preview_time = self._position
            self.position_changed.emit(self._position)
        self.setCursor(Qt.ClosedHandCursor if self._drag_mode in ('range', 'pan') else Qt.ArrowCursor)
        self.update()

    def mouseMoveEvent(self, e):
        if not self._drag_mode or self._duration <= 0:
            return
        x = int(e.position().x())
        t = self._x_to_time(x)
        if self._drag_mode == 'position':
            self._position = t
            self._drag_preview_time = t
            self.position_changed.emit(t)
        elif self._drag_mode == 'start':
            self._start = t
            self._drag_preview_time = t
        elif self._drag_mode == 'end':
            self._end = t
            self._drag_preview_time = t
        elif self._drag_mode == 'range':
            dx = x - self._drag_start_x
            dt = dx * self._duration / max(1, self.rect().width() - 16)
            if self._start is not None and self._end is not None:
                self._start = max(0, min(self._duration, self._start + dt))
                self._end = max(0, min(self._duration, self._end + dt))
                self._drag_preview_time = self._end
            self._drag_start_x = x
        self.update()

    def mouseReleaseEvent(self, e):
        self._drag_mode = None
        self._drag_preview_time = None
        self.setCursor(Qt.OpenHandCursor)

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta > 0:
            self.zoom_in()
        elif delta < 0:
            self.zoom_out()

    def zoom_in(self):
        old_pps = self._pixels_per_sec
        self._pixels_per_sec = min(60, self._pixels_per_sec * 1.5)
        # Maintain viewport: adjust offset so current position stays in same place
        if old_pps > 0 and self._duration > 0:
            ratio = self._pixels_per_sec / old_pps
            self._offset = int(self._offset * ratio)
        self.update()

    def zoom_out(self):
        old_pps = self._pixels_per_sec
        self._pixels_per_sec = max(1, self._pixels_per_sec / 1.5)
        if old_pps > 0 and self._duration > 0:
            ratio = self._pixels_per_sec / old_pps
            self._offset = int(self._offset * ratio)
        self.update()

    def reset_zoom(self):
        self._pixels_per_sec = 6
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = get_theme()
        palette = self.current_palette()
        r = self.rect()
        track_left, track_right, mid_y = self.track_geometry()

        p.setBrush(QColor(palette["container_fill"]))
        p.setPen(QColor(palette["container_border"]))
        p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 8, 8)

        track_pen = QPen(QColor(palette["track"]), TIMELINE_TRACK_THICKNESS)
        track_pen.setCapStyle(Qt.RoundCap)
        p.setPen(track_pen)
        p.drawLine(track_left, mid_y, track_right, mid_y)

        if self._duration > 0:
            progress_x = self._time_to_x(self._position)
            progress_pen = QPen(QColor(palette["progress"]), TIMELINE_TRACK_THICKNESS)
            progress_pen.setCapStyle(Qt.RoundCap)
            p.setPen(progress_pen)
            p.drawLine(track_left, mid_y, progress_x, mid_y)

        if self._start is not None and self._end is not None and self._duration > 0:
            sel_rect = self.selection_rect()
            p.setBrush(QColor(palette["selection_fill"]))
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(sel_rect, 6, 6)

            edge_pen = QPen(QColor(palette["selection_border"]), TIMELINE_SELECTION_BORDER_WIDTH)
            edge_pen.setCapStyle(Qt.RoundCap)
            p.setPen(edge_pen)
            p.drawLine(sel_rect.left(), sel_rect.top() - 2, sel_rect.left(), sel_rect.bottom() + 2)
            p.drawLine(sel_rect.right(), sel_rect.top() - 2, sel_rect.right(), sel_rect.bottom() + 2)

        marker_top = r.bottom() - TIMELINE_MARKER_HEIGHT - 6
        for ms, _me, _ml in self._markers:
            if self._duration <= 0:
                continue
            mx = self._time_to_x(ms)
            p.setBrush(QColor(palette["marker"]))
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(
                mx - TIMELINE_MARKER_WIDTH // 2,
                marker_top,
                TIMELINE_MARKER_WIDTH,
                TIMELINE_MARKER_HEIGHT,
                1,
                1,
            )

        if self._duration > 0:
            cursor_time = self._drag_preview_time if self._drag_preview_time is not None else self._position
            x = self._time_to_x(cursor_time)
            cursor_color = QColor(self.cursor_color_name())
            line_top = r.top() + TIMELINE_LABEL_HEIGHT + 4
            line_bottom = marker_top - 4

            if not is_dark() and self._cursor_mode == self.CURSOR_WHITE:
                outline_pen = QPen(QColor(c.border_strong), TIMELINE_CURSOR_LINE_WIDTH + 1)
                outline_pen.setCapStyle(Qt.RoundCap)
                p.setPen(outline_pen)
                p.drawLine(x, line_top, x, line_bottom)

            cursor_pen = QPen(cursor_color, TIMELINE_CURSOR_LINE_WIDTH)
            cursor_pen.setCapStyle(Qt.RoundCap)
            p.setPen(cursor_pen)
            p.drawLine(x, line_top, x, line_bottom)

            dot_rect = QRect(
                x - TIMELINE_CURSOR_DOT_SIZE // 2,
                mid_y - TIMELINE_CURSOR_DOT_SIZE // 2,
                TIMELINE_CURSOR_DOT_SIZE,
                TIMELINE_CURSOR_DOT_SIZE,
            )
            if not is_dark() and self._cursor_mode == self.CURSOR_WHITE:
                p.setBrush(QColor(c.border_strong))
                p.setPen(Qt.NoPen)
                p.drawEllipse(dot_rect.adjusted(-1, -1, 1, 1))
            p.setBrush(cursor_color)
            p.setPen(Qt.NoPen)
            p.drawEllipse(dot_rect)

            label_text = self._drag_preview_text() or self.cursor_label_text()
            label_rect = self.cursor_label_rect()
            p.setBrush(QColor(palette["label_fill"]))
            p.setPen(QColor(palette["label_border"]))
            p.drawRoundedRect(label_rect, 4, 4)
            p.setFont(QFont("JetBrains Mono", 8, QFont.Weight.Medium))
            p.setPen(QColor(palette["label_text"]))
            p.drawText(label_rect, Qt.AlignCenter, label_text)

        p.end()

class LegacyControlBar(QWidget):
    seek_back = Signal()
    seek_fwd = Signal()
    play_pause = Signal()
    export_clicked = Signal()
    mark_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(70)
        self._recording = False
        self._build()

    def _build(self):
        c = get_theme()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(12)

        # Seek back
        self._back = IconButton(34, _icon_seek_back, "后退 10 秒")
        self._back.setEnabled(False)
        self._back.clicked.connect(self.seek_back.emit)
        lay.addWidget(self._back)

        # Play/Pause button
        self._play = IconButton(34, _icon_play, "播放")
        self._play.setEnabled(False)
        self._play.clicked.connect(self.play_pause.emit)
        lay.addWidget(self._play)

        # Seek forward
        self._fwd = IconButton(34, _icon_seek_fwd, "前进 10 秒")
        self._fwd.setEnabled(False)
        self._fwd.clicked.connect(self.seek_fwd.emit)
        lay.addWidget(self._fwd)

        # Timeline
        self._timeline = InlineTimeline()
        lay.addWidget(self._timeline, 1)

        # Time display: position / duration
        self._time_label = QLabel("00:00:00 / 00:00:00")
        self._time_label.setObjectName("label_mono")
        self._time_label.setAlignment(Qt.AlignCenter)
        self._time_label.setFixedWidth(140)
        lay.addWidget(self._time_label)

        # Mark button
        self._mark = QPushButton("● 标记")
        self._mark.setFixedSize(70, 32)
        self._mark.setEnabled(False)
        self._mark.setStyleSheet(f"""
            QPushButton {{
                background:rgba(61,213,152,0.08);
                color:{c.accent_success};
                border:1px solid rgba(61,213,152,0.25);
                border-radius:6px;
                padding:0 10px;
                font-size:12px;
                font-weight:500;
            }}
            QPushButton:hover:!disabled {{
                background:rgba(61,213,152,0.15);
            }}
            QPushButton:checked {{
                background:{c.accent_success};
                color:white;
                border-color:{c.accent_success};
            }}
            QPushButton:disabled {{ opacity:0.4; }}
        """)
        self._mark.setCheckable(True)
        self._mark.clicked.connect(self.mark_clicked.emit)
        lay.addWidget(self._mark)

        # Export button
        self._export = QPushButton("↓ 导出")
        self._export.setFixedSize(70, 34)
        self._export.setEnabled(False)
        self._export.setStyleSheet(f"""
            QPushButton {{
                background:{c.accent_primary_dim};
                color:{c.accent_primary};
                border:1px solid {c.accent_primary};
                border-radius:6px;
                padding:0 14px;
                font-size:12px;
                font-weight:500;
            }}
            QPushButton:hover:!disabled {{
                background:{c.accent_primary};
                color:white;
            }}
            QPushButton:disabled {{ opacity:0.4; }}
        """)
        self._export.clicked.connect(self.export_clicked.emit)
        lay.addWidget(self._export)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = get_theme()
        p.setBrush(QColor(c.bg_secondary))
        p.setPen(QColor(c.border_subtle))
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 14, 14)
        p.end()

    def set_recording(self, r):
        self._recording = r
        # Always keep playback controls enabled so the user can
        # play/pause, seek, and mark after recording stops.
        # The play icon is updated separately via set_playing().
        self._back.setEnabled(True)
        self._fwd.setEnabled(True)
        self._mark.setEnabled(True)
        self._play.setEnabled(True)

    def set_playing(self, playing: bool):
        self._play._icon_fn = _icon_pause if playing else _icon_play
        self._play.setToolTip("暂停" if playing else "播放")
        self._play.update()

    def set_export_enabled(self, e):
        self._export.setEnabled(e)

    def set_time(self, position, duration):
        self._time_label.setText(f"{_fmt_time(position)} / {_fmt_time(duration)}")

    @property
    def timeline(self):
        return self._timeline

    @property
    def mark_button(self):
        return self._mark


CONTROL_BAR_HEIGHT = 140
CONTROL_TIME_LABEL_WIDTH = 172
CONTROL_ACTION_BUTTON_WIDTH = 84
CONTROL_ACTION_BUTTON_HEIGHT = 44


def _get_control_action_palette(*, active: bool = False, hover: bool = False) -> dict[str, str]:
    c = get_theme()
    if active and is_dark():
        return {
            "border": c.accent_primary,
            "background": c.accent_primary,
            "text": "#ffffff",
        }
    if hover and is_dark():
        return {
            "border": c.accent_primary,
            "background": "rgba(255,135,69,0.18)",
            "text": c.accent_primary,
        }
    if is_dark():
        return {
            "border": c.accent_primary,
            "background": c.accent_primary_dim,
            "text": c.accent_primary,
        }
    return get_option_button_palette(c, active=active, hover=hover)


class ControlActionButton(QPushButton):
    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self._hover = False
        self.setFixedSize(CONTROL_ACTION_BUTTON_WIDTH, CONTROL_ACTION_BUTTON_HEIGHT)
        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(True)
        self.setCheckable(False)

    def event(self, event):
        result = super().event(event)
        if event is not None and event.type() in (QEvent.Polish, QEvent.StyleChange):
            self.setFixedSize(CONTROL_ACTION_BUTTON_WIDTH, CONTROL_ACTION_BUTTON_HEIGHT)
        return result

    def current_palette(self) -> dict[str, str]:
        return _get_control_action_palette(
            active=self.isChecked(),
            hover=(self._hover and not self.isChecked()),
        )

    def enterEvent(self, event):
        self._hover = True
        self.update()
        if event is not None:
            super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        if event is not None:
            super().leaveEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if not self.isEnabled():
            p.setOpacity(0.4)
        palette = self.current_palette()
        p.setBrush(QColor(palette["background"]))
        p.setPen(QColor(palette["border"]))
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 8, 8)
        p.setPen(QColor(palette["text"]))
        p.setFont(QFont("Inter", 13, QFont.Weight.DemiBold))
        p.drawText(self.rect().adjusted(10, 0, -10, 0), Qt.AlignCenter, self.text())
        p.end()


class ControlBar(QWidget):
    seek_back = Signal()
    seek_fwd = Signal()
    play_pause = Signal()
    export_clicked = Signal()
    mark_in_clicked = Signal()
    mark_out_clicked = Signal()
    return_live_clicked = Signal()
    fullscreen_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(CONTROL_BAR_HEIGHT)
        self._recording = False
        self._live_available = False
        self._build()

    def _build(self):
        c = get_theme()
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)

        self._timeline = InlineTimeline()
        self._timeline.setFixedHeight(TIMELINE_HEIGHT)
        root.addWidget(self._timeline)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        root.addLayout(row)

        self._back = IconButton(44, _icon_seek_back, "后退 10 秒")
        self._back.setEnabled(False)
        self._back.clicked.connect(self.seek_back.emit)
        row.addWidget(self._back)

        self._play = IconButton(48, _icon_play, "播放")
        self._play.setEnabled(False)
        self._play.clicked.connect(self.play_pause.emit)
        row.addWidget(self._play)

        self._fwd = IconButton(44, _icon_seek_fwd, "前进 10 秒")
        self._fwd.setEnabled(False)
        self._fwd.clicked.connect(self.seek_fwd.emit)
        row.addWidget(self._fwd)

        self._time_label = QLabel("00:00:00 / 00:00:00")
        self._time_label.setObjectName("label_mono")
        self._time_label.setAlignment(Qt.AlignCenter)
        self._time_label.setFixedWidth(CONTROL_TIME_LABEL_WIDTH)
        self._time_label.setStyleSheet(f"font-size:14px;font-weight:600;color:{c.text_primary};font-family:'JetBrains Mono',monospace;")
        row.addWidget(self._time_label)
        row.addStretch(1)

        self._mark_in = ControlActionButton("入点")
        self._mark_in.setEnabled(False)
        self._mark_in.setCheckable(True)
        self._mark_in.setToolTip("将当前时间设为片段开始")
        self._mark_in.clicked.connect(self.mark_in_clicked.emit)
        row.addWidget(self._mark_in)

        self._mark_out = ControlActionButton("出点")
        self._mark_out.setEnabled(False)
        self._mark_out.setCheckable(True)
        self._mark_out.setToolTip("将当前时间设为片段结束")
        self._mark_out.clicked.connect(self.mark_out_clicked.emit)
        row.addWidget(self._mark_out)

        self._export = ControlActionButton("导出")
        self._export.setEnabled(False)
        self._export.clicked.connect(self.export_clicked.emit)
        row.addWidget(self._export)

        self._return_live = ControlActionButton("回直播")
        self._return_live.setEnabled(False)
        self._return_live.setToolTip("切回当前直播画面")
        self._return_live.clicked.connect(self.return_live_clicked.emit)
        row.addWidget(self._return_live)

        self._fullscreen = ControlActionButton("全屏")
        self._fullscreen.setToolTip("进入全屏播放器")
        self._fullscreen.clicked.connect(self.fullscreen_clicked.emit)
        row.addWidget(self._fullscreen)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = get_theme()
        p.setBrush(QColor(c.bg_secondary))
        p.setPen(QColor(c.border_subtle))
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 10, 10)
        p.end()

    def set_recording(self, r):
        self._recording = r
        self._back.setEnabled(True)
        self._fwd.setEnabled(True)
        self._mark_in.setEnabled(True)
        self._mark_out.setEnabled(True)
        self._play.setEnabled(True)

    def set_playing(self, playing: bool):
        self._play._icon_fn = _icon_pause if playing else _icon_play
        self._play.setToolTip("暂停" if playing else "播放")
        self._play.update()

    def set_range_state(self, has_in=False, has_out=False):
        self._mark_in.setChecked(has_in)
        self._mark_out.setChecked(has_out)
        self._mark_in.update()
        self._mark_out.update()

    def set_export_enabled(self, e):
        self._export.setEnabled(e)
        self._export.update()

    def set_live_available(self, enabled: bool):
        self._live_available = enabled
        self._return_live.setEnabled(enabled)
        self._return_live.update()

    def set_fullscreen(self, enabled: bool):
        self._fullscreen.setText("退出" if enabled else "全屏")
        self._fullscreen.setToolTip("退出全屏播放器" if enabled else "进入全屏播放器")
        self._fullscreen.update()

    def set_time(self, position, duration):
        self._time_label.setText(f"{_fmt_time(position)} / {_fmt_time(duration)}")

    @property
    def timeline(self):
        return self._timeline

    @property
    def mark_in_button(self):
        return self._mark_in

    @property
    def mark_out_button(self):
        return self._mark_out


class FullscreenPlayerWindow(QWidget):
    def __init__(self, preview: VideoPreview, controls: ControlBar, on_exit=None):
        super().__init__()
        self._on_exit = on_exit
        self.setWindowTitle("LSC 全屏播放器")
        self.setObjectName("fullscreenPlayer")
        self.setStyleSheet("QWidget#fullscreenPlayer { background:#000000; }")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(preview, 1)
        lay.addWidget(controls)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.close()
            return
        super().keyPressEvent(e)

    def closeEvent(self, e):
        callback = self._on_exit
        self._on_exit = None
        if callback:
            callback()
        super().closeEvent(e)


class ExportedCard(QWidget):
    clicked = Signal()

    def __init__(self, name, start, end, size, parent=None):
        super().__init__(parent)
        self._hover = False
        self.setMouseTracking(True)
        c = get_theme()
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
        c = get_theme()
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
        c = get_theme()
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
        self._analysis_video_path = ""
        self._analysis_highlights: list[dict] = []

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
        player_layout.addWidget(self._preview, 1)

        self._controls = ControlBar()
        self._controls.play_pause.connect(self._on_play_pause)
        self._controls.mark_in_clicked.connect(self._on_mark_in)
        self._controls.mark_out_clicked.connect(self._on_mark_out)
        self._controls.export_clicked.connect(self._on_export)
        self._controls.seek_back.connect(self._on_seek_back)
        self._controls.seek_fwd.connect(self._on_seek_fwd)
        self._controls.timeline.position_changed.connect(self._on_timeline_seek)
        self._controls.return_live_clicked.connect(self._on_return_to_live)
        self._controls.fullscreen_clicked.connect(self._on_fullscreen_toggle)
        player_layout.addWidget(self._controls)

        left.addWidget(player_section, 3)

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
        right_scroll.setStyleSheet("QScrollArea { background:transparent; border:none; }")
        right_scroll.setMinimumWidth(280)
        right_scroll.setMaximumWidth(380)
        right_scroll.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)

        self._config = ConfigPanel()
        self._config.connect_requested.connect(self._on_connect)
        self._config.start_record_requested.connect(self._on_record_toggle)
        self._config.analyze_requested.connect(self._on_analyze_current)
        self._config.export_analysis_requested.connect(self._on_export_analysis_results)
        fade_config = FadeInWidget(delay_ms=100)
        fade_config_layout = QVBoxLayout(fade_config)
        fade_config_layout.setContentsMargins(0, 0, 0, 0)
        fade_config_layout.addWidget(self._config)
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
                timer.start(200)

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
        self._preview.setParent(None)
        self._controls.setParent(None)
        self._controls.set_fullscreen(True)
        self._fullscreen_window = FullscreenPlayerWindow(
            self._preview,
            self._controls,
            on_exit=lambda: self._exit_fullscreen(close_window=False),
        )
        self._fullscreen_window.showFullScreen()

    def _exit_fullscreen(self, close_window=True):
        win = self._fullscreen_window
        if win is None:
            return
        self._fullscreen_window = None
        self._preview.setParent(None)
        self._controls.setParent(None)
        self._player_layout.insertWidget(0, self._preview, 1)
        self._player_layout.insertWidget(1, self._controls)
        self._controls.set_fullscreen(False)
        if close_window:
            win._on_exit = None
            win.close()

    # ── Connect ─────────────────────────────────────────────────

    def _on_connect(self, url):
        url = (url or "").strip()
        if not url:
            self.status_changed.emit("请输入直播间链接", "warning")
            return

        self._ctrl.page_url = url
        self._ctrl.stream_url = url
        self._config.set_connected(True)
        self._controls.set_live_available(bool(self._ctrl.page_url))
        self.status_changed.emit("连接中...", "info")
        self._ctrl.start_url_parse(url, self._on_url_parsed)

    def _on_url_parsed(self, info):
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
        self._controls.timeline.set_cursor_mode(InlineTimeline.CURSOR_WHITE)

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

        success, output_path, encoder_used = self._ctrl.start_recording_with_crf(
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
            self.status_changed.emit("录制启动失败: FFmpeg 无法连接直播流", "error")
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
            dur = self._ctrl.probe_video_duration()
            if dur > 0:
                self._ctrl.total_sec = dur
                self._controls.timeline.set_data(duration=dur, position=0)
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
                dur = self._ctrl.probe_video_duration()
                if dur > 0:
                    self._ctrl.total_sec = dur
                    self._controls.timeline.set_data(duration=dur, position=0)
                # Auto-play the completed recording
                self._preview.play_video(actual_path)
                played_output = True
            else:
                self.status_changed.emit("录制停止", "info")

        self._preview.set_state(recording=False, connected=False)
        self._controls.set_recording(False)
        self._controls.set_export_enabled(False)
        self._controls.set_range_state(False, False)
        self._controls.timeline.set_cursor_mode(InlineTimeline.CURSOR_WHITE)
        self._has_start = False
        self._start_sec = None
        self._end_sec = None
        self._config.set_recording(False)
        self._config.set_analyze_enabled(bool(self._ctrl.video_path and os.path.isfile(self._ctrl.video_path)))
        if hasattr(self._config, "set_export_analysis_enabled"):
            self._config.set_export_analysis_enabled(bool(getattr(self, "_analysis_highlights", [])))
        self._controls.set_playing(played_output)
        if played_output:
            self._ctrl.timer.start(200)
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
                self._ctrl.timer.start(200)
            return

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
            cursor_mode = InlineTimeline.CURSOR_RED if self._has_start else InlineTimeline.CURSOR_WHITE
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

        self._pending_reconnect_reason = reason
        self._reconnect_attempts = getattr(self, "_reconnect_attempts", 0) + 1
        self._ctrl.start_url_parse(page_url, self._on_url_parsed)
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
            InlineTimeline.CURSOR_RED if has_in and not has_out else InlineTimeline.CURSOR_WHITE
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
        self._start_sec = pos
        if self._end_sec is not None and self._end_sec <= self._start_sec:
            self._end_sec = None
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
        self._end_sec = pos
        self._sync_range_controls()
        self._apply_range_to_timeline(position=pos, duration=max(dur, pos, self._start_sec or 0))

    def _on_mark(self):
        self._on_mark_in()
        return
        if self._ctrl.is_recording:
            # During recording: mark based on recording elapsed time
            if self._has_start:
                self._has_start = False
                self._start_sec = None
                self._end_sec = None
                self._controls.mark_button.setChecked(False)
                self._controls.set_export_enabled(False)
                self._controls.timeline.set_cursor_mode(InlineTimeline.CURSOR_WHITE)
            else:
                self._has_start = True
                self._start_sec = self._ctrl.total_sec
                self._end_sec = self._ctrl.total_sec
                self._controls.mark_button.setChecked(True)
                self._controls.set_export_enabled(True)
                self._controls.timeline.set_cursor_mode(InlineTimeline.CURSOR_RED)
        else:
            # During playback: mark based on current playback position
            pos = self._preview.position_sec()
            dur = self._preview.duration_sec() or self._ctrl.total_sec
            if self._has_start:
                self._has_start = False
                self._start_sec = None
                self._end_sec = None
                self._controls.mark_button.setChecked(False)
                self._controls.set_export_enabled(False)
                self._controls.timeline.set_cursor_mode(InlineTimeline.CURSOR_WHITE)
            else:
                self._has_start = True
                self._start_sec = pos
                self._end_sec = min(pos + 10, dur) if dur > 0 else pos
                self._controls.mark_button.setChecked(True)
                self._controls.set_export_enabled(True)
                self._controls.timeline.set_cursor_mode(InlineTimeline.CURSOR_RED)
                self._controls.timeline.set_data(
                    duration=dur, position=pos,
                    start=self._start_sec, end=self._end_sec,
                )

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
        name = f"clip_{idx:03d}_{_fmt_time(self._start_sec).replace(':', '')}.mp4"
        duration = self._end_sec - self._start_sec
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
        ):
            self._controls.set_export_enabled(False)
        else:
            reason = getattr(self._ctrl, "exporter_init_error", "") or "导出器不可用或源文件不存在"
            self.status_changed.emit(f"片段导出失败: {reason}", "error")

    def _on_export_done(self, success, path, error, size_mb, name, idx, start_sec, end_sec):
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
            "valorant",
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
        if path and os.path.isfile(path):
            self.set_video_path(path)

    def _on_clear_range(self):
        self._has_start = False
        self._start_sec = None
        self._end_sec = None
        self._controls.set_range_state(False, False)
        self._controls.set_export_enabled(False)
        self._controls.timeline.set_cursor_mode(InlineTimeline.CURSOR_WHITE)

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

    # ── Cleanup ──────────────────────────────────────────────────

    def cleanup(self):
        self._ctrl.cleanup()
        self._preview.stop_video()
        self._preview.cleanup()
