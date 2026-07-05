# 四项功能修复与增强 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复录制异常恢复机制的缺陷、新增系统资源监控、实现 Electron 原生通知系统、修复设置项缺失的 Bug 并完善配置。

**Architecture:** 分为 4 个独立子系统，每个可单独实施和测试。后端改动集中在 `manager.py`、`session.py`、`room_handler.py` 和新增的 `resource_monitor.py`；前端改动集中在 `useWebSocket.ts`、`appStore.ts`、`types/index.ts`、`MainLayout.tsx` 和新增的 `SystemMonitor.tsx`、`useNotifications.ts`；Electron 主进程改动集中在 `main.ts` 和 `preload.ts`。

**Tech Stack:** Python (PySide6, psutil) / TypeScript (React, Zustand, antd) / Electron 28

---

## 文件结构总览

### 新增文件

| 文件 | 职责 |
|------|------|
| `lsc/core/services/resource_monitor.py` | 后端系统资源采集（CPU/内存/磁盘） |
| `lsc-electron/src/components/Layout/SystemMonitor.tsx` | 前端侧边栏迷你资源监控组件 |
| `lsc-electron/src/hooks/useNotifications.ts` | 前端通知决策层 hook |

### 修改文件

| 文件 | 改动内容 |
|------|----------|
| `requirements.txt` | 添加 `psutil>=5.9` |
| `lsc/gui/multi_room/session.py` | 新增重连参数字段 + `is_reconnecting` 状态 |
| `lsc/gui/multi_room/manager.py` | 声明 `low_tick` 信号、重连保存完整参数、重连状态广播 |
| `lsc/config.py` | 修复分辨率格式兼容（支持 `x` 和 `:` 分隔符） |
| `python-backend/handlers/room_handler.py` | 系统资源采集 WebSocket handler、修复默认编码器、修复导出分辨率、接入 preset |
| `lsc-electron/src/types/index.ts` | 新增 `SystemStats`/`NotificationSettings` 类型，扩展 `ElectronAPI` |
| `lsc-electron/src/store/appStore.ts` | 新增 `systemStats`/`exportProgress`/`notificationSettings` 状态 |
| `lsc-electron/src/hooks/useWebSocket.ts` | 接入 `export_progress`、新增 `system_stats` 监听 |
| `lsc-electron/src/components/Layout/MainLayout.tsx` | 插入 `SystemMonitor` 组件 |
| `lsc-electron/src/pages/Settings/index.tsx` | 新增 preset 选项、Intel/AMD 编码器选项、通知设置 |
| `lsc-electron/src/pages/Workbench/index.tsx` | 系统通知触发 |
| `lsc-electron/electron/main.ts` | Notification IPC、flashFrame、setProgressBar、setAppUserModelId、backend-error 暴露 |
| `lsc-electron/electron/preload.ts` | 暴露通知 IPC 接口 |

---

## 子系统 1：录制异常恢复机制修复

### Task 1: 声明 `low_tick` 信号（修复 AttributeError）

**Files:**
- Modify: `lsc/gui/multi_room/manager.py:312-325`

**问题：** `manager.py:1843` 调用 `self.low_tick.emit()`，但信号声明区（312-325 行）没有 `low_tick = Signal()` 声明，每 10 秒触发 `AttributeError`。

- [ ] **Step 1: 添加信号声明**

在 `manager.py` 的 `MultiRoomManager` 类信号定义区，`medium_tick` 信号之后、`align_finished` 信号之前，添加 `low_tick` 信号声明。

将这段代码：

```python
    # Emitted on low-frequency ticks (every 30s) when a recording room has
    # low disk space, so the UI can surface a warning before FFmpeg dies.
    # (Reserved for future use; currently stop_recording handles this inline.)
    # Emitted when audio cross-correlation alignment completes.
    align_finished = Signal(dict)  # result dict
```

替换为：

```python
    # Emitted on low-frequency ticks (every 10s) for disk space checks
    # and other low-frequency monitoring tasks.
    low_tick = Signal()
    # Emitted when audio cross-correlation alignment completes.
    align_finished = Signal(dict)  # result dict
```

- [ ] **Step 2: 验证**

Run: `python -c "from lsc.gui.multi_room.manager import MultiRoomManager; print('OK')"`
Expected: `OK` (无 AttributeError)

- [ ] **Step 3: Commit**

```bash
git add lsc/gui/multi_room/manager.py
git commit -m "fix: 声明 low_tick 信号修复 AttributeError"
```

---

### Task 2: 新增重连参数字段和 `is_reconnecting` 状态

**Files:**
- Modify: `lsc/gui/multi_room/session.py:66-84`

**问题：** 重连时只保存了 `output_dir`/`encoder`/`crf`/`param_mode`/`bitrate`/`bitrate_unit`，未保存 `resolution`/`framerate`/`audio_bitrate`，导致重连后丢失这些设置。同时没有 `is_reconnecting` 状态字段，前端无法区分"已停止"和"重连中"。

- [ ] **Step 1: 添加新字段到 RoomSession**

在 `session.py` 的 `RoomSession` dataclass 中，将这段代码：

```python
    # 自动重连状态
    reconnect_next_attempt_at: float = 0.0
    reconnect_attempts: int = 0
    reconnect_output_dir: str = ""
    reconnect_encoder: str = ""
    reconnect_crf: int = 23
    reconnect_param_mode: str = "CRF 质量"
    reconnect_bitrate: str = ""
    reconnect_bitrate_unit: str = "kbps"
```

替换为：

```python
    # 自动重连状态
    is_reconnecting: bool = False
    reconnect_next_attempt_at: float = 0.0
    reconnect_attempts: int = 0
    reconnect_output_dir: str = ""
    reconnect_encoder: str = ""
    reconnect_crf: int = 23
    reconnect_param_mode: str = "CRF 质量"
    reconnect_bitrate: str = ""
    reconnect_bitrate_unit: str = "kbps"
    reconnect_resolution: str = ""
    reconnect_framerate: str = ""
    reconnect_audio_bitrate: str = ""
```

