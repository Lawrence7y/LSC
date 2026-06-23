from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QRect
from PySide6.QtGui import QPainter, QPen, QColor, QFont, QFontMetrics
from PySide6.QtWidgets import QWidget

from lsc.gui.theme import get_theme, is_dark

TIMELINE_HEIGHT = 60
TIMELINE_SIDE_PADDING = 40
TIMELINE_TRACK_THICKNESS = 3
TIMELINE_SELECTION_HEIGHT = 24
TIMELINE_SELECTION_BORDER_WIDTH = 1.5
TIMELINE_CURSOR_LINE_WIDTH = 2
TIMELINE_CURSOR_DOT_SIZE = 10
TIMELINE_HANDLE_HIT_RADIUS = 10
TIMELINE_MARKER_WIDTH = 4
TIMELINE_MARKER_HEIGHT = 8
TIMELINE_LABEL_HEIGHT = 20


def _fmt_time(seconds: float) -> str:
    s = max(0, int(seconds))
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


class InlineTimeline(QWidget):
    position_changed = Signal(float)
    marker_clicked = Signal(int)

    CURSOR_WHITE, CURSOR_GREEN, CURSOR_RED = 0, 1, 2

    def __init__(self, parent=None):
        super().__init__(parent)
        self._duration = 0
        self._position = 0
        self._start = None
        self._end = None
        self._markers: list[tuple[float, float, str]] = []
        # Zoom/pan state: _view_start/_view_end define the visible time window.
        # When zoomed to fit (default), both are None and the full duration is shown.
        self._view_start: float | None = None
        self._view_end: float | None = None
        self._drag_mode = None
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
        return c.text_primary

    def set_data(self, duration=0, position=0, start=None, end=None):
        self._duration = duration
        self._position = position
        self._start = start
        self._end = end
        self.update()

    # ── Public API for in/out points ────────────────────────────
    # Replaces direct access to private _start/_end members.

    def get_in_point(self) -> float | None:
        """Return the current mark-in position (seconds), or None."""
        return self._start

    def get_out_point(self) -> float | None:
        """Return the current mark-out position (seconds), or None."""
        return self._end

    def set_in_point(self, position: float) -> None:
        """Set the mark-in point without raising signals."""
        self._start = position
        self.update()

    def set_out_point(self, position: float) -> None:
        """Set the mark-out point without raising signals."""
        self._end = position
        self.update()

    def clear_selection(self) -> None:
        """Clear both in/out points."""
        self._start = None
        self._end = None
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

    def _visible_range(self) -> tuple[float, float]:
        """Return (view_start, view_end) for the currently visible time window."""
        vs = self._view_start if self._view_start is not None else 0.0
        ve = self._view_end if self._view_end is not None else float(self._duration)
        return vs, ve

    def _time_to_x(self, t):
        track_left, track_right, _mid_y = self.track_geometry()
        vs, ve = self._visible_range()
        span = ve - vs
        if span <= 0:
            return track_left
        return track_left + int(((t - vs) / span) * (track_right - track_left))

    def _x_to_time(self, x):
        track_left, track_right, _mid_y = self.track_geometry()
        vs, ve = self._visible_range()
        span = ve - vs
        if track_right <= track_left or span <= 0:
            return vs
        ratio = max(0, min(1, (x - track_left) / (track_right - track_left)))
        return vs + ratio * span

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
        self.setCursor(Qt.ClosedHandCursor if self._drag_mode == 'range' else Qt.ArrowCursor)
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
            track_left, track_right, _ = self.track_geometry()
            track_width = max(1, track_right - track_left)
            dt = dx * self._duration / track_width
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
        self.update()

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta > 0:
            self.zoom_in()
        elif delta < 0:
            self.zoom_out()

    def zoom_in(self):
        """Zoom in by 1.5×, centered on the current playback position."""
        vs, ve = self._visible_range()
        span = ve - vs
        if span <= 0:
            return
        new_span = span / 1.5
        # Minimum visible span: 2 seconds
        if new_span < 2:
            new_span = 2
        # Center on current position
        center = self._position
        new_vs = center - new_span / 2
        new_ve = center + new_span / 2
        # Clamp to [0, duration]
        if new_vs < 0:
            new_ve -= new_vs
            new_vs = 0
        if new_ve > self._duration:
            new_vs -= (new_ve - self._duration)
            new_ve = self._duration
        new_vs = max(0, new_vs)
        new_ve = min(self._duration, new_ve)
        self._view_start = new_vs
        self._view_end = new_ve
        self.update()

    def zoom_out(self):
        """Zoom out by 1.5×, centered on the current playback position."""
        vs, ve = self._visible_range()
        span = ve - vs
        if span <= 0:
            return
        new_span = span * 1.5
        center = self._position
        new_vs = center - new_span / 2
        new_ve = center + new_span / 2
        # Clamp to [0, duration]
        if new_vs < 0:
            new_ve -= new_vs
            new_vs = 0
        if new_ve > self._duration:
            new_vs -= (new_ve - self._duration)
            new_ve = self._duration
        new_vs = max(0, new_vs)
        new_ve = min(self._duration, new_ve)
        # If we've zoomed out past full duration, reset to fit
        if new_vs <= 0 and new_ve >= self._duration:
            self._view_start = None
            self._view_end = None
        else:
            self._view_start = new_vs
            self._view_end = new_ve
        self.update()

    def reset_zoom(self):
        """Reset to show the full duration."""
        self._view_start = None
        self._view_end = None
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

        self._draw_time_ticks(p, palette, track_left, track_right, mid_y, r)

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

    def _draw_time_ticks(self, p: QPainter, palette: dict, track_left: int, track_right: int, mid_y: int, r: QRect):
        if self._duration <= 0:
            return

        vs, ve = self._visible_range()
        visible_span = ve - vs
        if visible_span <= 15:
            tick_interval = 1
        elif visible_span <= 120:
            tick_interval = 5
        else:
            tick_interval = 30

        tick_color = QColor(palette["track"])
        tick_color.setAlpha(120)
        tick_pen = QPen(tick_color, 1)
        p.setPen(tick_pen)
        tick_font = QFont("JetBrains Mono", 7)
        p.setFont(tick_font)
        text_color = QColor(palette["label_text"])
        text_color.setAlpha(140)

        # Start ticks from the first interval at or before the visible start
        t = max(0.0, (int(vs / tick_interval)) * tick_interval) if tick_interval > 0 else 0.0
        while t <= min(ve, self._duration):
            x = self._time_to_x(t)
            if track_left <= x <= track_right:
                p.drawLine(x, mid_y + TIMELINE_TRACK_THICKNESS + 2, x, mid_y + TIMELINE_TRACK_THICKNESS + 7)
                p.setPen(text_color)
                p.drawText(
                    QRect(x - 20, mid_y + TIMELINE_TRACK_THICKNESS + 8, 40, 12),
                    Qt.AlignHCenter | Qt.AlignTop,
                    _fmt_time(t),
                )
                p.setPen(tick_pen)
            t += tick_interval
