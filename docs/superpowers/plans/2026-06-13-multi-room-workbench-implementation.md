# Multi-Room Workbench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first multi-room workbench for the Python GUI so users can dynamically add room cards, preview multiple rooms, mute each room locally, and record multiple rooms at the same time.

**Architecture:** Add a dedicated multi-room session layer instead of cloning the current single-room page state. Each room gets its own `RoomSession` and single-room `RecordingController`, while a `MultiRoomManager` owns the collection, batch operations, and platform parsing. The new `MultiRoomPage` and `RoomCard` stay thin and consume manager/session state.

**Tech Stack:** Python 3.10+, PySide6 widgets/signals/layouts, existing `RecordingController`, existing `MpvWidget` preview path, platform adapters under `lsc/platforms`, pytest.

---

## Reference Inputs

- Spec: `docs/superpowers/specs/2026-06-13-multi-room-workbench-design.md`
- Existing single-room page: `lsc/gui/pages/record.py`
- Existing single-room controller: `lsc/gui/pages/recording_controller.py`
- Existing preview route: `lsc/gui/pages/video_preview.py`
- Existing page decomposition examples: `lsc/gui/pages/config_panel.py`, `lsc/gui/pages/control_bar.py`
- Existing adapter API: `lsc/platforms/base.py`, `lsc/platforms/registry.py`
- Existing focused regression files: `tests/test_recording_controller_options.py`, `tests/gui/test_record_interactions.py`, `tests/test_gui_page_audit.py`

## File Structure

- Create: `lsc/gui/multi_room/__init__.py`
  - Re-export the multi-room session and manager API.
- Create: `lsc/gui/multi_room/session.py`
  - Owns the `RoomSession` dataclass and single-room state helpers.
- Create: `lsc/gui/multi_room/manager.py`
  - Owns multi-room collection management, controller lifecycle, connect/disconnect, mute, single-room and batch recording actions.
- Create: `lsc/gui/components/room_card.py`
  - Owns the room card widget, card-level signals, and local preview mute state wiring.
- Create: `lsc/gui/pages/multi_room.py`
  - Owns the multi-room page layout, toolbar, grid container, detail panel, status summary, and manager-to-UI wiring.
- Modify: `lsc/gui/pages/dashboard.py`
  - Add a navigation entry or launch path to the multi-room page if the current page switcher requires it.
- Modify: `lsc/gui/pages/record_page.py`
  - If this file is the current page registry wrapper, wire the new `MultiRoomPage` into the existing page export surface.
- Create: `tests/test_multi_room_manager.py`
  - Unit tests for `RoomSession` and `MultiRoomManager`.
- Create: `tests/gui/test_multi_room_page.py`
  - Focused GUI interaction tests for room cards and page behavior.
- Modify: `tests/test_gui_page_reexports.py`
  - Ensure the new page is reachable through the page export surface if needed.

---

### Task 1: RoomSession Model

**Files:**
- Create: `lsc/gui/multi_room/__init__.py`
- Create: `lsc/gui/multi_room/session.py`
- Create: `tests/test_multi_room_manager.py`

- [ ] **Step 1: Write the failing RoomSession tests**

Create `tests/test_multi_room_manager.py` with these initial tests:

```python
"""Tests for multi-room session and manager behavior."""
from __future__ import annotations

from lsc.gui.multi_room.session import RoomSession


def test_room_session_defaults_to_muted_and_disconnected() -> None:
    session = RoomSession(room_id="room-1", room_url="https://live.douyin.com/123")

    assert session.room_id == "room-1"
    assert session.room_url == "https://live.douyin.com/123"
    assert session.platform == ""
    assert session.stream_info is None
    assert session.selected_quality == ""
    assert session.preview_muted is True
    assert session.is_connected is False
    assert session.is_recording is False
    assert session.record_output_path == ""
    assert session.record_started_at is None
    assert session.last_error == ""
    assert session.controller is None


def test_room_session_can_apply_stream_info_fields() -> None:
    from lsc.platforms.base import StreamInfo

    session = RoomSession(room_id="room-1", room_url="https://live.bilibili.com/123")
    info = StreamInfo(
        platform="bilibili",
        room_url=session.room_url,
        stream_url="https://example.com/live.m3u8",
        title="直播标题",
        streamer="主播A",
        is_live=True,
        quality_urls={"origin": "https://example.com/live.m3u8"},
        selected_quality="origin",
    )

    session.apply_stream_info(info)

    assert session.platform == "bilibili"
    assert session.stream_info is info
    assert session.selected_quality == "origin"
    assert session.is_connected is True
    assert session.last_error == ""


def test_room_session_can_capture_error_without_marking_connected() -> None:
    session = RoomSession(room_id="room-1", room_url="https://www.huya.com/123")

    session.set_error("虎牙直播间未开播")

    assert session.is_connected is False
    assert session.last_error == "虎牙直播间未开播"
```