- [ ] **Step 2: 更新 `status_text` 方法以显示重连状态**

将 `session.py` 的 `status_text` 方法：

```python
    def status_text(self) -> str:
        """生成当前房间状态的简短文本描述，供 UI 状态栏展示。"""
        parts: list[str] = []
        if self.is_connecting:
            parts.append("连接中")
        elif self.is_recording:
            parts.append("录制中")
        elif self.is_connected:
            parts.append("已连接")
        else:
            parts.append("未连接")
```

替换为：

```python
    def status_text(self) -> str:
        """生成当前房间状态的简短文本描述，供 UI 状态栏展示。"""
        parts: list[str] = []
        if self.is_reconnecting:
            parts.append(f"重连中({self.reconnect_attempts})")
        elif self.is_connecting:
            parts.append("连接中")
        elif self.is_recording:
            parts.append("录制中")
        elif self.is_connected:
            parts.append("已连接")
        else:
            parts.append("未连接")
```

- [ ] **Step 3: 验证**

Run: `python -c "from lsc.gui.multi_room.session import RoomSession; r = RoomSession(room_id='x', room_url='y'); print(r.is_reconnecting, r.reconnect_resolution, r.reconnect_framerate, r.reconnect_audio_bitrate)"`
Expected: `False     `

- [ ] **Step 4: Commit**

```bash
git add lsc/gui/multi_room/session.py
git commit -m "feat: 新增 is_reconnecting 状态和重连参数字段"
```

---

### Task 3: 重连保存完整参数并设置重连状态

**Files:**
- Modify: `lsc/gui/multi_room/manager.py:1419-1431`（保存参数）
- Modify: `lsc/gui/multi_room/manager.py:1616-1723`（重连逻辑）

**问题：** `start_recording` 成功时只保存了 6 个重连参数，未保存 `resolution`/`framerate`/`audio_bitrate`。重连调用 `start_recording` 时也只传了 6 个参数。

- [ ] **Step 1: 在录制成功时保存完整重连参数**

将 `manager.py:1419-1428` 的代码：

```python
        if ok:
            # Save recording params for auto-reconnect
            room.reconnect_output_dir = room_output_dir
            room.reconnect_encoder = encoder
            room.reconnect_crf = crf
            room.reconnect_param_mode = param_mode
            room.reconnect_bitrate = bitrate or ""
            room.reconnect_bitrate_unit = bitrate_unit
            room.reconnect_attempts = 0
            room.reconnect_next_attempt_at = 0.0
```

替换为：

```python
        if ok:
            # Save recording params for auto-reconnect
            room.reconnect_output_dir = room_output_dir
            room.reconnect_encoder = encoder
            room.reconnect_crf = crf
            room.reconnect_param_mode = param_mode
            room.reconnect_bitrate = bitrate or ""
            room.reconnect_bitrate_unit = bitrate_unit
            room.reconnect_resolution = resolution or ""
            room.reconnect_framerate = framerate or ""
            room.reconnect_audio_bitrate = audio_bitrate or ""
            room.reconnect_attempts = 0
            room.reconnect_next_attempt_at = 0.0
```

- [ ] **Step 2: 在重连方法中设置 `is_reconnecting` 并传递完整参数**

将 `manager.py:1633-1647` 的不可恢复和超限分支：

```python
        # Check if error is recoverable
        if not is_recoverable_error(error_msg):
            room.last_error = error_msg
            room.is_recording = False
            room.record_started_at = None
            _log.warning("Room %s non-recoverable error: %s", room.room_id, error_msg)
            return

        if room.reconnect_attempts >= _MAX_RECONNECT_ATTEMPTS:
            room.last_error = error_msg
            room.is_recording = False
            room.record_started_at = None
            _log.warning("Room %s reconnect exhausted (%d attempts), giving up",
                         room.room_id, room.reconnect_attempts)
            return
```

替换为：

```python
        # Check if error is recoverable
        if not is_recoverable_error(error_msg):
            room.last_error = error_msg
            room.is_recording = False
            room.is_reconnecting = False
            room.record_started_at = None
            _log.warning("Room %s non-recoverable error: %s", room.room_id, error_msg)
            return

        if room.reconnect_attempts >= _MAX_RECONNECT_ATTEMPTS:
            room.last_error = error_msg
            room.is_recording = False
            room.is_reconnecting = False
            room.record_started_at = None
            _log.warning("Room %s reconnect exhausted (%d attempts), giving up",
                         room.room_id, room.reconnect_attempts)
            return
```

- [ ] **Step 3: 在重连调度阶段设置 `is_reconnecting = True`**

将 `manager.py:1655-1660` 的首次调度分支：

```python
        if room.reconnect_next_attempt_at <= 0:
            room.reconnect_next_attempt_at = _time.monotonic() + delay
            room.last_error = f"{error_msg}，{delay:.0f}秒后尝试恢复..."
            _log.info("Room %s scheduling reconnect attempt %d/%d (delay=%.1fs)",
                      room.room_id, room.reconnect_attempts + 1, _MAX_RECONNECT_ATTEMPTS, delay)
            return
```

替换为：

```python
        if room.reconnect_next_attempt_at <= 0:
            room.reconnect_next_attempt_at = _time.monotonic() + delay
            room.is_reconnecting = True
            room.last_error = f"{error_msg}，{delay:.0f}秒后尝试恢复..."
            _log.info("Room %s scheduling reconnect attempt %d/%d (delay=%.1fs)",
                      room.room_id, room.reconnect_attempts + 1, _MAX_RECONNECT_ATTEMPTS, delay)
            return
```

- [ ] **Step 4: 在重连调用时传递完整参数**

将 `manager.py:1699-1707` 的重连调用：

```python
        ok = self.start_recording(
            room.room_id,
            room.reconnect_output_dir,
            room.reconnect_encoder,
            room.reconnect_crf,
            param_mode=room.reconnect_param_mode,
            bitrate=room.reconnect_bitrate,
            bitrate_unit=room.reconnect_bitrate_unit,
        )
```

替换为：

