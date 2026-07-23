# M3 / A3 — python-backend 拆除 Qt 桥接 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `python-backend` 进程零 PySide6；删除 `QtManagerBridge`；广播机制迁入 `broadcast_hub.py`；handler 改为 `orchestrator.call`。

**Architecture:** `RoomOrchestrator` 自带编排线程；`BroadcastHub` 只保留队列广播；事件订阅改为 `bus.subscribe`。WebSocket 消息 payload **字节级不变**。

**Tech Stack:** asyncio WebSocket、threading Event、RoomOrchestrator

**Spec:** `docs/spec-deqt-orchestrator-analyzer-plugin.md` §阶段 A3  
**前置:** M2 完成（薄壳可用；本阶段 backend 直接用 orchestrator）  
**后继:** M4（分析插件协议）

**执行约束：** 工作目录 `D:\Project\直播切片多人`；**不要 git commit**（除非用户要求）。

---

## 文件结构

| 文件 | 动作 |
|------|------|
| `python-backend/broadcast_hub.py` | **新建**：从 message_bridge 抽出纯广播 + 事件订阅 |
| `python-backend/message_bridge.py` | **删除**或薄兼容 shim（优先删除，测试改 import） |
| `python-backend/main.py` | 去 QApplication / Qt handler |
| `python-backend/server.py` | import / 启动改线 |
| `python-backend/start.py` | 同上 |
| `python-backend/handlers/room_handler.py` | `bridge.call`→`orchestrator.call`；Signal.connect→bus.subscribe |
| `tests/test_message_bridge*.py` | 改测 BroadcastHub |
| `tests/test_drain_merge_broadcasts.py` | 确认仍绿 |
| `docs/PROJECT_DESIGN.md` | 更新跨线程章节；已知问题 #2 标已消除 |

---

### Task 1: `BroadcastHub`（无 Qt）

**Files:**
- Create: `python-backend/broadcast_hub.py`
- Modify tests later in Task 4

- [ ] **Step 1: 从 `message_bridge.py` 搬移并去 Qt**

保留原样：

- `_TERMINAL_TYPES` / `_DROPPABLE_TYPES`
- `_enqueue_preserving_terminal` / `_expand_broadcast_queue`
- `queue_broadcast` / `get_broadcast` / `bind_async_wake` / `notify_broadcast`
- 回调 `_on_connect_finished` / `_on_batch_record_progress` / `_on_recording_stopped` 的 **payload 构造**

删除：

- `QObject` / `Signal` / `_execute` / `call` / `submit` / `_CallRequest` / `_pending_*`

新 API：

```python
# python-backend/broadcast_hub.py
from __future__ import annotations

import logging
import queue
import threading
from typing import Any

_log = logging.getLogger(__name__)

_TERMINAL_TYPES = frozenset({...})  # 原样
_DROPPABLE_TYPES = frozenset({...})  # 原样


class BroadcastHub:
    """纯 Python 广播队列 + manager/orchestrator 事件订阅。"""

    def __init__(self, orchestrator: Any):
        self._orch = orchestrator
        self.manager = orchestrator  # 兼容旧代码 bridge.manager 读法；可随后全局改名
        self._broadcast_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1000)
        self._wake = threading.Event()
        self._async_loop = None
        self._async_wake = None
        self._subscribe_orchestrator_events()

    def _subscribe_orchestrator_events(self) -> None:
        bus = self._orch.bus
        bus.subscribe("room_connect_finished", self._on_connect_finished)
        bus.subscribe("batch_record_progress", self._on_batch_record_progress)
        bus.subscribe("recording_stopped", self._on_recording_stopped)

    # _on_* / queue_broadcast / get_broadcast / notify_broadcast / bind_async_wake
    # 从 message_bridge 原样粘贴（payload 不得改字段）
```

**兼容别名（降低改动面）：** 在模块底部或 `message_bridge.py` 暂留：

```python
# 可选过渡：python-backend/message_bridge.py
from broadcast_hub import BroadcastHub as QtManagerBridge  # noqa: F401
```

M3 结束前删除该 shim，测试全部改 import。