- [ ] **Step 2: Run the RoomSession tests and verify they fail**

Run:

```powershell
$env:PYTHONPATH='.'
python -m pytest tests/test_multi_room_manager.py -q --no-cov
```

Expected: FAIL with `ModuleNotFoundError: No module named 'lsc.gui.multi_room'`.

- [ ] **Step 3: Implement RoomSession**

Create `lsc/gui/multi_room/session.py`:

```python
"""Room session state for the multi-room workbench."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from lsc.platforms.base import StreamInfo


@dataclass(slots=True)
class RoomSession:
    room_id: str
    room_url: str
    platform: str = ""
    stream_info: StreamInfo | None = None
    selected_quality: str = ""
    preview_muted: bool = True
    is_connected: bool = False
    is_recording: bool = False
    record_output_path: str = ""
    record_started_at: datetime | None = None
    last_error: str = ""
    controller: object | None = None

    def apply_stream_info(self, info: StreamInfo) -> None:
        self.platform = info.platform
        self.stream_info = info
        self.selected_quality = info.selected_quality
        self.is_connected = bool(info.is_live and info.stream_url)
        self.last_error = ""

    def set_error(self, message: str) -> None:
        self.is_connected = False
        self.last_error = message
```

Create `lsc/gui/multi_room/__init__.py`:

```python
"""Multi-room workbench state helpers."""
from .session import RoomSession

__all__ = ["RoomSession"]
```

- [ ] **Step 4: Run the RoomSession tests and verify they pass**

Run:

```powershell
$env:PYTHONPATH='.'
python -m pytest tests/test_multi_room_manager.py -q --no-cov
```

Expected: PASS with the three RoomSession tests green.

- [ ] **Step 5: Commit Task 1**

Run:

```powershell
git add lsc/gui/multi_room/__init__.py lsc/gui/multi_room/session.py tests/test_multi_room_manager.py
git commit -m "feat: add multi-room session model"
```

Expected: commit succeeds with only the session model and its tests.

---

### Task 2: MultiRoomManager

**Files:**
- Modify: `lsc/gui/multi_room/__init__.py`
- Modify: `tests/test_multi_room_manager.py`
- Create: `lsc/gui/multi_room/manager.py`

- [ ] **Step 1: Add failing manager tests**

Append to `tests/test_multi_room_manager.py`:

