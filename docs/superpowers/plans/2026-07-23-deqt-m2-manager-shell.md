# M2 / A2 — MultiRoomManager 薄 Qt 壳 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `MultiRoomManager` 退化为 ~200 行 Qt 壳：内部持有 `RoomOrchestrator`，Signal 仅转发 `EventBus`；存量 manager 测试**零改断言**全绿。

**Architecture:** 委托模式。业务在 orchestrator；壳保留类名 / Signal / 构造签名，供 `lsc/gui` 与现有测试继续使用。

**Tech Stack:** PySide6（仅壳）、RoomOrchestrator（M1）

**Spec:** `docs/spec-deqt-orchestrator-analyzer-plugin.md` §阶段 A2  
**前置:** M1 完成（`lsc/core/orchestrator.py` 公共 API 齐备）  
**后继:** M3（后端去 Qt）

**执行约束：** 工作目录 `D:\Project\直播切片多人`；**不要 git commit**（除非用户要求）。

---

## 文件结构

| 文件 | 职责 |
|------|------|
| `lsc/gui/multi_room/manager.py` | 重写为薄壳（删业务，留委托 + Signal） |
| `lsc/core/orchestrator.py` | 可能补 `_on_global_tick` 可测入口（若壳需暴露） |
| `tests/test_multi_room_manager.py` 等 | **不改断言**；必要时仅改 import 纯函数路径（优先 re-export） |

---

### Task 1: 壳类骨架 + re-export

**Files:** Rewrite `lsc/gui/multi_room/manager.py`

- [ ] **Step 1: 备份对照**

确认 M1 orchestrator 已有全部公开方法。用：

```bash
rg -n "^\s+def " lsc/gui/multi_room/manager.py lsc/core/orchestrator.py
```

两侧公开 `def` 集合对齐后再改壳。

- [ ] **Step 2: 写新壳（目标结构）**

```python
"""Multi-room manager — thin Qt shell over RoomOrchestrator."""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, Signal

from lsc.core.orchestrator import (
    MAX_CONCURRENT_PREVIEWS,
    MAX_ROOMS,
    RoomOrchestrator,
    _is_stream_offline_error,  # re-export for room_handler
    # 其他被外部 import 的符号一并 re-export
)
from lsc.gui.multi_room.session import RoomSession  # 若测试仍从 manager 间接依赖

_log = logging.getLogger(__name__)

ControllerFactory = Callable[[], object]
PreviewFactory = Callable[[], object]

__all__ = [
    "MultiRoomManager",
    "MAX_ROOMS",
    "MAX_CONCURRENT_PREVIEWS",
    "_is_stream_offline_error",
]


class MultiRoomManager(QObject):
    room_connect_finished = Signal(str, bool, str)
    batch_record_progress = Signal(str, bool)
    batch_record_finished = Signal(int, int)
    recording_stopped = Signal(str, str, str)
    global_tick = Signal()
    medium_tick = Signal()
    low_tick = Signal()

    def __init__(
        self,
        controller_factory: ControllerFactory | None = None,
        preview_factory: PreviewFactory | None = None,
    ) -> None:
        super().__init__()
        self._orch = RoomOrchestrator(
            controller_factory=controller_factory,
            preview_factory=preview_factory,
        )
        self._orch.start()
        # 测试与 reconnect 用例依赖这些属性名
        self._lock = self._orch._lock
        self._rooms = self._orch._rooms  # 或 property 转发
        self._tick_counter = 0  # 见 Task 2：转发到 orch
        self._wire_bus()

    def _wire_bus(self) -> None:
        b = self._orch.bus
        b.subscribe("room_connect_finished", lambda *a: self.room_connect_finished.emit(*a))
        b.subscribe("batch_record_progress", lambda *a: self.batch_record_progress.emit(*a))
        b.subscribe("batch_record_finished", lambda *a: self.batch_record_finished.emit(*a))
        b.subscribe("recording_stopped", lambda *a: self.recording_stopped.emit(*a))
        b.subscribe("global_tick", lambda: self.global_tick.emit())
        b.subscribe("medium_tick", lambda: self.medium_tick.emit())
        b.subscribe("low_tick", lambda: self.low_tick.emit())

    # ── 委托全部公共方法 ──
    def add_room(self, url: str):
        return self._orch.add_room(url)

    def get_room(self, room_id: str):
        return self._orch.get_room(room_id)

    def list_rooms(self):
        return self._orch.list_rooms()

    def room_count(self) -> int:
        return self._orch.room_count()

    def max_rooms(self) -> int:
        return self._orch.max_rooms()

    def remove_room(self, room_id: str) -> bool:
        return self._orch.remove_room(room_id)

    # ... 其余公开方法一一委托（完整列表见 M1 Task 4）...

    def shutdown(self, timeout_sec: float = 10.0) -> dict[str, int]:
        return self._orch.shutdown(timeout_sec=timeout_sec)

    def _on_global_tick(self) -> None:
        """存量测试直接调用；转发到编排线程。"""
        return self._orch.call(self._orch._on_global_tick)
```

