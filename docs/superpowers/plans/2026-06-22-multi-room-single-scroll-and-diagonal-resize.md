# Multi-Room Single Scroll And Diagonal Resize Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让多房间工作台只保留页面最右侧一根纵向滚动条，并为每个直播间卡片增加右下角斜向拖拽缩放能力。

**Architecture:** 通过在 `MultiRoomPage` 外层增加统一的页面滚动容器，把左侧直播间区和右侧侧栏作为同一个页面内容整体滚动；同时保留现有 `FlowLayout` 的流式排布和卡片独立尺寸持久化机制，仅在 `RoomCard` 上新增一个右下角联动宽高的拖拽手柄。这样能最小化改动范围，并延续现有多房间布局与测试模式。

**Tech Stack:** Python 3, PySide6, pytest

---

### Task 1: 为单滚动页面和斜向手柄补失败测试

**Files:**
- Modify: `D:/Project/直播切片多人/tests/gui/test_multi_room_page.py`
- Test: `D:/Project/直播切片多人/tests/gui/test_multi_room_page.py`

- [ ] **Step 1: 写失败测试，覆盖右下角斜向缩放手柄**

```python
def test_room_card_exposes_diagonal_resize_handle() -> None:
    _qapp()

    from lsc.gui.components.room_card import RoomCard

    room = RoomSession(room_id="room-1", room_url="https://live.douyin.com/123")
    card = RoomCard(room)

    assert card._resize_handle_corner.objectName() == "roomCardResizeHandleCorner"
    assert card._resize_handle_corner.cursor().shape().name == "SizeFDiagCursor"
```

- [ ] **Step 2: 运行测试，确认它先失败**

Run: `pytest tests/gui/test_multi_room_page.py::test_room_card_exposes_diagonal_resize_handle -v`
Expected: FAIL，提示 `RoomCard` 还没有 `_resize_handle_corner`

- [ ] **Step 3: 写失败测试，覆盖工作台只保留一个外层纵向滚动容器**

```python
def test_multi_room_page_uses_single_outer_scroll_container() -> None:
    _qapp()

    from PySide6.QtWidgets import QScrollArea
    from lsc.gui.pages.multi_room import MultiRoomPage
    from lsc.gui.multi_room.manager import MultiRoomManager

    page = MultiRoomPage(manager=MultiRoomManager())

    assert isinstance(page._page_scroll, QScrollArea)
    assert page._page_scroll.widget() is page._page_body
    assert not page._scroll.parent() is page
    assert page._scroll.verticalScrollBarPolicy() != page._scroll.ScrollBarPolicy.ScrollBarAlwaysOff
```

- [ ] **Step 4: 运行测试，确认它先失败**

Run: `pytest tests/gui/test_multi_room_page.py::test_multi_room_page_uses_single_outer_scroll_container -v`
Expected: FAIL，提示页面还没有 `_page_scroll` / `_page_body`

- [ ] **Step 5: 提交这一轮测试草案**

```bash
git add tests/gui/test_multi_room_page.py
git commit -m "test: cover multi-room single scroll layout"
```

### Task 2: 将多房间工作台改为单外层滚动页面

**Files:**
- Modify: `D:/Project/直播切片多人/lsc/gui/pages/multi_room.py`
- Test: `D:/Project/直播切片多人/tests/gui/test_multi_room_page.py`

- [ ] **Step 1: 最小实现页面外层滚动容器**

```python
self._page_scroll = QScrollArea()
self._page_scroll.setWidgetResizable(True)
self._page_scroll.setFrameShape(QScrollArea.NoFrame)
self._page_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

self._page_body = QWidget()
self._page_layout = QVBoxLayout(self._page_body)
self._page_layout.setContentsMargins(0, 0, 0, 0)
self._page_layout.setSpacing(0)
self._page_scroll.setWidget(self._page_body)

outer = QVBoxLayout(self)
outer.setContentsMargins(0, 0, 0, 0)
outer.setSpacing(0)
outer.addWidget(self._page_scroll)
```

- [ ] **Step 2: 把现有 splitter 挂到 `_page_body` 里，而不是直接挂到页面根节点**

```python
root = QSplitter(Qt.Orientation.Horizontal)
root.setChildrenCollapsible(False)
root.setHandleWidth(10)
root.setContentsMargins(24, 24, 24, 24)
self._page_layout.addWidget(root)
self._splitter = root
```

- [ ] **Step 3: 去掉右侧录制信息内部滚动，让内容直接由页面滚动**

```python
class DetailPanel(QWidget):
    def _build(self):
        self.setObjectName("detailPanel")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self._body = QWidget()
        self._body.setObjectName("detailPanelBody")
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(0, 0, 0, 0)
        self._body_layout.setSpacing(0)
        self._body_layout.setAlignment(Qt.AlignTop)
        root.addWidget(self._body)
```