```python
from types import SimpleNamespace


def test_manager_add_room_creates_unique_session() -> None:
    from lsc.gui.multi_room.manager import MultiRoomManager

    manager = MultiRoomManager(controller_factory=lambda: SimpleNamespace())

    first = manager.add_room("https://live.douyin.com/123")
    second = manager.add_room("https://live.bilibili.com/456")

    assert first.room_id != second.room_id
    assert len(manager.list_rooms()) == 2
    assert manager.get_room(first.room_id) is first


def test_manager_connect_room_applies_platform_parse_result(monkeypatch) -> None:
    from lsc.gui.multi_room.manager import MultiRoomManager
    from lsc.platforms.base import StreamInfo

    manager = MultiRoomManager(controller_factory=lambda: SimpleNamespace())
    room = manager.add_room("https://live.bilibili.com/123")

    def fake_parse_stream(url: str) -> StreamInfo:
        assert url == "https://live.bilibili.com/123"
        return StreamInfo(
            platform="bilibili",
            room_url=url,
            stream_url="https://example.com/live.m3u8",
            title="直播标题",
            streamer="主播A",
            is_live=True,
            quality_urls={"origin": "https://example.com/live.m3u8"},
            selected_quality="origin",
        )

    monkeypatch.setattr("lsc.gui.multi_room.manager.parse_stream", fake_parse_stream)

    ok = manager.connect_room(room.room_id)

    assert ok is True
    assert room.platform == "bilibili"
    assert room.is_connected is True
    assert room.last_error == ""


def test_manager_mute_room_only_updates_session_flag() -> None:
    from lsc.gui.multi_room.manager import MultiRoomManager

    manager = MultiRoomManager(controller_factory=lambda: SimpleNamespace())
    room = manager.add_room("https://www.huya.com/123")

    manager.mute_room(room.room_id, False)
    assert room.preview_muted is False

    manager.mute_room(room.room_id, True)
    assert room.preview_muted is True


def test_manager_start_and_stop_recording_uses_room_controller(tmp_path) -> None:
    from lsc.gui.multi_room.manager import MultiRoomManager

    class FakeController:
        def __init__(self):
            self.stream_url = "https://example.com/live.m3u8"
            self.input_args = ["-headers", "Referer: https://example.com/\r\n"]
            self.calls = []

        def start_recording_with_crf(self, stream_url, output_dir, encoder, crf, **kwargs):
            self.calls.append(("start", stream_url, output_dir, encoder, crf, kwargs))
            return True, str(tmp_path / "recording.mp4"), encoder

        def stop_recording(self):
            self.calls.append(("stop",))
            return True, 12.3, str(tmp_path / "recording.mp4")

    manager = MultiRoomManager(controller_factory=FakeController)
    room = manager.add_room("https://example.com/live.m3u8")
    room.is_connected = True
    room.controller.stream_url = "https://example.com/live.m3u8"

    ok = manager.start_recording(room.room_id, str(tmp_path), "Copy", 23)
    stopped = manager.stop_recording(room.room_id)

    assert ok is True
    assert room.is_recording is False
    assert room.record_output_path.endswith("recording.mp4")
    assert stopped is True


def test_manager_start_recording_all_is_failure_isolated(tmp_path) -> None:
    from lsc.gui.multi_room.manager import MultiRoomManager

    class FakeController:
        def __init__(self):
            self.stream_url = ""

        def start_recording_with_crf(self, stream_url, output_dir, encoder, crf, **kwargs):
            return bool(stream_url), str(tmp_path / "recording.mp4"), encoder

    manager = MultiRoomManager(controller_factory=FakeController)
    a = manager.add_room("https://example.com/a.m3u8")
    b = manager.add_room("https://example.com/b.m3u8")
    a.is_connected = True
    b.is_connected = True
    a.controller.stream_url = "https://example.com/a.m3u8"
    b.controller.stream_url = ""

    result = manager.start_recording_all(str(tmp_path), "Copy", 23)

    assert result[a.room_id] is True
    assert result[b.room_id] is False
```

- [ ] **Step 2: Run the manager tests and verify they fail**

Run:

```powershell
$env:PYTHONPATH='.'
python -m pytest tests/test_multi_room_manager.py -q --no-cov
```

Expected: FAIL because `lsc.gui.multi_room.manager` does not exist.

- [ ] **Step 3: Implement MultiRoomManager**

Create `lsc/gui/multi_room/manager.py`:

```python
"""Manager for multi-room workbench sessions."""
from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from lsc.gui.pages.recording_controller import RecordingController
from lsc.platforms.registry import parse_stream

from .session import RoomSession


class MultiRoomManager:
    def __init__(self, controller_factory=None):
        self._controller_factory = controller_factory or RecordingController
        self._rooms: dict[str, RoomSession] = {}

    def add_room(self, url: str) -> RoomSession:
        room_id = uuid4().hex
        controller = self._controller_factory()
        room = RoomSession(room_id=room_id, room_url=url.strip(), controller=controller)
        self._rooms[room_id] = room
        return room

    def remove_room(self, room_id: str) -> bool:
        room = self._rooms.pop(room_id, None)
        if room is None:
            return False
        controller = room.controller
        if controller and hasattr(controller, "cleanup"):
            controller.cleanup()
        return True

    def get_room(self, room_id: str) -> RoomSession | None:
        return self._rooms.get(room_id)

    def list_rooms(self) -> list[RoomSession]:
        return list(self._rooms.values())

    def connect_room(self, room_id: str) -> bool:
        room = self.get_room(room_id)
        if room is None:
            return False
        info = parse_stream(room.room_url)
        room.apply_stream_info(info)
        if not info.is_live or not info.stream_url:
            room.set_error(info.error or "直播间连接失败")
            return False
        controller = room.controller
        if controller is not None:
            controller.stream_url = info.stream_url
            controller.input_args = info.to_legacy_dict().get("_inputArgs", [])
        return True

    def disconnect_room(self, room_id: str) -> bool:
        room = self.get_room(room_id)
        if room is None:
            return False
        room.is_connected = False
        return True

    def mute_room(self, room_id: str, muted: bool) -> None:
        room = self.get_room(room_id)
        if room is not None:
            room.preview_muted = muted

    def start_recording(self, room_id: str, output_dir: str, encoder: str, crf: int) -> bool:
        room = self.get_room(room_id)
        if room is None or room.controller is None:
            return False
        ok, output_path, _encoder_used = room.controller.start_recording_with_crf(
            room.controller.stream_url,
            output_dir,
            encoder,
            crf,
            input_args=room.controller.input_args or None,
        )
        room.is_recording = ok
        room.record_output_path = output_path
        room.record_started_at = datetime.now() if ok else None
        if not ok:
            room.last_error = "录制启动失败"
        return ok

    def stop_recording(self, room_id: str) -> bool:
        room = self.get_room(room_id)
        if room is None or room.controller is None:
            return False
        ok, _size_mb, output_path = room.controller.stop_recording()
        room.is_recording = False
        if ok and output_path:
            room.record_output_path = output_path
        return ok

    def start_recording_all(self, output_dir: str, encoder: str, crf: int) -> dict[str, bool]:
        return {
            room.room_id: self.start_recording(room.room_id, output_dir, encoder, crf)
            for room in self.list_rooms()
        }

    def stop_recording_all(self) -> dict[str, bool]:
        return {room.room_id: self.stop_recording(room.room_id) for room in self.list_rooms()}
```

Update `lsc/gui/multi_room/__init__.py`:

```python
"""Multi-room workbench state helpers."""
from .manager import MultiRoomManager
from .session import RoomSession

__all__ = ["MultiRoomManager", "RoomSession"]
```

- [ ] **Step 4: Run the manager tests and verify they pass**

Run:

```powershell
$env:PYTHONPATH='.'
python -m pytest tests/test_multi_room_manager.py -q --no-cov
```

Expected: PASS with all manager tests green.

- [ ] **Step 5: Commit Task 2**

Run:

```powershell
git add lsc/gui/multi_room/__init__.py lsc/gui/multi_room/manager.py tests/test_multi_room_manager.py
git commit -m "feat: add multi-room session manager"
```

Expected: commit succeeds with manager behavior and tests.

---

### Task 3: RoomCard Component

**Files:**
- Create: `lsc/gui/components/room_card.py`
- Create: `tests/gui/test_multi_room_page.py`

- [ ] **Step 1: Write failing RoomCard tests**

Create `tests/gui/test_multi_room_page.py`:

```python
"""GUI tests for the multi-room workbench page and room cards."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication


def _qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_room_card_reflects_basic_room_state() -> None:
    _qapp()

    from lsc.gui.components.room_card import RoomCard
    from lsc.gui.multi_room.session import RoomSession

    room = RoomSession(room_id="room-1", room_url="https://live.douyin.com/123", platform="douyin")
    room.preview_muted = True
    room.is_connected = True

    card = RoomCard(room)

    assert "douyin" in card._platform_label.text().lower()
    assert card._mute_button.isChecked() is True
    assert "已连接" in card._status_label.text()


def test_room_card_emits_mute_toggle_for_own_room() -> None:
    _qapp()

    from lsc.gui.components.room_card import RoomCard
    from lsc.gui.multi_room.session import RoomSession

    room = RoomSession(room_id="room-1", room_url="https://live.douyin.com/123")
    card = RoomCard(room)
    hits = []
    card.mute_toggled.connect(lambda room_id, muted: hits.append((room_id, muted)))

    card._mute_button.click()

    assert hits[-1] == ("room-1", False)
```