```python
        ok = self.start_recording(
            room.room_id,
            room.reconnect_output_dir,
            room.reconnect_encoder,
            room.reconnect_crf,
            param_mode=room.reconnect_param_mode,
            bitrate=room.reconnect_bitrate,
            bitrate_unit=room.reconnect_bitrate_unit,
            resolution=room.reconnect_resolution or None,
            framerate=room.reconnect_framerate or None,
            audio_bitrate=room.reconnect_audio_bitrate or None,
        )
```

- [ ] **Step 5: 重连成功/失败时更新 `is_reconnecting`**

将 `manager.py:1708-1723` 的成功/失败处理：

```python
        if ok:
            _log.info("Room %s reconnect succeeded", room.room_id)
            room.reconnect_attempts = 0
            room.reconnect_next_attempt_at = 0.0
        else:
            _log.warning("Room %s reconnect attempt %d failed: %s",
                         room.room_id, room.reconnect_attempts, room.last_error)
            # 保留原始错误信息
            if not room.last_error or room.last_error == "录制启动失败":
                room.last_error = f"恢复失败（原始错误: {original_error}）"
            # Calculate next delay with exponential backoff
            next_delay = min(
                _RECONNECT_DELAY_SEC * (_RECONNECT_BACKOFF_FACTOR ** room.reconnect_attempts),
                _RECONNECT_MAX_DELAY_SEC,
            )
            room.reconnect_next_attempt_at = _time.monotonic() + next_delay
```

替换为：

```python
        if ok:
            _log.info("Room %s reconnect succeeded", room.room_id)
            room.reconnect_attempts = 0
            room.reconnect_next_attempt_at = 0.0
            room.is_reconnecting = False
        else:
            _log.warning("Room %s reconnect attempt %d failed: %s",
                         room.room_id, room.reconnect_attempts, room.last_error)
            # 保留原始错误信息
            if not room.last_error or room.last_error == "录制启动失败":
                room.last_error = f"恢复失败（原始错误: {original_error}）"
            # Calculate next delay with exponential backoff
            next_delay = min(
                _RECONNECT_DELAY_SEC * (_RECONNECT_BACKOFF_FACTOR ** room.reconnect_attempts),
                _RECONNECT_MAX_DELAY_SEC,
            )
            room.reconnect_next_attempt_at = _time.monotonic() + next_delay
            room.is_reconnecting = True
```

- [ ] **Step 6: 停止录制时清除 `is_reconnecting`**

将 `manager.py:1448-1451` 的 stop_recording 中的重连状态清理：

```python
        room.is_recording = False
        room.record_started_at = None
        room.reconnect_attempts = 0
        room.reconnect_next_attempt_at = 0.0
```

替换为：

```python
        room.is_recording = False
        room.is_reconnecting = False
        room.record_started_at = None
        room.reconnect_attempts = 0
        room.reconnect_next_attempt_at = 0.0
```

- [ ] **Step 7: 在 `_room_to_dict` 中序列化 `is_reconnecting`**

在 `python-backend/handlers/room_handler.py:386` 的 `_room_to_dict` 函数中，将：

```python
        'is_recording': room.is_recording,
```

替换为：

```python
        'is_recording': room.is_recording,
        'is_reconnecting': getattr(room, 'is_reconnecting', False),
```

- [ ] **Step 8: 前端类型新增 `is_reconnecting`**

在 `lsc-electron/src/types/index.ts:11` 的 `RoomSession` 接口中，将：

```typescript
  is_recording: boolean
```

替换为：

```typescript
  is_recording: boolean
  is_reconnecting?: boolean
```

- [ ] **Step 9: 验证**

Run: `python -c "from lsc.gui.multi_room.manager import MultiRoomManager; print('OK')"`

- [ ] **Step 10: Commit**

```bash
git add lsc/gui/multi_room/manager.py python-backend/handlers/room_handler.py lsc-electron/src/types/index.ts
git commit -m "fix: 重连保存完整参数并新增 is_reconnecting 状态"
```

---

## 子系统 2：系统资源监控

### Task 4: 添加 psutil 依赖并实现资源采集模块

**Files:**
- Modify: `requirements.txt`
- Create: `lsc/core/services/resource_monitor.py`

- [ ] **Step 1: 添加 psutil 到 requirements.txt**

在 `requirements.txt` 的 `numpy` 行之后、注释行之前，添加：

```
# System resource monitoring (CPU, memory, disk)
psutil>=5.9,<7
```

- [ ] **Step 2: 创建 resource_monitor.py**

创建文件 `lsc/core/services/resource_monitor.py`：

```python
"""系统资源监控模块。

通过 psutil 采集 CPU、内存、磁盘使用率，供后端心跳周期性调用，
并通过 WebSocket 广播到前端。
"""
from __future__ import annotations

import logging
import shutil
import os

_log = logging.getLogger(__name__)

try:
    import psutil
    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False
    _log.warning("psutil not available, system resource monitoring disabled")


def collect_system_stats(output_dir: str = "") -> dict:
    """采集系统资源快照。

    Parameters
    ----------
    output_dir : str
        录制输出目录路径，用于查询磁盘使用率。

    Returns
    -------
    dict
        包含 cpu_percent, memory_percent, memory_total_gb,
        memory_used_gb, disk_percent, disk_total_gb, disk_free_gb 的字典。
        若 psutil 不可用则返回 cpu_percent=-1 等哨兵值。
    """
    if not _HAS_PSUTIL:
        return {
            "cpu_percent": -1.0,
            "memory_percent": -1.0,
            "memory_total_gb": 0.0,
            "memory_used_gb": 0.0,
            "disk_percent": -1.0,
            "disk_total_gb": 0.0,
            "disk_free_gb": 0.0,
        }

    cpu_percent = psutil.cpu_percent(interval=None)

    mem = psutil.virtual_memory()
    memory_total_gb = round(mem.total / (1024 ** 3), 1)
    memory_used_gb = round(mem.used / (1024 ** 3), 1)
    memory_percent = round(mem.percent, 1)

    disk_percent = -1.0
    disk_total_gb = 0.0
    disk_free_gb = 0.0
    if output_dir:
        try:
            if not os.path.exists(output_dir):
                os.makedirs(output_dir, exist_ok=True)
            total, used, free = shutil.disk_usage(output_dir)
            disk_total_gb = round(total / (1024 ** 3), 1)
            disk_free_gb = round(free / (1024 ** 3), 1)
            disk_percent = round(used / total * 100, 1) if total > 0 else 0.0
        except Exception as exc:
            _log.debug("Disk usage query failed: %s", exc)

    return {
        "cpu_percent": cpu_percent,
        "memory_percent": memory_percent,
        "memory_total_gb": memory_total_gb,
        "memory_used_gb": memory_used_gb,
        "disk_percent": disk_percent,
        "disk_total_gb": disk_total_gb,
        "disk_free_gb": disk_free_gb,
    }
```

