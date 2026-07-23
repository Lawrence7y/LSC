# M1 / A1 — EventBus + RoomOrchestrator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增纯 Python `EventBus` + `RoomOrchestrator`，迁入 `MultiRoomManager` 全部非 Qt 业务逻辑；本阶段**不改** `manager.py` / `python-backend`（零切线）。

**Architecture:** Actor 模型：`queue.Queue` + 守护线程串行执行公开方法；心跳用 deadline 等待替代 QTimer；事件经 `EventBus` 同步派发（仅编排线程 `emit`）。

**Tech Stack:** Python 3.12、`threading` / `queue` / `concurrent.futures`、pytest（**不**设 `QT_QPA_PLATFORM`）

**Spec:** `docs/spec-deqt-orchestrator-analyzer-plugin.md` §阶段 A1  
**前置:** 无  
**后继:** M2（A2 薄壳）

**执行约束：** 工作目录 `D:\Project\直播切片多人`；**不要 git commit**（除非用户要求）。

---

## Spec 校正（以代码为准）

| Spec 原文 | 实际代码（必须保留） |
|-----------|----------------------|
| 心跳 1s / 5s / 10s | `_TICK_INTERVAL_MS = 3000`；medium 每 tick（房间 stagger `_STAGGER_GROUPS=3`）；low 每 4 ticks（≈12s） |
| QTimer 选区循环 | 50ms；编排线程 deadline 堆复刻 |

注释里写的 “1s/5s/10s” 是过期描述，**不要**按注释改频率。

---

## 文件结构

| 文件 | 职责 |
|------|------|
| `lsc/core/events.py` | `EventBus`：subscribe / unsubscribe / emit |
| `lsc/core/orchestrator.py` | `RoomOrchestrator` + 纯函数 helpers + worker 用 ThreadPool |
| `tests/test_event_bus.py` | EventBus 单元测试 |
| `tests/test_orchestrator.py` | orchestrator 无 Qt 测试 |
| `tests/test_orchestrator_event_parity.py` | 与旧 manager 事件序列对等（可选 Qt 侧） |

**本阶段不修改：** `lsc/gui/multi_room/manager.py`、`python-backend/**`

---

### Task 1: EventBus

**Files:**
- Create: `lsc/core/events.py`
- Test: `tests/test_event_bus.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_event_bus.py
from __future__ import annotations

import threading

import pytest

from lsc.core.events import EventBus


def test_subscribe_emit_delivers_args():
    bus = EventBus()
    seen: list[tuple] = []
    bus.subscribe("room_connect_finished", lambda *a: seen.append(a))
    bus.emit("room_connect_finished", "r1", True, "")
    assert seen == [("r1", True, "")]


def test_unsubscribe_stops_delivery():
    bus = EventBus()
    seen: list[int] = []
    def cb(*_a):
        seen.append(1)
    bus.subscribe("global_tick", cb)
    bus.unsubscribe("global_tick", cb)
    bus.emit("global_tick")
    assert seen == []


def test_callback_exception_does_not_stop_others(caplog):
    bus = EventBus()
    order: list[str] = []
    def bad(*_a):
        order.append("bad")
        raise RuntimeError("boom")
    def good(*_a):
        order.append("good")
    bus.subscribe("low_tick", bad)
    bus.subscribe("low_tick", good)
    bus.emit("low_tick")
    assert order == ["bad", "good"]


def test_emit_from_wrong_thread_raises_when_bound():
    bus = EventBus()
    bus.bind_emitter_thread(threading.current_thread())
    err: list[BaseException] = []
    def other():
        try:
            bus.emit("global_tick")
        except Exception as e:
            err.append(e)
    t = threading.Thread(target=other)
    t.start()
    t.join(2)
    assert err and isinstance(err[0], RuntimeError)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_event_bus.py -v`  
Expected: FAIL（`ModuleNotFoundError` / import error）

