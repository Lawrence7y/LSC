"""Icon widgets and exported clip cards for the record page."""
from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPolygon
from PySide6.QtWidgets import (
    QGridLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from lsc.utils.helpers import fmt_time as _fmt_time

from lsc.gui.components.widgets import EmptyState
from lsc.gui.theme import get_theme


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


class ExportedCard(QWidget):
    clicked = Signal()

    def __init__(self, name, start, end, size, parent=None):
        super().__init__(parent)
        self._hover = False
        self.setMouseTracking(True)
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