- [ ] **Step 3: 验证**

Run: `pip install psutil>=5.9 && python -c "from lsc.core.services.resource_monitor import collect_system_stats; s = collect_system_stats('C:/'); print(s)"`
Expected: 打印包含各资源字段的字典，cpu_percent >= 0

- [ ] **Step 4: Commit**

```bash
git add requirements.txt lsc/core/services/resource_monitor.py
git commit -m "feat: 新增 psutil 依赖和系统资源采集模块"
```

---

### Task 5: 后端心跳中采集资源并广播

**Files:**
- Modify: `python-backend/handlers/room_handler.py`

- [ ] **Step 1: 导入 resource_monitor 并添加 WebSocket handler**

在 `room_handler.py` 的导入区（约行 1-20 的 import 块之后），添加：

```python
from lsc.core.services.resource_monitor import collect_system_stats
```

- [ ] **Step 2: 在 WebSocket 消息路由中添加 `get_system_stats` handler**

在 `room_handler.py` 的 WebSocket 消息路由区域（搜索 `elif msg_type == 'get_disk_usage'` 附近），在该 handler 之后添加：

```python
        elif msg_type == 'get_system_stats':
            settings = load_settings()
            output_dir = _expand_user_path(settings.get('output_dir', ''))
            stats = collect_system_stats(output_dir)
            return {'type': 'system_stats', 'data': stats}
```

- [ ] **Step 3: 在低频心跳中自动广播 system_stats**

在 `room_handler.py` 中搜索 `_queue_rooms_update` 或低频心跳相关的广播逻辑。在低频心跳触发 `rooms_updated` 广播的代码之后，添加系统资源广播：

```python
            # 低频心跳：广播系统资源快照
            try:
                settings = load_settings()
                output_dir = _expand_user_path(settings.get('output_dir', ''))
                stats = collect_system_stats(output_dir)
                asyncio.run_coroutine_threadsafe(
                    server.broadcast('system_stats', stats),
                    loop,
                )
            except Exception as exc:
                _log.debug("System stats broadcast failed: %s", exc)
```

这段代码应放在 `_on_global_tick` 的低频心跳处理路径中，即与磁盘空间检查同一段落。如果找不到合适位置，在 `_queue_rooms_update` 函数之后添加一个独立的 `_broadcast_system_stats` 函数：

```python
def _broadcast_system_stats():
    """广播系统资源快照到前端。"""
    try:
        settings = load_settings()
        output_dir = _expand_user_path(settings.get('output_dir', ''))
        stats = collect_system_stats(output_dir)
        asyncio.run_coroutine_threadsafe(
            server.broadcast('system_stats', stats),
            loop,
        )
    except Exception as exc:
        _log.debug("System stats broadcast failed: %s", exc)
```

然后在 manager 的 `low_tick` 信号连接处调用它。在 `room_handler.py` 中搜索 `medium_tick` 的连接，在其旁边添加：

```python
    manager.low_tick.connect(lambda: _broadcast_system_stats())
```

- [ ] **Step 4: 验证**

Run: `python -c "from python-backend.handlers.room_handler import _broadcast_system_stats; print('OK')" || echo "Module check via import"`
Expected: 无导入错误

- [ ] **Step 5: Commit**

```bash
git add python-backend/handlers/room_handler.py
git commit -m "feat: 后端心跳广播系统资源快照"
```

---

### Task 6: 前端 Store 新增 systemStats 状态

**Files:**
- Modify: `lsc-electron/src/store/appStore.ts`
- Modify: `lsc-electron/src/types/index.ts`

- [ ] **Step 1: 在 types/index.ts 新增 SystemStats 接口**

在 `types/index.ts` 的 `DiskUsage` 接口之后（约行 10 之后），添加：

```typescript
export interface SystemStats {
  cpu_percent: number
  memory_percent: number
  memory_total_gb: number
  memory_used_gb: number
  disk_percent: number
  disk_total_gb: number
  disk_free_gb: number
}
```

- [ ] **Step 2: 在 appStore.ts 新增 systemStats 状态**

在 `appStore.ts` 的 `AppState` 接口中，`diskUsage` 之后添加：

```typescript
  systemStats: SystemStats | null
```

在 `AppActions` 接口中，`setDiskUsage` 之后添加：

```typescript
  setSystemStats: (stats: SystemStats | null) => void
```

在 import 行中将 `SystemStats` 加入导入：

```typescript
import { RoomSession, ClipSegment, RecordSettings, AppSettings, DependencyStatus, SystemStats } from '@/types'
```

在 store 初始状态中添加：

```typescript
  diskUsage: null,
  systemStats: null,
```

在 actions 中添加：

```typescript
  setSystemStats: (systemStats) => set({ systemStats }),
```

- [ ] **Step 3: 验证**

Run: `cd lsc-electron && npx tsc --noEmit`
Expected: 无类型错误

- [ ] **Step 4: Commit**

```bash
git add lsc-electron/src/store/appStore.ts lsc-electron/src/types/index.ts
git commit -m "feat: 前端 Store 新增 systemStats 状态"
```

---

### Task 7: 前端监听 system_stats 事件并创建 SystemMonitor 组件

