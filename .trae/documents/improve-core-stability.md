# 提升核心功能稳定性（含录制权限修复）

## 背景与现状

用户反馈：
1. 录制启动失败："文件写入权限不足。请检查输出目录权限。"
2. 整个程序的核心功能稳定性都很差

经全面审查（搜索代理报告 29 个隐患），剔除只影响 PySide6 独立模式的问题（C1/C2），聚焦 Electron 模式下真实存在的稳定性问题。

## 问题分析

### 问题 1：录制启动失败（用户原始反馈，Critical）

**根因**：[manager.py#L1056-L1060](file:///d:/Project/直播切片多人/lsc/gui/multi_room/manager.py#L1056-L1060) 的 `preflight_recording` 在 `output_dir`（`~/LSC/output`）不可写时直接返回错误并 `return False`，**走不到第 1074-1086 行的目录回退逻辑**。`humanize_error` 匹配 `room.last_error` 中的"拒绝访问"，返回"文件写入权限不足"。

### 问题 2：录制断线重连阻塞 Qt 主线程（High，违反硬约束）

**根因**：[manager.py#L1441](file:///d:/Project/直播切片多人/lsc/gui/multi_room/manager.py#L1441) `_on_global_tick` 在 Qt 主线程（QTimer）执行，第 1441 行调用 `_attempt_recording_reconnect`，而后者第 1364 行直接调用 `self.start_recording()`，同步执行 HTTP 刷新流 URL（最长 36s）+ FFmpeg 启动（最长 5s），**阻塞 Qt 主线程**。

**违反项目记忆硬约束**："Recording start/preview reconnect operations must be executed in background threads to prevent main thread blocking"。

### 问题 3：MSE streamer 启动防重检查存在 TOCTOU 竞争（Medium）

**根因**：[room_handler.py#L1012-L1018](file:///d:/Project/直播切片多人/python-backend/handlers/room_handler.py#L1012-L1018) 的 `_mse_starting` 检查与添加虽然在 `_mse_streamers_lock` 内，但第 1018 行 `_mse_starting.add(room_id)` 后释放锁，第 1042-1044 行的 `bridge.call(_snapshot)` 在锁外执行。如果此时另一个请求检查 `_mse_starting`，会被挡住（正确）。但若第一个请求在 `_start()` 第 1119 行 `streamer.start()` 成功后、第 1122 行注册到 `_mse_streamers` 前崩溃，streamer 进程泄漏。

实际影响较低（需要崩溃时机精确），但值得防御性修复。

### 问题 4：WebSocket 重连无最大次数上限（Medium）

**根因**：[websocket.ts#L129-L142](file:///d:/Project/直播切片多人/lsc-electron/src/services/websocket.ts#L129-L142) 实现了指数退避 1s→15s，但无最大重连次数。后端长时间不可用时前端永久重连，日志爆炸。

## 修复方案（按优先级排序）

### 修改 1：录制 preflight 失败时回退目录（Critical，修复用户原始反馈）

**文件**：[d:\Project\直播切片多人\lsc\gui\multi_room\manager.py](file:///d:/Project/直播切片多人/lsc/gui/multi_room/manager.py)

**位置**：第 1054-1060 行

**当前代码**：
```python
# Pre-flight disk space check (2GB threshold per project memory constraint)
from lsc.gui.pages.recording_controller import RecordingController
preflight = RecordingController.preflight_recording(output_dir, concurrent_streams=1)
if preflight:
    room.last_error = preflight
    _log.warning("录制预检失败: %s", preflight)
    return False
```

**修改后**：
```python
# Pre-flight disk space check (2GB threshold per project memory constraint)
from lsc.gui.pages.recording_controller import RecordingController
preflight = RecordingController.preflight_recording(output_dir, concurrent_streams=1)
if preflight:
    # 默认目录不可写或空间不足，回退到 ~/.lsc/output（用户主目录，通常可写）
    fallback_base = os.path.join(os.path.expanduser('~'), '.lsc', 'output')
    if os.path.abspath(fallback_base) != os.path.abspath(output_dir):
        _log.warning("预检失败 %s，回退到 %s", output_dir, fallback_base)
        fallback_preflight = RecordingController.preflight_recording(fallback_base, concurrent_streams=1)
        if not fallback_preflight:
            output_dir = fallback_base
            preflight = ""
        else:
            _log.warning("回退目录预检也失败: %s", fallback_preflight)
    if preflight:
        room.last_error = preflight
        _log.warning("录制预检失败: %s", preflight)
        return False
```

**为什么**：在 preflight 阶段就尝试回退，避免直接返回错误。`os.path.abspath` 比较防止 `output_dir` 本身就是回退目录时重复检查。回退通过后更新 `output_dir` 变量，让后续逻辑使用回退目录。

### 修改 2：录制断线重连移入后台线程（High，违反硬约束）

**文件**：[d:\Project\直播切片多人\lsc\gui\multi_room\manager.py](file:///d:/Project/直播切片多人/lsc/gui/multi_room/manager.py)

**位置**：第 1436-1442 行（`_on_global_tick` 中的 watchdog 调用）

**当前代码**：
```python
# Watchdog: check FFmpeg health + auto-reconnect
if room.is_recording:
    error_msg = controller.watchdog_check()
    if error_msg:
        _log.warning("Room %s watchdog: %s", room.room_id, error_msg)
        self._attempt_recording_reconnect(room, error_msg)
        self._dirty_recording = True
```

**修改后**：
```python
# Watchdog: check FFmpeg health + auto-reconnect
if room.is_recording:
    error_msg = controller.watchdog_check()
    if error_msg:
        _log.warning("Room %s watchdog: %s", room.room_id, error_msg)
        # 移入后台线程执行重连，避免 start_recording 的 HTTP 刷新 + FFmpeg 启动阻塞 Qt 主线程
        # 项目记忆硬约束：录制启动/预览重连操作必须执行在后台线程
        import threading
        def _reconnect_in_background():
            try:
                self._attempt_recording_reconnect(room, error_msg)
            except Exception as exc:
                _log.error("Room %s reconnect failed: %s", room.room_id, exc)
            finally:
                # 标记 dirty 让下次 tick 刷新 UI（不能在非主线程直接 emit signal）
                self._dirty_recording = True
        t = threading.Thread(target=_reconnect_in_background, daemon=True)
        t.start()
```

**为什么**：`_attempt_recording_reconnect` 内部调用 `self.start_recording()`，包含 HTTP 刷新流 URL（最长 36s）+ FFmpeg 启动（最长 5s），在 Qt 主线程执行会冻结 UI。移入 daemon 线程后，主线程立即返回，UI 保持响应。

**注意事项**：
- `_dirty_recording = True` 在后台线程设置是安全的（bool 赋值原子），下次 Qt 主线程 tick 会读取并 emit signal
- `_attempt_recording_reconnect` 内部对 room 的属性修改（`room.is_recording`、`room.last_error` 等）存在跨线程竞争，但这些属性本来就是 Qt 主线程的 `_on_global_tick` 在读、`start_recording` 在写。`start_recording` 已经被设计为可在 executor 线程执行（[room_handler.py#L580](file:///d:/Project/直播切片多人/python-backend/handlers/room_handler.py#L580) 已用 `run_in_executor`），所以属性竞争已被现有设计接受
- 不使用 QThread/QRunnable 是因为重连是低频操作（最多每 15s 一次），普通 threading.Thread 足够

### 修改 3：WebSocket 重连增加最大次数上限（Medium）

**文件**：[d:\Project\直播切片多人\lsc-electron\src\services\websocket.ts](file:///d:/Project/直播切片多人/lsc-electron/src/services/websocket.ts)

**位置**：第 129-142 行 `scheduleReconnect`

**需要先读取文件确认当前实现，然后添加**：
- 最大重连次数 20 次
- 超过后停止重连，通过回调通知 UI 显示"后端不可用，请重启应用"
- 用户手动重连时重置计数器

### 修改 4：MSE streamer 启动异常路径防御性清理（Medium，可选）

**文件**：[d:\Project\直播切片多人\python-backend\handlers\room_handler.py](file:///d:/Project/直播切片多人/python-backend/handlers/room_handler.py)

**位置**：第 1138-1165 行

**当前问题**：`_start()` 第 1119-1122 行 `streamer.start()` 成功后立即注册到 `_mse_streamers`，但若后续第 1161-1163 行 `bridge.call(_set_preview_enabled)` 抛异常，`_handle_mse_preview` 的 finally 只清理 `_mse_starting`，不清理已注册的 streamer。虽然 streamer 在 `_mse_streamers` 中可被后续 stop 清理，但前端不知道需要 stop（因为 preview_enabled 未设置成功）。

**修改方向**：在 `_handle_mse_preview` 的 except 块中，如果 streamer 已注册但 preview_enabled 设置失败，主动调用 `streamer.stop()` 并从 `_mse_streamers` 移除。

**优先级**：低（需要 bridge.call 抛异常才触发，概率低），可作为后续优化。

## 验证步骤

1. **录制权限修复验证**：
   - 重启程序
   - 添加房间 → 连接 → 点击录制
   - 预期：录制成功启动，文件保存到 `~/.lsc/output/` 目录
   - 查看后端日志，应看到 `预检失败 ... 回退到 ...` 警告

2. **重连不阻塞 UI 验证**：
   - 开始录制后，手动 kill FFmpeg 进程模拟崩溃
   - 预期：UI 保持响应（鼠标可移动，按钮可点击），后端日志显示后台线程执行重连
   - 重连成功后录制恢复

3. **WebSocket 重连上限验证**：
   - 停止 Python 后端
   - 观察前端控制台，20 次重连后应停止并显示"后端不可用"提示
   - 重启后端后，前端应能手动重连

4. **TypeScript 编译**：
   ```bash
   cd d:\Project\直播切片多人\lsc-electron && npx tsc --noEmit
   ```

## 假设与决策

1. **聚焦 Electron 模式**：C1/C2 只影响 PySide6 独立模式（根目录 main.py），Electron 模式（python-backend/main.py）不加载 page.py，所以不在本次修复范围。
2. **优先修复用户感知问题**：录制权限是用户直接反馈的问题，优先修复。重连阻塞 UI 是稳定性隐患，作为第二优先级。
3. **不重构 _attempt_recording_reconnect**：该函数逻辑复杂（退避、重试、状态管理），本次只把调用点移入后台线程，不重构内部逻辑，降低引入新 bug 的风险。
4. **MSE streamer 清理（修改 4）标记为可选**：实际触发概率低，可作为后续优化。本次重点修复修改 1-3。

## 后续优化（不在本次范围）

搜索代理报告的其他问题，按优先级排序供后续处理：
- H1：recording_history 非线程安全（多房间并发时历史记录错乱）
- H4：持久化双路径不一致（数据迁移风险）
- C4：MSE streamer Popen 未设 stdin（强 kill 损坏 segment）
- H5-H7：资源泄漏与状态同步
- M1-M8：防御性加固
- L1-L8：体验优化