- [ ] **Step 2: 单元测试先改一版指向 BroadcastHub**

把 `tests/test_message_bridge.py` 中「Signal connect / call / timeout」用例：

- **删除或改写** `call` / Qt Signal 相关断言
- **保留** `queue_broadcast` 满队列、droppable drop、terminal 保留（见 `test_message_bridge_terminal.py`）

最小保留测试示例：

```python
def test_queue_and_get():
    class FakeOrch:
        def __init__(self):
            from lsc.core.events import EventBus
            self.bus = EventBus()
    hub = BroadcastHub(FakeOrch())
    hub.queue_broadcast({"type": "rooms_updated", "data": {}})
    assert hub.get_broadcast()["type"] == "rooms_updated"
```

Run: `pytest tests/test_message_bridge_terminal.py -v`（先让 terminal 测改完能跑）

---

### Task 2: `main.py` / `server.py` / `start.py` 去 Qt

**Files:** `python-backend/main.py`, `server.py`, `start.py`

- [ ] **Step 1: `main.py` 启动序列改为**

```python
from broadcast_hub import BroadcastHub
from lsc.core.orchestrator import RoomOrchestrator

class LSCWebSocketBackend:
    def __init__(self):
        self.manager = RoomOrchestrator()  # 属性名可保留 manager 减少 handler churn
        self.manager.start()
        self.bridge = BroadcastHub(self.manager)  # 属性名 bridge 暂留，兼容 register_room_handlers
        self.server = LSCWebSocketServer(...)

    def start(self):
        # WS 线程：register_room_handlers(server, bridge); broadcast coroutine; server.start()
        # 主线程：不再 app.exec()；改为 wait 在 Event 或 join WS + 处理 KeyboardInterrupt
        self._stop_event = threading.Event()
        ...
        self._stop_event.wait()  # 或类似阻塞

    def stop(self):
        shutdown_room_handlers(...)
        self.manager.shutdown()
        ...
```

删除：

- `from PySide6...`
- `QApplication`
- `_install_qt_message_handler` 调用（可删整个函数）

- [ ] **Step 2: `server.py` / `start.py` 同步**

同样：`RoomOrchestrator` + `BroadcastHub`；去掉 `QApplication` / `app.exec()`。

- [ ] **Step 3: 验证无 PySide6**

```bash
rg "PySide6" python-backend/
```

Expected: **无匹配**（测试文件若在 repo 根 `tests/` 仍可 import PySide6 测 GUI 壳，那是 `lsc/gui`，不是 `python-backend/`）。

---

### Task 3: `room_handler.py` 机械替换

**Files:** `python-backend/handlers/room_handler.py`

- [ ] **Step 1: import**

```python
# 旧
from lsc.gui.multi_room.manager import MultiRoomManager, _is_stream_offline_error
# 新
from lsc.core.orchestrator import RoomOrchestrator, _is_stream_offline_error
```

类型注解 `MultiRoomManager` → `RoomOrchestrator`（或 `Any` 过渡）。

- [ ] **Step 2: `bridge.call` → 编排 call**

所有：

```python
bridge.call(fn, ...)
```

改为：

```python
bridge.manager.call(fn, ...)   # BroadcastHub.manager == RoomOrchestrator
# 或更清晰：在 register 时解构
orch = bridge.manager
orch.call(fn, ...)
```

`timeout=` 关键字保持兼容（orchestrator.call 已支持）。

统计：约 15+ 处；用

```bash
rg -n "bridge\.call" python-backend/handlers/room_handler.py
```

逐个替换，禁止漏改。

- [ ] **Step 3: Signal.connect → bus.subscribe**

在 `register_room_handlers`（约 3119–3178）把：

```python
manager.room_connect_finished.connect(_queue_rooms_update)
manager.batch_record_progress.connect(_queue_rooms_update)
manager.batch_record_finished.connect(_queue_rooms_update)
manager.medium_tick.connect(_queue_recording_size_patches)
manager.low_tick.connect(_queue_rooms_update)
manager.recording_stopped.connect(_on_manager_recording_stopped_offline)
manager.low_tick.connect(lambda: _broadcast_system_stats())
```