**Files:**
- Modify: `lsc-electron/src/hooks/useWebSocket.ts`
- Create: `lsc-electron/src/components/Layout/SystemMonitor.tsx`
- Modify: `lsc-electron/src/components/Layout/MainLayout.tsx`

- [ ] **Step 1: 在 useWebSocket.ts 新增 system_stats 监听**

在 `useWebSocket.ts` 的 `_attachSharedWebSocketHandlers` 函数中，`disk_usage` 监听之后（约行 222 之后），添加：

```typescript
  const handleSystemStats = (data: any) => {
    if (data && typeof data.cpu_percent === 'number') {
      useAppStore.getState().setSystemStats({
        cpu_percent: data.cpu_percent,
        memory_percent: data.memory_percent,
        memory_total_gb: data.memory_total_gb,
        memory_used_gb: data.memory_used_gb,
        disk_percent: data.disk_percent,
        disk_total_gb: data.disk_total_gb,
        disk_free_gb: data.disk_free_gb,
      })
    }
  }
  const unsubSystemStats = wsClient.on('system_stats', handleSystemStats)
```

在连接成功时（约行 128 `wsClient.send('get_disk_usage', {})` 旁边），添加：

```typescript
    wsClient.send('get_system_stats', {})
```

在返回的 cleanup 函数中添加 `unsubSystemStats()`。

- [ ] **Step 2: 创建 SystemMonitor.tsx 组件**

创建文件 `lsc-electron/src/components/Layout/SystemMonitor.tsx`：

```tsx
import { useAppStore } from '@/store/appStore'

function ResourceBar({ label, percent, color }: { label: string; percent: number; color: string }) {
  const isOverload = percent > 85
  const barColor = isOverload ? 'var(--state-error-dark)' : color
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6, width: '100%', fontSize: 11 }}>
      <span style={{ width: 28, color: 'var(--text-tertiary)', flexShrink: 0 }}>{label}</span>
      <div style={{
        flex: 1,
        height: 4,
        borderRadius: 2,
        background: 'var(--bg-tertiary)',
        overflow: 'hidden',
      }}>
        <div style={{
          width: `${Math.min(100, Math.max(0, percent))}%`,
          height: '100%',
          borderRadius: 2,
          background: barColor,
          transition: 'width 0.5s ease, background 0.3s ease',
        }} />
      </div>
      <span style={{
        width: 32,
        textAlign: 'right',
        color: isOverload ? 'var(--state-error-dark)' : 'var(--text-secondary)',
        flexShrink: 0,
        fontVariantNumeric: 'tabular-nums',
      }}>
        {percent >= 0 ? `${Math.round(percent)}%` : '--'}
      </span>
    </div>
  )
}

export default function SystemMonitor() {
  const systemStats = useAppStore((state) => state.systemStats)

  if (!systemStats) return null

  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      gap: 4,
      padding: '6px 8px',
      borderRadius: 6,
      background: 'var(--bg-tertiary)',
      border: '1px solid var(--border-default)',
      width: '100%',
    }}>
      <ResourceBar label="CPU" percent={systemStats.cpu_percent} color="var(--brand-500)" />
      <ResourceBar label="内存" percent={systemStats.memory_percent} color="var(--state-warning-dark)" />
      <ResourceBar label="磁盘" percent={systemStats.disk_percent} color="var(--state-success-dark)" />
    </div>
  )
}
```

- [ ] **Step 3: 在 MainLayout.tsx 中插入 SystemMonitor 组件**

在 `MainLayout.tsx` 的导入区添加：

```typescript
import SystemMonitor from './SystemMonitor'
```

在 `MainLayout.tsx` 的 Footer 区域（约行 208，连接状态指示器 `</div>` 之后，重连按钮之前），插入：

```tsx
          <SystemMonitor />
```

即在 `MainLayout.tsx` 中，这段代码：

```tsx
          </div>
          {/* 重连按钮：仅在断开/失败时显示 */}
```

之间插入 `<SystemMonitor />`。

- [ ] **Step 4: 验证**

Run: `cd lsc-electron && npx tsc --noEmit`
Expected: 无类型错误

- [ ] **Step 5: Commit**

```bash
git add lsc-electron/src/hooks/useWebSocket.ts lsc-electron/src/components/Layout/SystemMonitor.tsx lsc-electron/src/components/Layout/MainLayout.tsx
git commit -m "feat: 前端侧边栏系统资源监控组件"
```

---

## 子系统 3：Electron 原生通知系统

### Task 8: 主进程实现通知 IPC

**Files:**
- Modify: `lsc-electron/electron/main.ts`

- [ ] **Step 1: 导入 Notification 并设置 AppUserModelId**

在 `main.ts:1` 的 import 语句中，将：

```typescript
import { app, BrowserWindow, ipcMain, dialog, shell, Tray, Menu, nativeImage } from 'electron'
```

替换为：

```typescript
import { app, BrowserWindow, ipcMain, dialog, shell, Tray, Menu, nativeImage, Notification } from 'electron'
```

在 `main.ts` 的 `app.whenReady()` 回调中（搜索 `app.whenReady()`，在 `createWindow()` 调用之前），添加：

```typescript
  if (process.platform === 'win32') {
    app.setAppUserModelId('com.lsc.app')
  }
```

- [ ] **Step 2: 注册通知 IPC handler**

在 `main.ts` 的 `registerWindowIpc()` 函数末尾（约行 676 `}` 之前），添加：