- [ ] **Step 3: 实现 EventBus**

```python
# lsc/core/events.py
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

_log = logging.getLogger(__name__)

# 事件名与 MultiRoomManager Signal 一一对应
EVENT_ROOM_CONNECT_FINISHED = "room_connect_finished"
EVENT_BATCH_RECORD_PROGRESS = "batch_record_progress"
EVENT_BATCH_RECORD_FINISHED = "batch_record_finished"
EVENT_RECORDING_STOPPED = "recording_stopped"
EVENT_GLOBAL_TICK = "global_tick"
EVENT_MEDIUM_TICK = "medium_tick"
EVENT_LOW_TICK = "low_tick"


class EventBus:
    """同步事件总线。emit 仅允许在编排线程调用（bind 后强制检查）。"""

    def __init__(self) -> None:
        self._subs: dict[str, list[Callable[..., Any]]] = {}
        self._lock = threading.RLock()
        self._emitter_thread: threading.Thread | None = None

    def bind_emitter_thread(self, thread: threading.Thread) -> None:
        self._emitter_thread = thread

    def subscribe(self, event: str, callback: Callable[..., Any]) -> None:
        with self._lock:
            self._subs.setdefault(event, []).append(callback)

    def unsubscribe(self, event: str, callback: Callable[..., Any]) -> None:
        with self._lock:
            cbs = self._subs.get(event)
            if not cbs:
                return
            try:
                cbs.remove(callback)
            except ValueError:
                return

    def emit(self, event: str, *args: Any) -> None:
        if self._emitter_thread is not None and threading.current_thread() is not self._emitter_thread:
            raise RuntimeError(
                f"EventBus.emit({event!r}) must run on orchestrator thread"
            )
        with self._lock:
            callbacks = list(self._subs.get(event, ()))
        for cb in callbacks:
            try:
                cb(*args)
            except Exception:
                _log.exception("EventBus subscriber failed for %s", event)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_event_bus.py -v`  
Expected: PASS

---

### Task 2: Orchestrator actor 骨架（call / submit / 队列）

**Files:**
- Create: `lsc/core/orchestrator.py`（骨架）
- Test: `tests/test_orchestrator.py`

- [ ] **Step 1: 写失败测试（call / submit / 同线程直执 / 堆积上限）**

```python
# tests/test_orchestrator.py
from __future__ import annotations

import threading
import time

import pytest

from lsc.core.orchestrator import RoomOrchestrator


@pytest.fixture
def orch():
    o = RoomOrchestrator(controller_factory=lambda: object(), preview_factory=lambda: object())
    o.start()
    yield o
    o.shutdown(timeout_sec=5.0)


def test_call_executes_on_orchestrator_thread(orch):
    tid_holder: list[int] = []
    def fn():
        tid_holder.append(threading.get_ident())
        return 42
    assert orch.call(fn) == 42
    assert tid_holder[0] == orch.thread_ident


def test_call_from_orchestrator_thread_runs_inline(orch):
    def nested():
        return orch.call(lambda: "inline")
    assert orch.call(nested) == "inline"


def test_submit_fire_and_forget(orch):
    done = threading.Event()
    orch.submit(lambda: done.set())
    assert done.wait(2.0)


def test_call_rejects_when_pending_full(orch):
    block = threading.Event()
    release = threading.Event()
    def blocker():
        block.set()
        release.wait(5)
    # 填满 pending（MAX=8）：先塞 8 个阻塞任务
    threads = []
    for _ in range(8):
        t = threading.Thread(target=lambda: orch.call(blocker, timeout=5.0))
        t.start()
        threads.append(t)
    assert block.wait(2.0)
    with pytest.raises(TimeoutError, match="too busy"):
        orch.call(lambda: None, timeout=1.0)
    release.set()
    for t in threads:
        t.join(5)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_orchestrator.py::test_call_executes_on_orchestrator_thread -v`  
Expected: FAIL import / missing API

