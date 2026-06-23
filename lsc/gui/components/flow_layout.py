"""流式布局(FlowLayout)。

参考 Qt 官方 FlowLayout 示例适配 PySide6,新增「行内填充扩展」:
默认情况下卡片在每行内均匀扩展填满可用宽度(不超过各自 maximumWidth),
而被用户手动固定宽度(minimumWidth == maximumWidth)的卡片保持固定宽、
不参与扩展。这样既能像网格一样排满,又允许单张卡片独立改变宽度并自然换行。
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QLayout, QLayoutItem, QWidget, QWidgetItem


class FlowLayout(QLayout):
    """横向排列、自动换行的流式布局。"""

    def __init__(self, parent: QWidget | None = None, spacing: int = 8):
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._spacing = spacing

    # ── item management ──────────────────────────────────────────
    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802 (Qt API)
        self._items.append(item)

    def addWidget(self, widget: QWidget) -> None:  # noqa: N802 (Qt API)
        self.addItem(QWidgetItem(widget))

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:  # noqa: N802 (Qt API)
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int) -> QLayoutItem | None:  # noqa: N802 (Qt API)
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def spacing(self) -> int:  # noqa: N802 (Qt API)
        return self._spacing

    def setSpacing(self, spacing: int) -> None:  # noqa: N802 (Qt API)
        self._spacing = int(spacing)
        self.invalidate()

    # ── size hints ───────────────────────────────────────────────
    def expandingDirections(self) -> Qt.Orientation:  # noqa: N802 (Qt API)
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802 (Qt API)
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 (Qt API)
        return self.doLayout(QRect(0, 0, width, 0), test_only=True)

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt API)
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802 (Qt API)
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        extra = QSize(2 * self._spacing, 2 * self._spacing)
        return size + extra

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802 (Qt API)
        super().setGeometry(rect)
        self.doLayout(rect, test_only=False)

    # ── core layout ──────────────────────────────────────────────
    @staticmethod
    def _item_width(item: QLayoutItem) -> int:
        """item 的基准宽度,clamp 到 widget 的 [minimumWidth, maximumWidth]。"""
        w = item.sizeHint().width()
        wid = item.widget()
        if wid is not None:
            lo = wid.minimumWidth()
            hi = wid.maximumWidth() if wid.maximumWidth() >= 0 else w
            if hi > 0:
                w = max(lo, min(hi, w))
        return max(0, w)

    def _is_expandable(self, item: QLayoutItem) -> bool:
        wid = item.widget()
        if wid is None:
            return False
        return wid.minimumWidth() < wid.maximumWidth()

    def _row_max_extra(self, row: list[tuple[QLayoutItem, int]]) -> dict[int, int]:
        """每行中可扩展 item 还能再增长多少(到其 maximumWidth)。"""
        grow: dict[int, int] = {}
        for i, (item, w) in enumerate(row):
            wid = item.widget()
            if wid is None:
                continue
            if wid.maximumWidth() > 0 and wid.minimumWidth() < wid.maximumWidth():
                grow[i] = max(0, wid.maximumWidth() - w)
        return grow

    def doLayout(self, rect: QRect, test_only: bool) -> int:
        spacing = self._spacing
        items = [it for it in self._items if not it.isEmpty()]
        if not items:
            return 0

        x0 = rect.x()
        right = rect.x() + rect.width()
        # ── greedy line break ──
        rows: list[list[tuple[QLayoutItem, int]]] = []
        cur: list[tuple[QLayoutItem, int]] = []
        cur_w = 0
        for it in items:
            w = self._item_width(it)
            gap = spacing if cur else 0
            if cur and x0 + cur_w + gap + w > right + 1:
                rows.append(cur)
                cur = []
                cur_w = 0
                gap = 0
            cur.append((it, w))
            cur_w += gap + w
        if cur:
            rows.append(cur)

        y = rect.y()
        for row in rows:
            total_base = sum(w for _, w in row) + spacing * (len(row) - 1)
            extra = max(0, rect.width() - total_base)
            if extra > 0:
                grow = self._row_max_extra(row)
                total_grow = sum(grow.values())
                if total_grow > 0:
                    for i, g in grow.items():
                        share = extra * g / total_grow
                        take = min(g, int(round(share)))
                        row[i] = (row[i][0], row[i][1] + take)
            # place
            x = x0
            line_h = 0
            for it, w in row:
                # 使用 heightForWidth(w) 获取实际宽度下的准确高度,
                # 避免 sizeHint().height() 在宽度变化后返回过时值。
                wid = it.widget()
                if wid is not None and wid.hasHeightForWidth():
                    h = wid.heightForWidth(w)
                else:
                    h = it.sizeHint().height()
                if not test_only:
                    it.setGeometry(QRect(QPoint(x, y), QSize(w, h)))
                x += w + spacing
                line_h = max(line_h, h)
            y += line_h + spacing
        return y - rect.y()