**要点：**

- `room_handler` 仍 `from lsc.gui.multi_room.manager import MultiRoomManager, _is_stream_offline_error` —— **re-export 必须保留**。
- 删除文件中全部 QThread worker / QTimer / 业务方法体。
- 文件目标约 150–250 行。

- [ ] **Step 3: 同步内部属性**

`test_recording_reconnect_tick.py` 可能读 `manager_module._MEDIUM_FREQ_INTERVAL`、调用 `mgr._on_global_tick`、访问 reconnect 状态。在壳上：

```python
# 模块级 re-export 常量
from lsc.core.orchestrator import (
    _MEDIUM_FREQ_INTERVAL,
    _TICK_INTERVAL_MS,
    _LOW_FREQ_INTERVAL,
    _STAGGER_GROUPS,
)
```

若测试访问 `mgr._attempt_recording_reconnect`，壳上增加：

```python
def _attempt_recording_reconnect(self, *a, **kw):
    return self._orch.call(lambda: self._orch._attempt_recording_reconnect(*a, **kw))
```

用一次全量跑测发现缺口，缺啥补啥委托（不要改测试断言）。

---

### Task 2: Signal 转发线程纪律

**Files:** `manager.py` 壳 + 必要时 `orchestrator.py`

- [ ] **Step 1: 理解约束**

EventBus `emit` 在编排线程同步调用订阅者。壳的订阅者做的是 `Signal.emit`。Qt Signal 跨线程默认 QueuedConnection 到壳所在线程（通常是创建 QObject 的线程）。

**风险：** 单元测试无 QApp 时，Signal 可能同步或无效。存量 `test_multi_room_manager` 多数不 assert Signal，而是 assert 房间状态 —— 应仍绿。

- [ ] **Step 2: 若 Signal 在无 QApp 下炸**

捕获并降级：

```python
def _safe_emit(sig: Signal, *args):
    try:
        sig.emit(*args)
    except RuntimeError:
        _log.debug("signal emit skipped (no receiver / no app)")
```

或仅在 `QCoreApplication.instance()` 存在时 emit。**禁止**在订阅者里再 `orch.call`（会死锁）。

- [ ] **Step 3: 冒烟测试 Signal 转发（有 Qt 时）**

```python
# tests/test_manager_shell_signals.py
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
import sys
app = QApplication.instance() or QApplication(sys.argv)

from lsc.gui.multi_room.manager import MultiRoomManager

def test_bus_forwards_connect_finished():
    mgr = MultiRoomManager(controller_factory=lambda: None, preview_factory=lambda: None)
    seen = []
    mgr.room_connect_finished.connect(lambda *a: seen.append(a))
    mgr._orch.bus.emit("room_connect_finished", "r1", True, "")
    # 若 Queued，需要 processEvents
    app.processEvents()
    assert seen == [("r1", True, "")]
    mgr.shutdown()
```

注意：`emit` 只允许编排线程 —— 测试应 `mgr._orch.call(lambda: mgr._orch.bus.emit(...))`。

Run: `pytest tests/test_manager_shell_signals.py -v`  
Expected: PASS

---

### Task 3: 存量测试零改断言全绿

- [ ] **Step 1:**

```bash
set QT_QPA_PLATFORM=offscreen
pytest tests/test_multi_room_manager.py tests/test_stability_guards.py tests/test_recording_reconnect_tick.py -v
```

Expected: PASS。若失败：只改壳委托 / 暴露私有方法转发，**不改测试期望值**。

- [ ] **Step 2: 确认 `hasattr(mgr, "_lock")` 等 stability 断言仍成立**

`test_stability_guards` 依赖 `_lock` —— Task 1 已映射到 `self._orch._lock`。

- [ ] **Step 3: orchestrator 自身测试仍绿**

```bash
pytest tests/test_event_bus.py tests/test_orchestrator.py -v
```

---

### Task 4: PySide6 GUI 手动冒烟清单（人工）

- [ ] 若本机仍启动旧 GUI：添加房间 → 连接 → 录制 → 停止。  
Electron 路径可放到 M3 做全链路；本阶段至少保证 **manager 单测 + shell signal 测** 绿。

---

## M2 验收对照

| 验收项 | 证据 |
|--------|------|
| manager 相关存量测试零改断言全绿 | 上方 pytest |
| `manager.py` 约 ≤250 行、无业务 FFmpeg 逻辑 | `wc -l` / 目视 |
| `_is_stream_offline_error` 仍可从 manager import | `python -c "from lsc.gui.multi_room.manager import _is_stream_offline_error"` |
| Signal 仍存在且转发 | `test_manager_shell_signals.py` |

---

## 回滚

`git checkout -- lsc/gui/multi_room/manager.py`；删除 `test_manager_shell_signals.py`。