- [ ] **Step 3: 实现骨架**

在 `lsc/core/orchestrator.py` 实现（完整可运行最小集）：

```python
from __future__ import annotations

import logging
import queue
import threading
import time
import traceback
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from lsc.core.events import EventBus

_log = logging.getLogger(__name__)

ControllerFactory = Callable[[], object]
PreviewFactory = Callable[[], object]

# 从 manager.py 原样复制常量（含 _TICK_INTERVAL_MS=3000 等）
MAX_ROOMS = 12
MAX_CONCURRENT_PREVIEWS = 4
_TICK_INTERVAL_MS = 3000
_HIGH_FREQ_INTERVAL = 1
_MEDIUM_FREQ_INTERVAL = 1
_LOW_FREQ_INTERVAL = 4
_STAGGER_GROUPS = 3
# ... 其余 reconnect / disk / URL 常量同 manager.py


class _CallRequest:
    def __init__(self, fn: Callable, args: tuple, kwargs: dict):
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.result: Any = None
        self.exception: BaseException | None = None
        self.traceback: str | None = None
        self.event = threading.Event()
        self.cancelled = False


class RoomOrchestrator:
    _MAX_PENDING_REQUESTS = 8

    def __init__(
        self,
        controller_factory: ControllerFactory | None = None,
        preview_factory: PreviewFactory | None = None,
    ) -> None:
        self._controller_factory = controller_factory
        self._preview_factory = preview_factory
        self.bus = EventBus()
        self._cmd_queue: queue.Queue[Any] = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._pending_count = 0
        self._pending_lock = threading.Lock()
        self._rooms: dict = {}
        self._lock = threading.RLock()
        self._tick_counter = 0
        self._next_tick_deadline: float | None = None
        self._loop_deadline: float | None = None  # 选区循环
        self._worker_pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="orch-worker")
        # connect/metadata/batch worker 状态 dict 同 manager

    @property
    def thread_ident(self) -> int | None:
        t = self._thread
        return t.ident if t else None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_loop, name="RoomOrchestrator", daemon=True)
        self._thread.start()
        # 等线程起来再 bind，避免竞态
        while self._thread.ident is None:
            time.sleep(0.001)
        self.bus.bind_emitter_thread(self._thread)

    def _run_loop(self) -> None:
        while not self._stop.is_set():
            timeout = self._compute_wait_timeout()
            try:
                item = self._cmd_queue.get(timeout=timeout)
            except queue.Empty:
                self._on_deadlines()
                continue
            if item is None:  # poison
                break
            if isinstance(item, _CallRequest):
                self._dispatch_request(item)
            elif callable(item):
                try:
                    item()
                except Exception:
                    _log.exception("orchestrator command failed")
            self._on_deadlines()

    def _compute_wait_timeout(self) -> float | None:
        now = time.monotonic()
        deadlines = [d for d in (self._next_tick_deadline, self._loop_deadline) if d is not None]
        if not deadlines:
            return 0.05  # idle poll
        return max(0.0, min(deadlines) - now)

    def _on_deadlines(self) -> None:
        now = time.monotonic()
        if self._next_tick_deadline is not None and now >= self._next_tick_deadline:
            self._on_global_tick()
            self._next_tick_deadline = now + (_TICK_INTERVAL_MS / 1000.0)
        if self._loop_deadline is not None and now >= self._loop_deadline:
            self._on_preview_loop_tick()
            self._loop_deadline = now + 0.05

    def _dispatch_request(self, req: _CallRequest) -> None:
        if req.cancelled:
            with self._pending_lock:
                self._pending_count -= 1
            return
        try:
            req.result = req.fn(*req.args, **req.kwargs)
        except Exception as exc:
            req.exception = exc
            req.traceback = traceback.format_exc()
            _log.error("orchestrator call raised", exc_info=True)
        finally:
            with self._pending_lock:
                self._pending_count -= 1
            req.event.set()

    def call(self, fn: Callable, *args, timeout: float = 10.0, **kwargs) -> Any:
        if self._thread and threading.current_thread() is self._thread:
            return fn(*args, **kwargs)
        with self._pending_lock:
            if self._pending_count >= self._MAX_PENDING_REQUESTS:
                raise TimeoutError(
                    f"orchestrator too busy ({self._pending_count} pending, "
                    f"max {self._MAX_PENDING_REQUESTS})"
                )
            self._pending_count += 1
        req = _CallRequest(fn, args, kwargs)
        self._cmd_queue.put(req)
        if not req.event.wait(timeout=timeout):
            req.cancelled = True
            raise TimeoutError("orchestrator call timed out")
        if req.exception is not None:
            raise req.exception
        return req.result

    def submit(self, fn: Callable, *args, **kwargs) -> None:
        if self._thread and threading.current_thread() is self._thread:
            try:
                fn(*args, **kwargs)
            except Exception:
                _log.exception("submit on orch thread failed")
            return
        self._cmd_queue.put(lambda: fn(*args, **kwargs))

    def _ensure_tick_armed(self) -> None:
        if self._next_tick_deadline is None:
            self._next_tick_deadline = time.monotonic() + (_TICK_INTERVAL_MS / 1000.0)

    def _disarm_tick_if_empty(self) -> None:
        with self._lock:
            empty = len(self._rooms) == 0
        if empty:
            self._next_tick_deadline = None

    def _on_global_tick(self) -> None:
        # Task 5 填满；骨架先 emit
        self.bus.emit("global_tick")

    def _on_preview_loop_tick(self) -> None:
        pass  # Task 5

    def shutdown(self, timeout_sec: float = 10.0) -> dict[str, int]:
        # 清空房间逻辑在 Task 4+；此处停线程
        self._stop.set()
        self._cmd_queue.put(None)
        if self._thread:
            self._thread.join(timeout=timeout_sec)
        self._worker_pool.shutdown(wait=False, cancel_futures=True)
        return {"rooms": 0, "recordings_stopped": 0, "previews_stopped": 0,
                "workers_cancelled": 0, "controllers_cleaned": 0, "previews_cleaned": 0}
```