- [ ] **Step 2: Run the RoomCard tests and verify they fail**

Run:

```powershell
$env:PYTHONPATH='.'
python -m pytest tests/gui/test_multi_room_page.py -q --no-cov
```

Expected: FAIL because `lsc.gui.components.room_card` does not exist.

- [ ] **Step 3: Implement RoomCard**

Create `lsc/gui/components/room_card.py`:

```python
"""Room card widget for the multi-room workbench."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from lsc.gui.multi_room.session import RoomSession


class RoomCard(QWidget):
    selected = Signal(str)
    connect_requested = Signal(str)
    record_requested = Signal(str)
    stop_requested = Signal(str)
    remove_requested = Signal(str)
    mute_toggled = Signal(str, bool)

    def __init__(self, room: RoomSession, parent=None):
        super().__init__(parent)
        self.room = room
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        self._platform_label = QLabel()
        self._title_label = QLabel()
        self._status_label = QLabel()
        self._mute_button = QCheckBox("静音")
        self._connect_button = QPushButton("连接")
        self._record_button = QPushButton("录制")
        self._stop_button = QPushButton("停止")
        self._remove_button = QPushButton("移除")

        root.addWidget(self._platform_label)
        root.addWidget(self._title_label)
        root.addWidget(self._status_label)

        controls = QHBoxLayout()
        controls.addWidget(self._connect_button)
        controls.addWidget(self._record_button)
        controls.addWidget(self._stop_button)
        controls.addWidget(self._mute_button)
        controls.addWidget(self._remove_button)
        root.addLayout(controls)

        self._mute_button.clicked.connect(self._on_mute_clicked)
        self._connect_button.clicked.connect(lambda: self.connect_requested.emit(self.room.room_id))
        self._record_button.clicked.connect(lambda: self.record_requested.emit(self.room.room_id))
        self._stop_button.clicked.connect(lambda: self.stop_requested.emit(self.room.room_id))
        self._remove_button.clicked.connect(lambda: self.remove_requested.emit(self.room.room_id))

    def _on_mute_clicked(self) -> None:
        muted = self._mute_button.isChecked()
        self.room.preview_muted = muted
        self.mute_toggled.emit(self.room.room_id, muted)

    def refresh(self) -> None:
        self._platform_label.setText(self.room.platform or "unknown")
        self._title_label.setText(
            self.room.stream_info.title if self.room.stream_info is not None else self.room.room_url
        )
        self._status_label.setText("已连接" if self.room.is_connected else (self.room.last_error or "未连接"))
        self._mute_button.setChecked(self.room.preview_muted)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.selected.emit(self.room.room_id)
        super().mousePressEvent(event)
```

- [ ] **Step 4: Run the RoomCard tests and verify they pass**

Run:

```powershell
$env:PYTHONPATH='.'
python -m pytest tests/gui/test_multi_room_page.py -q --no-cov
```

Expected: PASS with the two RoomCard tests green.

- [ ] **Step 5: Commit Task 3**

Run:

```powershell
git add lsc/gui/components/room_card.py tests/gui/test_multi_room_page.py
git commit -m "feat: add multi-room room card"
```

Expected: commit succeeds with the card component and tests.

---

### Task 4: MultiRoomPage

**Files:**
- Create: `lsc/gui/pages/multi_room.py`
- Modify: `tests/gui/test_multi_room_page.py`
- Modify: `tests/test_gui_page_reexports.py`
- Modify: `lsc/gui/pages/record_page.py`

- [ ] **Step 1: Add failing page tests**

Append to `tests/gui/test_multi_room_page.py`:

```python
def test_multi_room_page_adds_card_and_updates_summary(monkeypatch) -> None:
    _qapp()

    from lsc.gui.pages.multi_room import MultiRoomPage
    from lsc.gui.multi_room.session import RoomSession

    class FakeManager:
        def __init__(self):
            self.rooms = []

        def add_room(self, url: str):
            room = RoomSession(room_id=f"room-{len(self.rooms)+1}", room_url=url)
            self.rooms.append(room)
            return room

        def list_rooms(self):
            return list(self.rooms)

    page = MultiRoomPage(manager=FakeManager())
    page.add_room_from_url("https://live.douyin.com/123")

    assert page._grid_layout.count() == 1
    assert "1" in page._summary_label.text()


def test_multi_room_page_mute_action_updates_manager() -> None:
    _qapp()

    from lsc.gui.pages.multi_room import MultiRoomPage
    from lsc.gui.multi_room.session import RoomSession

    class FakeManager:
        def __init__(self):
            self.room = RoomSession(room_id="room-1", room_url="https://live.douyin.com/123")
            self.muted = []

        def list_rooms(self):
            return [self.room]

        def mute_room(self, room_id: str, muted: bool):
            self.muted.append((room_id, muted))

    manager = FakeManager()
    page = MultiRoomPage(manager=manager)
    page.load_rooms()

    card = page._cards["room-1"]
    card.mute_toggled.emit("room-1", False)

    assert manager.muted[-1] == ("room-1", False)
```

Append to `tests/test_gui_page_reexports.py`:

```python
def test_multi_room_page_is_reexported() -> None:
    from lsc.gui.pages.record_page import MultiRoomPage

    assert MultiRoomPage is not None
```

- [ ] **Step 2: Run the page tests and verify they fail**

Run:

```powershell
$env:PYTHONPATH='.'
python -m pytest tests/gui/test_multi_room_page.py tests/test_gui_page_reexports.py -q --no-cov
```

Expected: FAIL because `lsc.gui.pages.multi_room` and the re-export do not exist.

- [ ] **Step 3: Implement MultiRoomPage**

Create `lsc/gui/pages/multi_room.py`:

```python
"""Multi-room workbench page."""
from __future__ import annotations

from PySide6.QtWidgets import QGridLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from lsc.gui.components.room_card import RoomCard
from lsc.gui.multi_room import MultiRoomManager


class MultiRoomPage(QWidget):
    def __init__(self, manager: MultiRoomManager | None = None, parent=None):
        super().__init__(parent)
        self._manager = manager or MultiRoomManager()
        self._cards: dict[str, RoomCard] = {}
        self._build_ui()
        self.load_rooms()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        toolbar = QVBoxLayout()
        self._add_button = QPushButton("添加房间")
        self._batch_record_button = QPushButton("批量开始录制")
        self._batch_stop_button = QPushButton("批量停止录制")
        self._summary_label = QLabel("房间数: 0")
        toolbar.addWidget(self._add_button)
        toolbar.addWidget(self._batch_record_button)
        toolbar.addWidget(self._batch_stop_button)
        toolbar.addWidget(self._summary_label)
        root.addLayout(toolbar)

        self._grid_host = QWidget()
        self._grid_layout = QGridLayout(self._grid_host)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._grid_host)
        root.addWidget(scroll)

    def load_rooms(self) -> None:
        while self._grid_layout.count():
            item = self._grid_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._cards.clear()

        for index, room in enumerate(self._manager.list_rooms()):
            card = self._make_card(room)
            row, column = divmod(index, 2)
            self._grid_layout.addWidget(card, row, column)
            self._cards[room.room_id] = card
        self._refresh_summary()

    def add_room_from_url(self, url: str) -> None:
        room = self._manager.add_room(url)
        card = self._make_card(room)
        index = len(self._cards)
        row, column = divmod(index, 2)
        self._grid_layout.addWidget(card, row, column)
        self._cards[room.room_id] = card
        self._refresh_summary()

    def _make_card(self, room):
        card = RoomCard(room)
        card.mute_toggled.connect(self._manager.mute_room)
        return card

    def _refresh_summary(self) -> None:
        self._summary_label.setText(f"房间数: {len(self._cards)}")
```