改为：

```python
bus = manager.bus
bus.subscribe("room_connect_finished", lambda *_a: _queue_rooms_update())
bus.subscribe("batch_record_progress", lambda *_a: _queue_rooms_update())
bus.subscribe("batch_record_finished", lambda *_a: _queue_rooms_update())
bus.subscribe("medium_tick", lambda: _queue_recording_size_patches())
bus.subscribe("low_tick", lambda: _queue_rooms_update())
bus.subscribe("recording_stopped", _on_manager_recording_stopped_offline_adapted)
bus.subscribe("low_tick", lambda: _broadcast_system_stats())
```

**适配 `_on_manager_recording_stopped_offline`：** 原 Qt 槽签名 `(room_id, reason, message)`。若现实现是绑在 Signal 上的函数，改为：

```python
def _on_manager_recording_stopped_offline(room_id: str, reason: str, message: str) -> None:
    ...  # 原逻辑，reason=='offline' → file MSE
```

**纪律：** 这些订阅者回调在编排线程同步执行 → **禁止**在回调里再 `orch.call` 写操作（重入）；重活继续丢到现有 executor（与今日 bridge 回调只 `queue_broadcast` / 轻量调度一致）。

- [ ] **Step 4: 双订阅注意**

`BroadcastHub` 已订 `room_connect_finished` / `batch_record_progress` / `recording_stopped` 做 WS 广播；handler 再订同一事件做 `rooms_updated` —— 与今日「bridge + handler 双 connect」同构，保留两边。

---

### Task 4: 广播与 drain 测试全绿

- [ ] **Step 1:**

```bash
pytest tests/test_message_bridge.py tests/test_message_bridge_terminal.py tests/test_drain_merge_broadcasts.py -v
```

Expected: PASS（测试已改指向 BroadcastHub）。

- [ ] **Step 2: 相关 handler / manager 回归**

```bash
pytest tests/test_multi_room_manager.py tests/test_stability_guards.py tests/test_recording_reconnect_tick.py tests/test_orchestrator.py -v
```

- [ ] **Step 3: 删除 `message_bridge.py`（若仍有 shim）并再跑一次 grep**

```bash
rg "QtManagerBridge|message_bridge|PySide6" python-backend/
```

Expected: 空。

---

### Task 5: 文档 + 冒烟

- [ ] **Step 1: 更新 `docs/PROJECT_DESIGN.md`**

- 第三部分跨线程通信：改为 Orchestrator actor + BroadcastHub（不再写 Qt 信号桥）。
- 第十八部分已知问题 #2（`bridge.call` 超时乱序）：标记 **已消除**（架构移除根因），附 M3 日期。

- [ ] **Step 2: Electron 全链路人工冒烟**

`cd lsc-electron && npm run dev`（或既有启动方式）：

1. 后端日志无 PySide6 / QApplication
2. 添加房间 → 连接 → 预览 MSE → 录制 → 停止 → 导出一点
3. 确认 `rooms_updated` / `recording_started` / `recording_stopped` 前端仍正常

- [ ] **Step 3: 记录体积（可选）**

若从安装依赖去掉 PySide6：记录 `pip show PySide6` 前后 wheel 体积或 `site-packages` 差；**不强制**改 `requirements.txt` 是否移除（GUI 壳 `lsc/gui` 可能仍需）—— 仅 backend 运行时不 import 即可。`requirements.txt` 是否拆 optional 列为 follow-up，本 plan 不强制。

---

## M3 验收对照

| 验收项 | 证据 |
|--------|------|
| `python-backend` 无 PySide6 import | `rg` |
| 广播测试全绿 | pytest 列表 |
| Electron 全链路冒烟 | 人工清单 |
| 已知问题 #2 文档已标消除 | PROJECT_DESIGN |
| WS payload 形状不变 | 冒烟 + 既有 frontend 兼容 |

---

## 回滚

`git revert` M3 commits；或恢复 `message_bridge.py` + main 启动 Qt 循环。M1/M2 文件可保留。