公开房间 API 在后续 Task 用「编排线程内执行」模式包装：

```python
def add_room(self, url: str):
    return self.call(self._add_room_locked, url)

def _add_room_locked(self, url: str):
    ...  # 原 manager.add_room 体
```

或：对外方法体全部 `return self.call(lambda: self._impl_add_room(url))`。选定一种后全文件统一。

- [ ] **Step 4: 跑 Task 2 测试**

Run: `pytest tests/test_orchestrator.py -v -k "call or submit"`  
Expected: PASS（pending-full 测试若 flaky，可改为 stub 队列计数断言）

---

### Task 3: 迁入纯函数 helpers + 常量

**Files:** Modify `lsc/core/orchestrator.py`

- [ ] **Step 1:** 从 `lsc/gui/multi_room/manager.py` **原样复制**下列符号到 `orchestrator.py`（保持函数名，便于 A2 re-export）：

- `_OFFLINE_STREAM_ERROR_PATTERNS`
- `_is_stream_offline_error`
- `_offline_stream_error_message`
- `_is_stream_url_expiring`
- `_get_room_stream_url`
- `_room_stream_is_reusable`
- `_sync_controller_stream`
- `_heal_connected_flag`
- `_build_room_subdir`（及依赖）
- 全部 reconnect / disk / preview 常量

- [ ] **Step 2: 单测 offline helper**

```python
def test_is_stream_offline_error():
    from lsc.core.orchestrator import _is_stream_offline_error
    assert _is_stream_offline_error("直播间已结束")
    assert not _is_stream_offline_error("connection reset")
```

Run: `pytest tests/test_orchestrator.py::test_is_stream_offline_error -v`  
Expected: PASS