Update `lsc/gui/pages/record_page.py` to re-export:

```python
from .multi_room import MultiRoomPage
```

- [ ] **Step 4: Run the page tests and verify they pass**

Run:

```powershell
$env:PYTHONPATH='.'
python -m pytest tests/gui/test_multi_room_page.py tests/test_gui_page_reexports.py -q --no-cov
```

Expected: PASS with the page tests green.

- [ ] **Step 5: Commit Task 4**

Run:

```powershell
git add lsc/gui/pages/multi_room.py lsc/gui/pages/record_page.py tests/gui/test_multi_room_page.py tests/test_gui_page_reexports.py
git commit -m "feat: add multi-room workbench page"
```

Expected: commit succeeds with the new page and tests.

---

### Task 5: Recording and Navigation Wiring

**Files:**
- Modify: `lsc/gui/pages/multi_room.py`
- Modify: `lsc/gui/components/room_card.py`
- Modify: `lsc/gui/multi_room/manager.py`
- Modify: `lsc/gui/pages/dashboard.py`
- Modify: `tests/gui/test_multi_room_page.py`
- Modify: `tests/test_multi_room_manager.py`

- [ ] **Step 1: Add failing wiring tests**

Append to `tests/test_multi_room_manager.py`:

```python
def test_manager_connect_room_passes_controller_input_args_from_stream_info(monkeypatch) -> None:
    from lsc.gui.multi_room.manager import MultiRoomManager
    from lsc.platforms.base import StreamInfo

    manager = MultiRoomManager(controller_factory=lambda: SimpleNamespace(stream_url="", input_args=[]))
    room = manager.add_room("https://live.douyin.com/123")

    monkeypatch.setattr(
        "lsc.gui.multi_room.manager.parse_stream",
        lambda url: StreamInfo(
            platform="douyin",
            room_url=url,
            stream_url="https://example.com/live.m3u8",
            is_live=True,
            quality_urls={"origin": "https://example.com/live.m3u8"},
            selected_quality="origin",
            headers={"Referer": "https://live.douyin.com/"},
        ),
    )

    manager.connect_room(room.room_id)

    assert room.controller.input_args == ["-headers", "Referer: https://live.douyin.com/\r\n"]
```

Append to `tests/gui/test_multi_room_page.py`:

```python
def test_multi_room_page_batch_recording_calls_manager(monkeypatch, tmp_path) -> None:
    _qapp()

    from lsc.gui.pages.multi_room import MultiRoomPage

    class FakeManager:
        def __init__(self):
            self.calls = []

        def list_rooms(self):
            return []

        def start_recording_all(self, output_dir: str, encoder: str, crf: int):
            self.calls.append((output_dir, encoder, crf))
            return {}

    manager = FakeManager()
    page = MultiRoomPage(manager=manager)
    page.start_recording_all(str(tmp_path), "Copy", 23)

    assert manager.calls == [(str(tmp_path), "Copy", 23)]
```

- [ ] **Step 2: Run the wiring tests and verify they fail**

Run:

```powershell
$env:PYTHONPATH='.'
python -m pytest tests/test_multi_room_manager.py tests/gui/test_multi_room_page.py -q --no-cov
```

Expected: FAIL because the extra controller input args and batch recording hook are not implemented.

- [ ] **Step 3: Implement the final wiring**

Modify `lsc/gui/multi_room/manager.py`:

```python
        if controller is not None:
            controller.stream_url = info.stream_url
            controller.input_args = info.to_legacy_dict().get("_inputArgs", [])
            room.selected_quality = info.selected_quality
```

Modify `lsc/gui/pages/multi_room.py` to add page-level helpers:

```python
    def start_recording_all(self, output_dir: str, encoder: str, crf: int) -> dict[str, bool]:
        return self._manager.start_recording_all(output_dir, encoder, crf)

    def stop_recording_all(self) -> dict[str, bool]:
        return self._manager.stop_recording_all()
```

