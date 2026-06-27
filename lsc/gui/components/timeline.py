from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen, QPolygon
from PySide6.QtWidgets import QMenu, QWidget

from lsc.gui.theme import get_theme, is_dark

TIMELINE_HEIGHT = 80
TIMELINE_SIDE_PADDING = 16
TIMELINE_RULER_HEIGHT = 24
TIMELINE_TRACK_AREA_HEIGHT = 36
TIMELINE_TRACK_THICKNESS = 16
TIMELINE_SELECTION_HEIGHT = 16
TIMELINE_SELECTION_BORDER_WIDTH = 2
TIMELINE_CURSOR_LINE_WIDTH = 2
TIMELINE_CURSOR_TRI_SIZE = 8
TIMELINE_HANDLE_HIT_RADIUS = 14
TIMELINE_MARKER_WIDTH = 3
TIMELINE_MARKER_HEIGHT = 10
TIMELINE_LABEL_HEIGHT = 20
TIMELINE_MINOR_TICK_HEIGHT = 6
TIMELINE_MAJOR_TICK_HEIGHT = 10


def _fmt_time(seconds: float) -> str:
    s = max(0, int(seconds))
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


class InlineTimeline(QWidget):
    position_changed = Signal(float)
    marker_clicked = Signal(int)

    CURSOR_WHITE, CURSOR_GREEN, CURSOR_RED = 0, 1, 2

    in_mark_set = Signal(float)
    out_mark_set = Signal(float)
    selection_cleared = Signal()
    zoom_reset_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._duration = 0
        self._position = 0
        self._start = None
        self._end = None
        self._markers: list[tuple[float, float, str]] = []
        self._clip_segments: list[tuple[float, float]] = []
        self._view_start: float | None = None
        self._view_end: float | None = None
        self._drag_mode = None
        self._drag_start_x = 0
        self._cursor_mode = self.CURSOR_WHITE
        self._drag_preview_time = None
        self._live_clock_text = ""
        self._hover_time: float | None = None
        self._context_menu: QMenu | None = None
        self.setFixedHeight(TIMELINE_HEIGHT)
        self.setCursor(Qt.OpenHandCursor)
        self.setMouseTracking(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

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

    def _ruler_rect(self) -> QRect:
        r = self.rect()
        return QRect(
            r.x() + TIMELINE_SIDE_PADDING,
            r.y(),
            r.width() - 2 * TIMELINE_SIDE_PADDING,
            TIMELINE_RULER_HEIGHT,
        )

    def _track_top(self) -> int:
        return self.rect().y() + TIMELINE_RULER_HEIGHT + 2

    def track_geometry(self) -> tuple[int, int, int]:
        r = self.rect()
        track_top = self._track_top()
        mid_y = track_top + TIMELINE_TRACK_AREA_HEIGHT // 2
        return (
            r.x() + TIMELINE_SIDE_PADDING,
            r.right() - TIMELINE_SIDE_PADDING,
            mid_y,
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
        current = self._drag_preview_time if self._drag_preview_time is not None else self._position
        return f"{_fmt_time(current)} / {_fmt_time(self._duration)}"

    def cursor_label_rect(self) -> QRect:
        x = self._time_to_x(self._drag_preview_time if self._drag_preview_time is not None else self._position)
        fm = QFontMetrics(QFont("JetBrains Mono", 8, QFont.Weight.Medium))
        width = fm.horizontalAdvance(self.cursor_label_text()) + 14
        track_top = self._track_top()
        rect = QRect(x - width // 2, max(2, track_top - TIMELINE_LABEL_HEIGHT - 6), width, TIMELINE_LABEL_HEIGHT)
        if rect.left() < 2:
            rect.moveLeft(2)
        if rect.right() > self.rect().right() - 2:
            rect.moveRight(self.rect().right() - 2)
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

    def get_in_point(self) -> float | None:
        return self._start

    def get_out_point(self) -> float | None:
        return self._end

    def set_in_point(self, position: float) -> None:
        self._start = position
        self.update()

    def set_out_point(self, position: float) -> None:
        self._end = position
        self.update()

    def clear_selection(self) -> None:
        self._start = None
        self._end = None
        self.update()

    def set_cursor_mode(self, mode):
        self._cursor_mode = mode
        self.update()

    def set_live_clock(self, text):
        self._live_clock_text = text
        self.update()

    def set_clip_segments(self, segments: list[tuple[float, float]]) -> None:
        self._clip_segments = list(segments)
        self.update()

    def _drag_preview_text(self):
        if self._drag_preview_time is None and self._position is None:
            return ""
        current = self._drag_preview_time if self._drag_preview_time is not None else self._position
        return f"{_fmt_time(current)} / {_fmt_time(self._duration)}"

    def add_marker(self, start_sec, end_sec, label=""):
        self._markers.append((start_sec, end_sec, label))
        self.update()

    def clear_markers(self):
        self._markers.clear()
        self.update()

    def _visible_range(self) -> tuple[float, float]:
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
        if self._duration <= 0:
            return None
        cursor_x = self._time_to_x(self._position)
        if abs(x - cursor_x) < TIMELINE_HANDLE_HIT_RADIUS:
            return 'position'
        if self._start is not None and self._end is not None:
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
        if self._drag_mode and self._duration > 0:
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
            return

        if self._duration > 0 and not self._drag_mode:
            track_left, track_right, _ = self.track_geometry()
            x = int(e.position().x())
            if track_left <= x <= track_right:
                self._hover_time = self._x_to_time(x)
            else:
                self._hover_time = None
            self.update()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        self._drag_mode = None
        self._drag_preview_time = None
        self.setCursor(Qt.OpenHandCursor)
        self.update()

    def leaveEvent(self, event):
        self._hover_time = None
        self.update()
        super().leaveEvent(event)

    def _show_context_menu(self, pos: QPoint) -> None:
        if self._duration <= 0:
            return
        menu = QMenu(self)
        c = get_theme()
        dark = is_dark()
        bg = c.bg_elevated if dark else c.bg_secondary
        text = c.text_primary
        border = c.border_default
        item_sel_bg = c.accent_primary_dim
        item_sel_text = c.accent_primary
        sep = c.border_subtle
        menu.setStyleSheet(f"""
            QMenu {{
                background: {bg};
                color: {text};
                border: 1px solid {border};
                border-radius: 8px;
                padding: 6px;
            }}
            QMenu::item {{
                background: transparent;
                border-radius: 6px;
                padding: 8px 24px;
                font-size: 13px;
            }}
            QMenu::item:selected {{
                background: {item_sel_bg};
                color: {item_sel_text};
            }}
            QMenu::separator {{
                background: {sep};
                height: 1px;
                margin: 4px 8px;
            }}
        """)

        time_at_pos = self._x_to_time(pos.x())
        track_left, _, _ = self.track_geometry()
        global_pos = self.mapToGlobal(pos)

        action_mark_in = menu.addAction("标记入点 (I)")
        action_mark_out = menu.addAction("标记出点 (O)")
        menu.addSeparator()
        action_goto_start = menu.addAction("跳转到开头")
        action_goto_end = menu.addAction("跳转到结尾")
        menu.addSeparator()
        action_clear = menu.addAction("清除选区")
        action_reset_zoom = menu.addAction("重置缩放")
        menu.addSeparator()
        action_zoom_in = menu.addAction("放大 (滚轮向上)")
        action_zoom_out = menu.addAction("缩小 (滚轮向下)")

        action = menu.exec(global_pos)
        if action == action_mark_in:
            if time_at_pos >= 0:
                self._start = time_at_pos
                self.in_mark_set.emit(time_at_pos)
                self.update()
        elif action == action_mark_out:
            if time_at_pos >= 0:
                self._end = time_at_pos
                self.out_mark_set.emit(time_at_pos)
                self.update()
        elif action == action_goto_start:
            self._position = 0
            self.position_changed.emit(0)
            self.update()
        elif action == action_goto_end:
            self._position = self._duration
            self.position_changed.emit(self._duration)
            self.update()
        elif action == action_clear:
            self.clear_selection()
            self.selection_cleared.emit()
        elif action == action_reset_zoom:
            self.reset_zoom()
            self.zoom_reset_requested.emit()
        elif action == action_zoom_in:
            self.zoom_in()
        elif action == action_zoom_out:
            self.zoom_out()

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key_I:
            if self._position >= 0:
                self._start = self._position
                self.in_mark_set.emit(self._position)
                self.update()
        elif key == Qt.Key_O:
            if self._position >= 0:
                self._end = self._position
                self.out_mark_set.emit(self._position)
                self.update()
        elif key == Qt.Key_X:
            self.clear_selection()
            self.selection_cleared.emit()
        elif key == Qt.Key_Home:
            self._position = 0
            self.position_changed.emit(0)
            self.update()
        elif key == Qt.Key_End:
            self._position = self._duration
            self.position_changed.emit(self._duration)
            self.update()
        elif key == Qt.Key_Space:
            super().keyPressEvent(event)
        else:
            super().keyPressEvent(event)

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            if delta > 0:
                self.zoom_in()
            elif delta < 0:
                self.zoom_out()
            event.accept()
        else:
            delta = event.angleDelta().y()
            if delta > 0:
                self.zoom_in()
            elif delta < 0:
                self.zoom_out()

    def zoom_in(self):
        vs, ve = self._visible_range()
        span = ve - vs
        if span <= 0:
            return
        new_span = span / 1.5
        if new_span < 2:
            new_span = 2
        center = self._position
        new_vs = center - new_span / 2
        new_ve = center + new_span / 2
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
        vs, ve = self._visible_range()
        span = ve - vs
        if span <= 0:
            return
        new_span = span * 1.5
        center = self._position
        new_vs = center - new_span / 2
        new_ve = center + new_span / 2
        if new_vs < 0:
            new_ve -= new_vs
            new_vs = 0
        if new_ve > self._duration:
            new_vs -= (new_ve - self._duration)
            new_ve = self._duration
        new_vs = max(0, new_vs)
        new_ve = min(self._duration, new_ve)
        if new_vs <= 0 and new_ve >= self._duration:
            self._view_start = None
            self._view_end = None
        else:
            self._view_start = new_vs
            self._view_end = new_ve
        self.update()

    def reset_zoom(self):
        self._view_start = None
        self._view_end = None
        self.update()

    # ── Paint ────────────────────────────────────────────────────

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = get_theme()
        r = self.rect()
        track_left, track_right, mid_y = self.track_geometry()
        ruler_rect = self._ruler_rect()
        palette = self.current_palette()

        # ── 时间刻度标尺 ──
        self._draw_time_ticks(p, palette, track_left, track_right, mid_y, r, ruler_rect)

        # ── 轨道背景 ──
        track_top = mid_y - TIMELINE_TRACK_THICKNESS // 2
        track_bg = QColor(c.border_default)
        track_bg.setAlphaF(0.25)
        p.setBrush(track_bg)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRect(track_left, track_top, track_right - track_left, TIMELINE_TRACK_THICKNESS), 4, 4)

        if self._duration <= 0:
            p.end()
            return

        # ── 已录制进度 ──
        progress_x = self._time_to_x(self._position)
        progress_color = QColor(c.accent_primary)
        progress_color.setAlphaF(0.35)
        p.setBrush(progress_color)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRect(track_left, track_top, max(0, progress_x - track_left), TIMELINE_TRACK_THICKNESS), 4, 4)

        # ── 切片段 ──
        clip_color = QColor(c.accent_success)
        clip_color.setAlphaF(0.45)
        for cs, ce in self._clip_segments:
            if cs >= ce:
                continue
            cx1 = self._time_to_x(cs)
            cx2 = self._time_to_x(ce)
            clip_left = max(track_left, cx1)
            clip_right = min(track_right, cx2)
            if clip_right > clip_left:
                p.setBrush(clip_color)
                p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(QRect(clip_left, track_top, clip_right - clip_left, TIMELINE_TRACK_THICKNESS), 3, 3)
                edge_pen = QPen(QColor(c.accent_success), 1.5)
                p.setPen(edge_pen)
                p.drawLine(clip_left, track_top, clip_left, track_top + TIMELINE_TRACK_THICKNESS)
                p.drawLine(clip_right, track_top, clip_right, track_top + TIMELINE_TRACK_THICKNESS)

        # ── 选区 ──
        if self._start is not None and self._end is not None:
            sx = self._time_to_x(self._start)
            ex = self._time_to_x(self._end)
            sel_left = max(track_left, min(sx, ex))
            sel_right = min(track_right, max(sx, ex))
            sel_w = max(1, sel_right - sel_left)
            sel_fill = QColor(c.accent_primary)
            sel_fill.setAlphaF(0.22)
            p.setBrush(sel_fill)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(QRect(sel_left, track_top, sel_w, TIMELINE_TRACK_THICKNESS), 3, 3)
            edge_pen = QPen(QColor(c.accent_primary), 2)
            edge_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(edge_pen)
            p.drawLine(sel_left, track_top - 3, sel_left, track_top + TIMELINE_TRACK_THICKNESS + 3)
            p.drawLine(sel_right, track_top - 3, sel_right, track_top + TIMELINE_TRACK_THICKNESS + 3)
            handle_h = 8
            handle_w = 4
            p.setBrush(QColor(c.accent_primary))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(QRect(sel_left - handle_w // 2, mid_y - handle_h // 2, handle_w, handle_h), 1, 1)
            p.drawRoundedRect(QRect(sel_right - handle_w // 2, mid_y - handle_h // 2, handle_w, handle_h), 1, 1)

        # ── 标记点 ──
        for ms, _me, _ml in self._markers:
            mx = self._time_to_x(ms)
            marker_color = QColor(c.accent_primary)
            marker_color.setAlphaF(0.6)
            p.setPen(QPen(marker_color, 2))
            p.drawLine(mx, track_top - 2, mx, track_top + TIMELINE_TRACK_THICKNESS + 2)

        # ── 悬停指示线 ──
        if self._hover_time is not None and not self._drag_mode:
            hx = self._time_to_x(self._hover_time)
            hover_color = QColor(c.text_tertiary)
            hover_color.setAlphaF(0.5)
            p.setPen(QPen(hover_color, 1, Qt.PenStyle.DashLine))
            p.drawLine(hx, track_top - 2, hx, track_top + TIMELINE_TRACK_THICKNESS + 2)

        # ── 播放头：三角形 + 贯穿竖线 ──
        cursor_time = self._drag_preview_time if self._drag_preview_time is not None else self._position
        x = self._time_to_x(cursor_time)
        cursor_color = QColor(self.cursor_color_name())

        line_top = ruler_rect.bottom() + 2
        line_bottom = r.bottom() - 2
        p.setPen(QPen(cursor_color, TIMELINE_CURSOR_LINE_WIDTH))
        p.drawLine(x, line_top, x, line_bottom)

        tri = TIMELINE_CURSOR_TRI_SIZE
        tri_top = track_top - 4
        p.setBrush(cursor_color)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon(QPolygon([
            QPoint(x, tri_top + tri),
            QPoint(x - tri, tri_top),
            QPoint(x + tri, tri_top),
        ]))

        # ── 时间码浮窗（悬停/拖动时）──
        show_tooltip = self._drag_mode is not None or self._hover_time is not None
        if show_tooltip:
            label_text = self._drag_preview_text() or self.cursor_label_text()
            label_rect = self.cursor_label_rect()
            capsule_bg = QColor("#1c1c1e") if is_dark() else QColor("#3a3a3c")
            p.setBrush(capsule_bg)
            p.setPen(QPen(QColor(c.border_default), 1))
            p.drawRoundedRect(label_rect, 6, 6)
            p.setFont(QFont("JetBrains Mono", 8, QFont.Weight.Medium))
            p.setPen(QColor("#ffffff"))
            p.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, label_text)
            tri_size = 4
            tri_top_lbl = label_rect.bottom() + 1
            p.setBrush(capsule_bg)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawPolygon(QPolygon([
                QPoint(x, tri_top_lbl + tri_size),
                QPoint(x - tri_size, tri_top_lbl),
                QPoint(x + tri_size, tri_top_lbl),
            ]))

        p.end()

    def _draw_time_ticks(self, p: QPainter, palette: dict, track_left: int, track_right: int, mid_y: int, r: QRect, ruler_rect: QRect):
        if self._duration <= 0:
            return

        vs, ve = self._visible_range()
        visible_span = ve - vs

        if visible_span <= 10:
            major_interval = 1
            minor_divisions = 4
        elif visible_span <= 30:
            major_interval = 5
            minor_divisions = 5
        elif visible_span <= 120:
            major_interval = 10
            minor_divisions = 2
        elif visible_span <= 600:
            major_interval = 30
            minor_divisions = 3
        elif visible_span <= 3600:
            major_interval = 60
            minor_divisions = 4
        else:
            major_interval = 300
            minor_divisions = 5

        minor_interval = major_interval / minor_divisions if minor_divisions > 0 else major_interval

        major_tick_color = QColor(palette["label_text"])
        major_tick_color.setAlpha(200)
        minor_tick_color = QColor(palette["track"])
        minor_tick_color.setAlpha(120)

        major_font = QFont("JetBrains Mono", 8, QFont.Weight.Medium)
        text_color = QColor(palette["label_text"])
        text_color.setAlpha(220)

        ruler_bottom = ruler_rect.bottom()
        ruler_top = ruler_rect.top()

        p.setFont(major_font)
        fm = QFontMetrics(major_font)

        t = max(0.0, (int(vs / major_interval)) * major_interval) if major_interval > 0 else 0.0
        while t <= min(ve, self._duration) + minor_interval:
            x = self._time_to_x(t)
            if track_left - 5 <= x <= track_right + 5:
                p.setPen(QPen(major_tick_color, 1.5))
                p.drawLine(x, ruler_bottom - TIMELINE_MAJOR_TICK_HEIGHT, x, ruler_bottom)
                time_text = _fmt_time(t)
                text_rect = QRect(x - 30, ruler_top + 4, 60, fm.height())
                p.setPen(text_color)
                p.drawText(text_rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, time_text)
            t += major_interval

        p.setPen(QPen(minor_tick_color, 1))
        t = max(0.0, (int(vs / minor_interval)) * minor_interval) if minor_interval > 0 else 0.0
        while t <= min(ve, self._duration) + minor_interval / 2:
            x = self._time_to_x(t)
            is_major = abs(t - round(t / major_interval) * major_interval) < 0.001
            if track_left - 5 <= x <= track_right + 5 and not is_major:
                p.drawLine(x, ruler_bottom - TIMELINE_MINOR_TICK_HEIGHT, x, ruler_bottom)
            t += minor_interval

        grid_color = QColor(palette["track"])
        grid_color.setAlpha(40)
        p.setPen(QPen(grid_color, 1, Qt.PenStyle.DotLine))
        t = 0.0
        while t <= self._duration:
            x = self._time_to_x(t)
            if track_left <= x <= track_right:
                p.drawLine(x, ruler_bottom + 2, x, r.bottom() - 4)
            t += major_interval