---

### Task 4: 迁入房间 CRUD + connect/preview/record 公共 API

**Files:** Modify `lsc/core/orchestrator.py`  
**参考源:** `lsc/gui/multi_room/manager.py`（公开方法列表见下方）

必须保留的公开方法（签名与 manager 一致）：

`add_room` / `get_room` / `list_rooms` / `room_count` / `max_rooms` / `remove_room` / `save_rooms` / `flush_save_rooms` / `load_rooms` / `connect_room` / `disconnect_room` / `get_active_preview_count` / `start_preview` / `play_preview_stream` / `pause_preview` / `resume_preview` / `stop_preview` / `set_preview_muted` / `seek_preview` / `get_preview_position` / `get_preview_duration` / `align_previews_to_live` / `start_range_loop` / `stop_range_loop` / `is_range_loop_active` / `seek_selected_previews` / `refresh_stream_url` / `refresh_stream_url_async` / `mute_room` / `start_recording` / `stop_recording` / `stop_recording_async` / `start_recording_all` / `start_recording_all_async` / `stop_recording_all` / `shutdown` / `start_export` / `cancel_export` / `get_rooms_for_cut` / `get_total_recording_size_mb`

- [ ] **Step 1: 迁移策略（机械，禁止「顺手优化」）**

1. 复制 `MultiRoomManager` 方法体到 `RoomOrchestrator`。
2. 替换：
   - `self.room_connect_finished.emit(...)` → `self.bus.emit("room_connect_finished", ...)`
   - 同理 `batch_record_*` / `recording_stopped` / `*_tick`
   - `QTimer` 启停 → `_ensure_tick_armed` / `_disarm_tick_if_empty` / `_loop_deadline`
   - `_ConnectWorker(QThread)` → 见 Task 5
3. `_create_controller` / `_create_preview` 逻辑保持 ImportError → None。
4. `self._lock = RLock()` 保护 `_rooms` 保持不变。

- [ ] **Step 2: CRUD 测试（无 Qt）**

```python
def test_add_get_remove_room(orch):
    r = orch.add_room("https://live.example/1")
    assert r is not None
    assert orch.room_count() == 1
    assert orch.get_room(r.room_id) is r
    assert orch.remove_room(r.room_id) is True
    assert orch.room_count() == 0


def test_max_rooms_cap(orch):
    from lsc.core.orchestrator import MAX_ROOMS
    for i in range(MAX_ROOMS):
        assert orch.add_room(f"https://live.example/{i}") is not None
    assert orch.add_room("https://live.example/extra") is None
```

Run: `pytest tests/test_orchestrator.py -v -k "add_get or max_rooms"`  
Expected: PASS

---

### Task 5: Workers → ThreadPoolExecutor + 心跳 / 选区循环

**Files:** Modify `lsc/core/orchestrator.py`

- [ ] **Step 1: Connect worker**

用对象 + `threading.Event` 替代 `QThread.requestInterruption`：

```python
class _ConnectJob:
    def __init__(self, room_id: str, url: str, quality_preset: str):
        self.room_id = room_id
        self.url = url
        self.quality_preset = quality_preset
        self.cancel = threading.Event()

    def run(self) -> tuple[str, bool, str, object | None]:
        if self.cancel.is_set():
            return self.room_id, False, "cancelled", None
        # parse_stream / select_quality — 复制 _ConnectWorker.run
        # 解析前后检查 self.cancel.is_set()
        ...
```

完成回调**必须** `orch.submit(...)` 回投编排队列，再在编排线程 `bus.emit("room_connect_finished", ...)`，禁止在 worker 线程直接 emit。

- [ ] **Step 2: MetadataProbe / BatchRecord / SizeUpdate** 同样模式：

- Batch：内层 `ThreadPoolExecutor(max_workers=min(4, n))` 保留；取消用 `cancel` Event 在提交前检查。
- SizeUpdate：提交到 `self._worker_pool`；缓存逻辑原样。