Modify `lsc/gui/pages/dashboard.py` to add a visible entry point if it uses explicit navigation buttons, following the current dashboard pattern.

- [ ] **Step 4: Run the wiring tests and verify they pass**

Run:

```powershell
$env:PYTHONPATH='.'
python -m pytest tests/test_multi_room_manager.py tests/gui/test_multi_room_page.py -q --no-cov
```

Expected: PASS with all manager and page tests green.

- [ ] **Step 5: Run focused regressions**

Run:

```powershell
$env:PYTHONPATH='.'
python -m pytest tests/test_recording_controller_options.py tests/gui/test_record_interactions.py tests/test_gui_page_reexports.py -q --no-cov
```

Expected: PASS.

- [ ] **Step 6: Commit Task 5**

Run:

```powershell
git add lsc/gui/multi_room/manager.py lsc/gui/components/room_card.py lsc/gui/pages/multi_room.py lsc/gui/pages/dashboard.py tests/test_multi_room_manager.py tests/gui/test_multi_room_page.py
git commit -m "feat: wire multi-room workbench into gui"
```

Expected: commit succeeds with the end-to-end wiring.

---

### Task 6: Verification and Manual Smoke Checklist

**Files:**
- Modify: `docs/superpowers/plans/2026-06-13-multi-room-workbench-implementation.md` only if execution notes must be appended.

- [ ] **Step 1: Run compile checks**

Run:

```powershell
python -m compileall -q lsc tests
```

Expected: exits with code 0.

- [ ] **Step 2: Run all focused multi-room tests**

Run:

```powershell
$env:PYTHONPATH='.'
python -m pytest tests/test_multi_room_manager.py tests/gui/test_multi_room_page.py tests/test_recording_controller_options.py tests/gui/test_record_interactions.py tests/test_gui_page_reexports.py -q --no-cov
```

Expected: PASS.

- [ ] **Step 3: Run manual smoke for dynamic room cards**

Use the GUI and verify:

```text
1. Open the multi-room page.
2. Add two different room URLs.
3. Confirm two cards appear.
4. Confirm each card can be selected and removed independently.
```

Expected: the grid grows dynamically and the summary count updates correctly.

- [ ] **Step 4: Run manual smoke for per-room mute**

Verify:

```text
1. Connect previews for two rooms.
2. Mute room A.
3. Confirm room A preview is muted.
4. Confirm room B preview mute state is unchanged.
5. Start recording room A and stop it.
6. Confirm muting preview did not alter recording behavior.
```

Expected: mute is isolated to the room preview only.

- [ ] **Step 5: Run manual smoke for multi-room recording**

Verify:

```text
1. Add at least two rooms and connect them.
2. Start recording both rooms.
3. Confirm both rooms enter recording state.
4. Stop one room.
5. Confirm the other room keeps recording.
6. Use batch stop and confirm all active rooms stop cleanly.
```

Expected: recording state is isolated per room and batch stop is failure-isolated.

- [ ] **Step 6: Commit execution notes only if added**

If execution evidence was appended to this plan, run:

```powershell
git add docs/superpowers/plans/2026-06-13-multi-room-workbench-implementation.md
git commit -m "docs: record multi-room workbench verification"
```

Expected: commit only if the plan file changed.

---

## Self-Review

Spec coverage:

- Dynamic room cards are covered by Tasks 2 through 4.
- Per-room mute and isolated preview behavior are covered by Tasks 3, 4, and 6.
- Multi-room simultaneous recording is covered by Tasks 2, 5, and 6.
- Reuse of existing `RecordingController`, `MpvWidget`, and platform adapter paths is covered by Tasks 2 and 5.
- Non-goals such as unified timeline, batch clip cutting, drag sorting, persistence, and grouping are intentionally excluded.

Placeholder scan:

- The plan includes exact file paths, test code, implementation code, commands, expected failure states, expected pass states, and commit messages.

Type consistency:

- `RoomSession`, `MultiRoomManager`, `RoomCard`, and `MultiRoomPage` use consistent names and signatures across tasks.
- `start_recording_all(output_dir, encoder, crf)` and `stop_recording_all()` keep the same signature throughout the plan.