```typescript
  // 系统通知
  ipcMain.handle('show-notification', (_event, payload: {
    title: string
    body: string
    silent?: boolean
  }) => {
    if (!Notification.isSupported()) return
    // 窗口聚焦时跳过系统通知（antd message 已处理）
    if (mainWindow?.isFocused()) return
    const notif = new Notification({
      title: payload.title,
      body: payload.body,
      icon: path.join(__dirname, '../../assets/icon.ico'),
      silent: payload.silent ?? false,
    })
    notif.on('click', () => {
      mainWindow?.show()
      mainWindow?.focus()
    })
    notif.show()
    // 任务栏闪烁
    if (mainWindow && !mainWindow.isFocused()) {
      mainWindow.flashFrame(true)
      mainWindow.once('focus', () => mainWindow.flashFrame(false))
    }
  })

  // 任务栏进度条
  ipcMain.handle('set-progress-bar', (_event, progress: number) => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.setProgressBar(progress)
    }
  })

  // 托盘动态状态
  ipcMain.handle('set-tray-state', (_event, state: 'idle' | 'recording' | 'error') => {
    if (!tray) return
    const iconPath = path.join(__dirname, '../../assets/icon.ico')
    try {
      const img = nativeImage.createFromPath(iconPath)
      if (!img.isEmpty()) tray.setImage(img)
    } catch {
      // ignore
    }
  })

  // backend-error 前端监听桥接
  ipcMain.handle('get-backend-error', () => {
    return pythonDetectError
  })
```

- [ ] **Step 3: Commit**

```bash
git add lsc-electron/electron/main.ts
git commit -m "feat: Electron 主进程通知 IPC（Notification/flashFrame/setProgressBar）"
```

---

### Task 9: preload.ts 暴露通知接口

**Files:**
- Modify: `lsc-electron/electron/preload.ts`

- [ ] **Step 1: 在 electronAPI 中新增通知接口**

在 `preload.ts` 的 `contextBridge.exposeInMainWorld('electronAPI', {` 块末尾（`removeUpdateStatusListeners` 之后，`})` 之前），添加：

```typescript
  // 系统通知
  showNotification: (payload: { title: string; body: string; silent?: boolean }) =>
    ipcRenderer.invoke('show-notification', payload),
  setProgressBar: (progress: number) =>
    ipcRenderer.invoke('set-progress-bar', progress),
  setTrayState: (state: 'idle' | 'recording' | 'error') =>
    ipcRenderer.invoke('set-tray-state', state),
  getBackendError: () =>
    ipcRenderer.invoke('get-backend-error'),
  onBackendError: (callback: (error: string) => void) => {
    ipcRenderer.on('backend-error', (_event, error) => callback(error))
  },
```

- [ ] **Step 2: 更新前端 ElectronAPI 类型定义**

在 `types/index.ts` 的 `ElectronAPI` 接口中，`removeUpdateStatusListeners` 之后（约行 127 `}` 之前），添加：

```typescript
  showNotification?: (payload: { title: string; body: string; silent?: boolean }) => Promise<void>
  setProgressBar?: (progress: number) => Promise<void>
  setTrayState?: (state: 'idle' | 'recording' | 'error') => Promise<void>
  getBackendError?: () => Promise<string | null>
  onBackendError?: (callback: (error: string) => void) => void
```

- [ ] **Step 3: 验证**

Run: `cd lsc-electron && npx tsc --noEmit`
Expected: 无类型错误

- [ ] **Step 4: Commit**

```bash
git add lsc-electron/electron/preload.ts lsc-electron/src/types/index.ts
git commit -m "feat: preload 暴露通知 IPC 接口"
```

---

### Task 10: 前端通知决策层 hook

**Files:**
- Create: `lsc-electron/src/hooks/useNotifications.ts`
- Modify: `lsc-electron/src/hooks/useWebSocket.ts`
- Modify: `lsc-electron/src/pages/Workbench/index.tsx`

- [ ] **Step 1: 创建 useNotifications.ts**

创建文件 `lsc-electron/src/hooks/useNotifications.ts`：

```typescript
import { useEffect, useRef } from 'react'
import { wsClient } from '@/services/websocket'
import { useAppStore } from '@/store/appStore'

interface NotificationPayload {
  title: string
  body: string
  silent?: boolean
}

const TRIGGERS: Record<string, (data: any) => NotificationPayload | null> = {
  clip_completed: (d) => ({
    title: '切片导出完成',
    body: `${d.room_name || '房间'} 切片已就绪`,
  }),
  clip_failed: (d) => ({
    title: '切片导出失败',
    body: d.error || '未知错误',
  }),
  recording_started: (d) => d.success
    ? { title: '录制已开始', body: d.room_name || '直播间', silent: true }
    : { title: '录制启动失败', body: d.error || '未知错误' },
  room_connect_finished: (d) => d.success
    ? null
    : { title: '房间连接失败', body: d.error || '连接失败' },
  reconnect_failed: () => ({
    title: '后端连接断开',
    body: 'WebSocket 重连失败，请检查后端状态',
  }),
}

export function useNotifications() {
  const unsubsRef = useRef<(() => void)[]>([])

  useEffect(() => {
    const triggers = Object.keys(TRIGGERS)

    for (const event of triggers) {
      const handler = (data: any) => {
        const factory = TRIGGERS[event]
        const payload = factory(data)
        if (!payload) return
        // 窗口聚焦时不弹系统通知
        if (document.hasFocus()) return
        window.electronAPI?.showNotification?.(payload)
      }
      unsubsRef.current.push(wsClient.on(event, handler))
    }

    // backend-error 监听
    if (window.electronAPI?.onBackendError) {
      window.electronAPI.onBackendError((error) => {
        if (error) {
          window.electronAPI?.showNotification?.({
            title: '后端启动失败',
            body: error,
          })
        }
      })
    }

    return () => {
      unsubsRef.current.forEach((fn) => fn())
      unsubsRef.current = []
    }
  }, [])
}
```

- [ ] **Step 2: 在 App.tsx 中调用 useNotifications**

在 `App.tsx` 的根组件中（在 `useWebSocket()` 调用附近），添加：

```typescript
import { useNotifications } from '@/hooks/useNotifications'
```

在组件函数体中添加：

```typescript
  useNotifications()
```

- [ ] **Step 3: 接入 export_progress 到任务栏进度条**

在 `useWebSocket.ts` 中，将空的 `export_progress` handler（约行 251-253）：

```typescript
    unsubs.push(on('export_progress', () => {
      // 进度更新可通过 toast 或 ClipList 中的进度指示器展示
    }))
```

替换为：