- [ ] **Step 3: `_on_global_tick` 完整迁入**

从 manager 复制 `_on_global_tick` 及 medium stagger / low disk / reconnect 逻辑；信号改为 `bus.emit`。

- [ ] **Step 4: 选区循环**

`start_range_loop`：无 native AB 时设 `_loop_deadline = monotonic()+0.05`；`_on_preview_loop_tick` 复制 50ms timer 回调体。

- [ ] **Step 5: 心跳频率测试**

```python
def test_tick_layers_emit(orch):
    global_n = medium_n = low_n = 0
    def g():
        nonlocal global_n; global_n += 1
    def m():
        nonlocal medium_n; medium_n += 1
    def l():
        nonlocal low_n; low_n += 1
    orch.bus.subscribe("global_tick", lambda: g())
    orch.bus.subscribe("medium_tick", lambda: m())
    orch.bus.subscribe("low_tick", lambda: l())
    orch.add_room("https://live.example/tick")
    # 直接驱动 4 次 tick（与 reconnect 测试同手法）
    for _ in range(4):
        orch.call(orch._on_global_tick)
    assert global_n == 4
    assert medium_n == 4  # _MEDIUM_FREQ_INTERVAL == 1
    assert low_n == 1     # 每 4 ticks
```

Run: `pytest tests/test_orchestrator.py::test_tick_layers_emit -v`  
Expected: PASS

---

### Task 6: 事件对等测试（manager vs orchestrator）

**Files:** Create `tests/test_orchestrator_event_parity.py`

- [ ] **Step 1: 场景脚本**

对两侧分别：`add_room` →（mock parse）`connect_room` 成功/失败 → `start_recording`（FakeController）→ `stop_recording` → 驱动若干 `_on_global_tick`。收集 `(event_name, args)` 列表。

```python
def _collect_events(bus_or_signals, is_qt: bool):
    ...
```

Qt manager 侧：仅在 `QT_QPA_PLATFORM=offscreen` 且 PySide6 可用时跑；否则 `pytest.importorskip("PySide6")` 或 skip。

Orchestrator 侧：无条件跑。

断言：同一 Fake 注入下，`room_connect_finished` / `recording_stopped` / tick 事件名与参数一致（允许 float 时间字段不在事件 args 内）。

- [ ] **Step 2:**

Run: `pytest tests/test_orchestrator_event_parity.py -v`  
Expected: PASS 或 skip（无 Qt）；orchestrator 半边必须绿

---

### Task 7: 回归确认本阶段零切线

- [ ] **Step 1:**

Run:

```bash
pytest tests/test_event_bus.py tests/test_orchestrator.py tests/test_orchestrator_event_parity.py -v
pytest tests/test_multi_room_manager.py tests/test_stability_guards.py tests/test_recording_reconnect_tick.py -v
```

Expected: 全部 PASS；manager 行为未改。

- [ ] **Step 2:** 确认无 backend import 变更：

```bash
rg "RoomOrchestrator|lsc.core.events" python-backend lsc/gui
```

Expected: 无匹配（A1 纯新增）。

- [ ] **Step 3:** 在 `docs/PROJECT_DESIGN.md` **暂不**改（等 M3）；可在本 plan 末尾记「M1 done」。

---

## M1 验收对照

| 验收项 | 命令 / 证据 |
|--------|-------------|
| `test_orchestrator.py` 绿 | 上方 pytest |
| 事件对等 | `test_orchestrator_event_parity.py` |
| 心跳频率与代码常量一致 | `test_tick_layers_emit` |
| 不依赖 Qt | orchestrator 测试无 `QT_QPA_PLATFORM` |
| manager / backend 未改 | git diff 仅新增文件 |

---

## 回滚

删除 `lsc/core/events.py`、`lsc/core/orchestrator.py`、对应 tests 即可。