- [ ] **Step 4: 禁用左侧直播间区的独立滚动条，仅保留内容承载**

```python
self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
self._scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.MinimumExpanding)
```

- [ ] **Step 5: 跑页面相关测试，确认布局切换后仍通过**

Run: `pytest tests/gui/test_multi_room_page.py -k "multi_room_page or detail_panel or responsive_grid" -v`
Expected: PASS

- [ ] **Step 6: 提交单滚动页面改动**

```bash
git add lsc/gui/pages/multi_room.py tests/gui/test_multi_room_page.py
git commit -m "feat: use single scroll container in multi-room page"
```

### Task 3: 为直播间卡片增加右下角斜向缩放手柄

**Files:**
- Modify: `D:/Project/直播切片多人/lsc/gui/components/room_card.py`
- Modify: `D:/Project/直播切片多人/lsc/gui/theme.py`
- Test: `D:/Project/直播切片多人/tests/gui/test_multi_room_page.py`

- [ ] **Step 1: 最小实现新的右下角手柄类**

```python
class _CardCornerHandle(_CardEdgeHandle):
    def __init__(self, card: "RoomCard", parent=None):
        super().__init__(card, parent)
        self.setObjectName("roomCardResizeHandleCorner")
        self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        self._drag_pos: QPointF | None = None
        self._start_width = 0
        self._start_height = 0
```

- [ ] **Step 2: 在拖拽时同时调整卡片宽度和预览高度**

```python
def mouseMoveEvent(self, event: QMouseEvent) -> None:
    if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
        delta = event.globalPosition() - self._drag_pos
        self._card.set_card_width(self._start_width + int(delta.x()))
        self._card.set_preview_height(self._start_height + int(delta.y()))
        event.accept()
        return
    super().mouseMoveEvent(event)
```

- [ ] **Step 3: 在 `RoomCard` 中挂载并定位这个手柄**

```python
self._resize_handle_corner = _CardCornerHandle(self, self)
self._resize_handle_corner.show()
```

```python
self._resize_handle_corner.setGeometry(w - t, h - t, t, t)
self._resize_handle_corner.raise_()
```

- [ ] **Step 4: 给主题补充角手柄样式选择器**

```python
QWidget#roomCardResizeHandleH, QWidget#roomCardResizeHandleV, QWidget#roomCardResizeHandleCorner {{
    background: transparent;
}}
```

- [ ] **Step 5: 跑手柄相关测试，确认通过**

Run: `pytest tests/gui/test_multi_room_page.py -k "resize_handle or diagonal_resize_handle" -v`
Expected: PASS

- [ ] **Step 6: 提交卡片缩放改动**

```bash
git add lsc/gui/components/room_card.py lsc/gui/theme.py tests/gui/test_multi_room_page.py
git commit -m "feat: add diagonal resize handle for room cards"
```

### Task 4: 回归验证整页滚动、响应式换行和非遮挡行为

**Files:**
- Modify: `D:/Project/直播切片多人/tests/gui/test_multi_room_page.py`
- Test: `D:/Project/直播切片多人/tests/gui/test_multi_room_page.py`

- [ ] **Step 1: 增加回归测试，确认右侧侧栏和左侧卡片都在同一个页面滚动体内**

```python
def test_multi_room_page_places_splitter_inside_page_scroll_body() -> None:
    _qapp()

    from lsc.gui.pages.multi_room import MultiRoomPage
    from lsc.gui.multi_room.manager import MultiRoomManager

    page = MultiRoomPage(manager=MultiRoomManager())

    assert page._splitter.parent() is page._page_body
    assert page._right_scroll.parent() is not page
```

- [ ] **Step 2: 增加回归测试，确认房间增多后仍保留流式排布而不是重叠**

```python
def test_multi_room_page_many_rooms_keep_distinct_card_geometries() -> None:
    _qapp()

    from PySide6.QtWidgets import QApplication
    from lsc.gui.pages.multi_room import MultiRoomPage
    from lsc.gui.multi_room.manager import MultiRoomManager

    page = MultiRoomPage(manager=MultiRoomManager())
    for i in range(6):
        page._url_input.setText(f"https://live.douyin.com/{i}")
        page._on_add_room()

    page.resize(1440, 1000)
    page.show()
    QApplication.processEvents()

    rects = [card.geometry() for card in page._cards.values()]
    assert len({(r.x(), r.y(), r.width(), r.height()) for r in rects}) == len(rects)
```

- [ ] **Step 3: 跑完整目标测试集**

Run: `pytest tests/gui/test_multi_room_page.py tests/test_gui_page_audit.py -v`
Expected: PASS

- [ ] **Step 4: 提交回归测试**

```bash
git add tests/gui/test_multi_room_page.py
git commit -m "test: verify multi-room page scroll and layout regressions"
```