```typescript
    unsubs.push(on('export_progress', (data: { percent?: number; job_id?: string }) => {
      if (data?.percent !== undefined) {
        const progress = Math.max(0, Math.min(1, data.percent / 100))
        window.electronAPI?.setProgressBar?.(progress)
        // 更新 store 中的导出进度
        useAppStore.getState().setExportProgress({
          job_id: data.job_id || '',
          percent: data.percent,
        })
      }
    }))
```

- [ ] **Step 4: 在 appStore.ts 新增 exportProgress 状态**

在 `appStore.ts` 的 `AppState` 接口中添加：

```typescript
  exportProgress: { job_id: string; percent: number } | null
```

在 `AppActions` 中添加：

```typescript
  setExportProgress: (progress: { job_id: string; percent: number } | null) => void
```

在初始状态中添加：

```typescript
  exportProgress: null,
```

在 actions 中添加：

```typescript
  setExportProgress: (exportProgress) => set({ exportProgress }),
```

在 `useWebSocket.ts` 的 `_attachSharedWebSocketHandlers` 中也添加 `export_progress` 的全局监听，在 `clip_completed` 监听之后：

```typescript
  const unsubExportProgress = wsClient.on('export_progress', (data: any) => {
    if (data?.percent !== undefined) {
      const progress = Math.max(0, Math.min(1, data.percent / 100))
      window.electronAPI?.setProgressBar?.(progress)
    }
  })
```

并在 cleanup 中调用 `unsubExportProgress()`。

导出完成后清除进度条。在 `clip_completed` 和 `clip_failed` 的 handler 中添加：

```typescript
    window.electronAPI?.setProgressBar?.(-1)
```

- [ ] **Step 5: 录制状态切换托盘图标**

在 `useWebSocket.ts` 的 `rooms_updated` handler 中，在 `useAppStore.getState().setRooms(data.rooms)` 之后，添加托盘状态同步：

```typescript
      // 根据录制状态切换托盘图标
      const anyRecording = data.rooms.some((r: any) => r.is_recording)
      const anyError = data.rooms.some((r: any) => r.last_error && !r.is_recording)
      if (anyError) {
        window.electronAPI?.setTrayState?.('error')
      } else if (anyRecording) {
        window.electronAPI?.setTrayState?.('recording')
      } else {
        window.electronAPI?.setTrayState?.('idle')
      }
```

- [ ] **Step 6: 验证**

Run: `cd lsc-electron && npx tsc --noEmit`
Expected: 无类型错误

- [ ] **Step 7: Commit**

```bash
git add lsc-electron/src/hooks/useNotifications.ts lsc-electron/src/hooks/useWebSocket.ts lsc-electron/src/store/appStore.ts lsc-electron/src/App.tsx
git commit -m "feat: 前端通知决策层+导出进度条+托盘状态同步"
```

---

## 子系统 4：设置项修复

### Task 11: 修复导出分辨率格式不匹配 Bug

**Files:**
- Modify: `lsc/config.py:50-60`

**问题：** `settings.json` 和导出预设使用冒号分隔的分辨率（如 `"1920:1080"`），但 `ExportProfile.__post_init__` 用 `.split("x")` 验证，导致分辨率被静默清空。

- [ ] **Step 1: 修复 ExportProfile 分辨率验证逻辑**

将 `config.py:50-60` 的分辨率验证：

```python
        # 分辨率格式验证
        if self.resolution:
            parts = self.resolution.split("x", 1)
            if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
                _log.warning("Invalid resolution format: %s, clearing", self.resolution)
                self.resolution = ""
            else:
                w, h = int(parts[0]), int(parts[1])
                if w <= 0 or h <= 0 or w > 7680 or h > 4320:
                    _log.warning("Resolution out of range: %s, clearing", self.resolution)
                    self.resolution = ""
```

替换为：

```python
        # 分辨率格式验证（兼容 "1920x1080" 和 "1920:1080" 两种分隔符）
        if self.resolution:
            normalized = self.resolution.replace(":", "x")
            parts = normalized.split("x", 1)
            if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
                _log.warning("Invalid resolution format: %s, clearing", self.resolution)
                self.resolution = ""
            else:
                w, h = int(parts[0]), int(parts[1])
                if w <= 0 or h <= 0 or w > 7680 or h > 4320:
                    _log.warning("Resolution out of range: %s, clearing", self.resolution)
                    self.resolution = ""
                else:
                    self.resolution = normalized
```

- [ ] **Step 2: 在 room_handler.py 导出处也做格式归一化**

在 `room_handler.py:1374` 的 `ExportProfile` 构造之前（约行 1373），添加分辨率归一化：

将 `room_handler.py:1329-1383` 中构造 `ExportProfile` 的代码段，找到 `resolution=resolution,` 行，在其之前添加：

```python
            # 分辨率格式归一化：将 "1920:1080" 转为 "1920x1080"
            if resolution and ":" in resolution:
                resolution = resolution.replace(":", "x")
```

- [ ] **Step 3: 验证**

Run: `python -c "from lsc.config import ExportProfile; p = ExportProfile(resolution='1920:1080'); print(p.resolution)"`
Expected: `1920x1080`

- [ ] **Step 4: Commit**

```bash
git add lsc/config.py python-backend/handlers/room_handler.py
git commit -m "fix: 修复导出分辨率格式不匹配导致静默失效"
```

---

### Task 12: 修复默认编码器格式不一致

**Files:**
- Modify: `python-backend/handlers/room_handler.py:284`

**问题：** `load_settings()` 默认编码器是旧格式 `"H.264 NVENC"`，前端默认是 `"h264_nvenc"`。

- [ ] **Step 1: 统一默认编码器格式**

将 `room_handler.py:284`：

```python
        'encoder': 'H.264 NVENC',
```

替换为：

```python
        'encoder': 'h264_nvenc',
```

- [ ] **Step 2: Commit**

```bash
git add python-backend/handlers/room_handler.py
git commit -m "fix: 统一默认编码器格式为 h264_nvenc"
```

---

### Task 13: 导出时传入 preset 参数

**Files:**
- Modify: `python-backend/handlers/room_handler.py:1374-1383`

**问题：** `ExportProfile` 构造时不传 `preset`，始终默认 `"medium"`。

- [ ] **Step 1: 在导出时传入 preset**

在 `room_handler.py` 的 `ExportProfile` 构造中（约行 1374-1383），将：

```python
            profile = ExportProfile(
                codec=codec_map.get(encoder, 'libx264'),
                crf=crf_val,
                rate_mode=rate_mode_map.get(param_mode, 'crf'),
                video_bitrate=video_bitrate,
                audio_bitrate=audio_br,
                resolution=resolution,
                fps=_parse_fps(framerate),
                vertical_crop=vertical_crop,
            )
```

替换为：

```python
            # 从设置中读取编码预设（默认 medium）
            preset = settings.get('preset', 'medium')
            profile = ExportProfile(
                codec=codec_map.get(encoder, 'libx264'),
                crf=crf_val,
                preset=preset,
                rate_mode=rate_mode_map.get(param_mode, 'crf'),
                video_bitrate=video_bitrate,
                audio_bitrate=audio_br,
                resolution=resolution,
                fps=_parse_fps(framerate),
                vertical_crop=vertical_crop,
            )
```

- [ ] **Step 2: Commit**

```bash
git add python-backend/handlers/room_handler.py
git commit -m "feat: 导出时传入 preset 参数"
```

---

### Task 14: 前端设置页新增 preset 和编码器选项

**Files:**
- Modify: `lsc-electron/src/pages/Settings/index.tsx`
- Modify: `lsc-electron/src/types/index.ts`

- [ ] **Step 1: 在 RecordSettings 类型中新增 preset 字段**

在 `types/index.ts` 的 `RecordSettings` 接口中，`preview_quality` 之后添加：

```typescript
  preset?: string
```

- [ ] **Step 2: 在 appStore 默认设置中添加 preset**

在 `appStore.ts` 的 `defaultSettings` 中，`preview_quality` 之后添加：

```typescript
  preset: 'medium',
```

- [ ] **Step 3: 在 Settings 页面添加 preset 下拉选项**

在 `Settings/index.tsx` 的编码参数 SettingsRow 之后（约行 342 之后），CRF 行之前，添加：

```tsx
            <SettingsRow label="编码预设">
              <select
                value={settings.preset || 'medium'}
                onChange={e => handleRecordChange('preset', e.target.value)}
                className="settings-select"
              >
                <option value="ultrafast">ultrafast（最快）</option>
                <option value="fast">fast（快速）</option>
                <option value="medium">medium（均衡）</option>
                <option value="slow">slow（慢速）</option>
              </select>
            </SettingsRow>
```

- [ ] **Step 4: 在编码器选项中补充 Intel/AMD 编码器**

在 `Settings/index.tsx:325-330` 的编码器 select 中，将：

```tsx
                <option value="libx264">libx264</option>
                <option value="libx265">libx265</option>
                <option value="copy">copy</option>
                <option value="h264_nvenc">h264_nvenc</option>
                <option value="hevc_nvenc">hevc_nvenc</option>
```

替换为：

```tsx
                <option value="libx264">libx264</option>
                <option value="libx265">libx265</option>
                <option value="copy">copy</option>
                <option value="h264_nvenc">h264_nvenc (NVIDIA)</option>
                <option value="hevc_nvenc">hevc_nvenc (NVIDIA)</option>
                <option value="h264_qsv">h264_qsv (Intel)</option>
                <option value="h264_amf">h264_amf (AMD)</option>
```

- [ ] **Step 5: 验证**

Run: `cd lsc-electron && npx tsc --noEmit`
Expected: 无类型错误

- [ ] **Step 6: Commit**

```bash
git add lsc-electron/src/types/index.ts lsc-electron/src/store/appStore.ts lsc-electron/src/pages/Settings/index.tsx
git commit -m "feat: 设置页新增编码预设和 Intel/AMD 编码器选项"
```

---

### Task 15: 清理死配置 audio_codec 和 autoRealign

**Files:**
- Modify: `python-backend/handlers/room_handler.py:292`

**问题：** `audio_codec` 字段在 `load_settings()` 默认值中定义但从未被任何代码读取使用。

- [ ] **Step 1: 移除 load_settings 中的 audio_codec 默认值**

将 `room_handler.py:292`：

```python
        'audio_codec': 'AAC 128k',
```

删除该行。

- [ ] **Step 2: Commit**

```bash
git add python-backend/handlers/room_handler.py
git commit -m "chore: 移除未使用的 audio_codec 死配置"
```

---

## 最终验证

### Task 16: 全量类型检查与集成验证

- [ ] **Step 1: 后端 Python 语法检查**

Run: `python -c "from lsc.config import ExportProfile; from lsc.gui.multi_room.session import RoomSession; from lsc.gui.multi_room.manager import MultiRoomManager; from lsc.core.services.resource_monitor import collect_system_stats; print('All imports OK')"`

- [ ] **Step 2: 前端 TypeScript 检查**

Run: `cd lsc-electron && npx tsc --noEmit`
Expected: 0 errors

- [ ] **Step 3: Ruff 检查**

Run: `ruff check lsc/`
Expected: 无新增错误

- [ ] **Step 4: 验证分辨率修复**

Run: `python -c "from lsc.config import ExportProfile; p1 = ExportProfile(resolution='1920:1080'); p2 = ExportProfile(resolution='1920x1080'); assert p1.resolution == '1920x1080'; assert p2.resolution == '1920x1080'; print('Resolution fix OK')"`

- [ ] **Step 5: 验证 psutil 资源采集**

Run: `python -c "from lsc.core.services.resource_monitor import collect_system_stats; s = collect_system_stats('C:/'); assert s['cpu_percent'] >= 0; assert s['memory_percent'] > 0; print('Resource monitor OK')"`

- [ ] **Step 6: Final commit（如有遗留改动）**

```bash
git add -A
git commit -m "chore: 集成验证修复"
```
