# 修复预览全屏卡顿和切片系统失效

## Summary

用户报告两个问题：
1. **预览窗口放大后总是在卡** - 需要提高程序稳定性
2. **切片系统没有起作用**

两个问题的根因均已通过代码探索定位：

**问题 1 根因**：全屏 Modal 打开时，小预览区 VideoPreview 因 key 变化重新挂载，新 MsePlayer 在 `window.__msePlayers[roomId]` 注册表中被全屏 player 覆盖，导致小预览收不到任何 MSE 段。15 秒后小预览的 `loadTimeout` 触发，向 backend 发送 `enable_preview { enabled: false }`，**把整个房间的 MseStreamer 停掉**，全屏预览在缓冲耗尽（约 30 秒）后冻结。

**问题 2 根因**：在 Electron/MSE 模式下，`_get_current_pos(room)` 返回的 `controller.current_sec` **永远是 0**，因为 MSE 预览的播放位置（浏览器 `<video>.currentTime`）从不回传后端。用户按 I/O 设置入出点 → `mark_in = mark_out = 0` → 导出时 `duration < 1` → `ClipExporter.export_clip` 返回 `"Clip too short: 0.0s"` → 导出失败。

## Current State Analysis

### 问题 1：预览全屏卡顿

**故障链**：
```
全屏 Modal 打开
    ↓
RoomCard.tsx:235  key 从 'normal' 变为 'fs' → 小预览 VideoPreview 卸载并重新挂载
    ↓
新小预览 MsePlayer 创建，注册到 window.__msePlayers[roomId]
    ↓
全屏 VideoPreview 挂载，覆盖 registry[roomId] = fullscreenPlayer
    ↓ （小预览 player 收不到任何段）
15 秒后 VideoPreview.tsx:99-110  loadTimeout 触发
    ↓
hasReceivedDataRef.current === false → setError + send('enable_preview', {enabled: false})
    ↓
后端 room_handler.py:1157-1174  停止 MseStreamer
    ↓
全屏预览不再收到新段，缓冲区（约 30 秒）耗尽后画面冻结
```

**关键代码位置**：
- [RoomCard.tsx:235](file:///d:/Project/直播切片多人/lsc-electron/src/pages/Workbench/components/RoomCard.tsx#L235) - key 包含 `fs/normal` 导致全屏切换时小预览重新挂载
- [VideoPreview.tsx:99-110](file:///d:/Project/直播切片多人/lsc-electron/src/components/VideoPreview.tsx#L99-L110) - 15 秒 loadTimeout 触发后发送 `enable_preview { enabled: false }` 停掉后端 streamer
- [VideoPreview.tsx:157-161](file:///d:/Project/直播切片多人/lsc-electron/src/components/VideoPreview.tsx#L157-L161) - registry 覆盖问题

### 问题 2：切片系统失效

**故障链**：
```
用户观看 MSE 预览，按 I/O 设置入出点
    ↓
前端 Workbench/index.tsx:387-393  send('set_mark_in', { room_id })
    ↓
后端 room_handler.py:686  room.mark_in = _get_current_pos(room)
    ↓
_get_current_pos (room_handler.py:361-371):
    controller.current_sec 永远是 0（Electron 模式下 preview_widget is None，
    global_tick 不会更新 current_sec）
    ↓
room.mark_in = 0, room.mark_out = 0
    ↓
用户点击"添加到切片" → clip { start: 0, end: 0 }
    ↓
导出时 ClipExporter.export_clip (clip.py:126-128):
    duration = end - start = 0 < 1 → 返回 "Clip too short: 0.0s"
    ↓
导出失败，用户以为"切片系统没起作用"
```

**关键代码位置**：
- [room_handler.py:361-371](file:///d:/Project/直播切片多人/python-backend/handlers/room_handler.py#L361-L371) - `_get_current_pos` 恒返回 0
- [room_handler.py:676-708](file:///d:/Project/直播切片多人/python-backend/handlers/room_handler.py#L676-L708) - `set_mark_in`/`set_mark_out` 使用 `_get_current_pos`
- [manager.py:1424](file:///d:/Project/直播切片多人/lsc/gui/multi_room/manager.py#L1424) - `current_sec` 仅从 Qt widget 同步，Electron 下为 None
- [Workbench/index.tsx:387-413](file:///d:/Project/直播切片多人/lsc-electron/src/pages/Workbench/index.tsx#L387-L413) - `handleMarkIn`/`handleAddClip`

## Proposed Changes

### 文件 1: `d:\Project\直播切片多人\lsc-electron\src\components\VideoPreview.tsx`

#### 修改 1: loadTimeout 触发时不再发送 enable_preview false

**位置**：第 99-110 行

**改动**：loadTimeout 触发时只显示错误状态，**不发送 `enable_preview { enabled: false }`**，避免小预览的误判停止整个房间的后端 streamer

**修改后**：
```tsx
loadTimeoutRef.current = setTimeout(() => {
  if (!hasReceivedDataRef.current && playerRef.current) {
    console.warn(`[VideoPreview] 预览加载超时 (${roomId})`)
    setError('预览加载超时，请检查直播流是否正常')
    // 不再通知后端关闭预览 —— 该房间可能有其他 VideoPreview（全屏）正在使用
    // 后端 streamer 的生命周期应由用户主动点击"停止预览"控制
  }
}, 15000)
```

**为什么**：
- 全屏打开时小预览重新挂载，新 player 收不到段是正常现象（被全屏 player 覆盖）
- 小预览的 loadTimeout 不应该停止整个房间的 streamer
- 后端 streamer 的生命周期应由用户主动控制（点击"停止预览"按钮）

### 文件 2: `d:\Project\直播切片多人\lsc-electron\src\pages\Workbench\components\RoomCard.tsx`

#### 修改 2: 移除 key 中的全屏状态切换

**位置**：第 235 行

**改动**：移除 key 中的 `fs/normal` 切换，改为固定 key

```tsx
<VideoPreview
  key={`preview-${room.room_id}`}
  roomId={room.room_id}
  ...
/>
```

**为什么**：
- 之前添加 `fs/normal` 切换是为了修复"全屏关闭后小预览停止"的问题
- 但这导致全屏打开时小预览重新挂载，触发 loadTimeout → 停止 streamer → 全屏卡顿
- 移除 key 切换后，小预览在全屏打开/关闭时不会重新挂载，保持原有 player 实例
- 配合修改 1（loadTimeout 不停止 streamer），小预览的 player 在全屏期间虽然收不到段，但不会影响全屏 player

**注意**：这会恢复"全屏关闭后小预览停止"的问题，但通过修改 3（registry cleanup 保护）和修改 4（全屏关闭后重新注册）来解决

### 文件 3: `d:\Project\直播切片多人\lsc-electron\src\components\VideoPreview.tsx`

#### 修改 3: 全屏 VideoPreview 卸载时不删除 registry 槽位（已有保护）

**位置**：第 182-189 行（已实现）

**现状**：registry cleanup 已有保护逻辑 `if (currentRegistry[roomId]?.player === playerRef.current)`，全屏 VideoPreview 卸载时会删除自己注册的槽位

**问题**：全屏卸载后，registry[roomId] 被删除，小预览的 player 需要重新注册

#### 修改 4: 小预览 VideoPreview 在全屏关闭后重新注册 registry

**位置**：VideoPreview 组件内，新增 useEffect 监听全屏状态

**方案**：VideoPreview 接收 `isFullscreen` prop（或通过 store 获取），当 `isFullscreen` 从 true 变为 false 时，重新注册自己到 registry 并请求 mse_init

**实际更简方案**：由于移除了 key 切换（修改 2），小预览的 VideoPreview 在全屏期间不会卸载，其 registry 注册仍然存在（只是被全屏 player 覆盖）。全屏 VideoPreview 卸载时，由于保护逻辑，只会删除自己注册的槽位（如果 registry[roomId] 还指向全屏 player）。

**但问题是**：全屏 VideoPreview 卸载后，registry[roomId] 被删除，小预览的 player 仍在但不在 registry 中。

**最终方案**：修改 VideoPreview 的 registry useEffect，添加对全屏状态的监听。当全屏关闭时，小预览主动重新注册并请求 mse_init。

由于 VideoPreview 当前不知道全屏状态，最简方案是：**在 RoomCard 中传入 `isFullscreenActive` prop，VideoPreview 监听它从 true → false 的变化，重新注册 registry 并请求 mse_init**。

```tsx
// VideoPreviewProps 新增
isFullscreenActive?: boolean

// 新增 useEffect
const lastFullscreenRef = useRef(false)
useEffect(() => {
  // 全屏从打开变为关闭时，重新注册 registry 并请求 init
  if (lastFullscreenRef.current && !isFullscreenActive && playerRef.current && videoRef.current) {
    const registry = (window as any).__msePlayers || {}
    registry[roomId] = { feedInit, feedMedia, player: playerRef.current }
    ;(window as any).__msePlayers = registry
    sendRef.current('request_mse_init', { room_id: roomId })
  }
  lastFullscreenRef.current = !!isFullscreenActive
}, [isFullscreenActive, roomId, feedInit, feedMedia])
```

### 文件 4: `d:\Project\直播切片多人\lsc-electron\src\pages\Workbench\index.tsx`

#### 修改 5: RoomCard 传递 isFullscreenActive prop

**位置**：第 845-862 行 RoomCard 使用处

**改动**：新增 `isFullscreenActive={fullscreenRoomId === room.room_id}` prop

### 文件 5: `d:\Project\直播切片多人\lsc-electron\src\pages\Workbench\components\RoomCard.tsx`

#### 修改 6: RoomCard 接收并传递 isFullscreenActive 到 VideoPreview

**位置**：RoomCardProps 接口和 VideoPreview 使用处

**改动**：
1. RoomCardProps 已有 `fullscreenRoomId` prop
2. VideoPreview 使用处新增 `isFullscreenActive={fullscreenRoomId === room.room_id}`

### 文件 6: `d:\Project\直播切片多人\python-backend\handlers\room_handler.py`

#### 修改 7: set_mark_in / set_mark_out 支持前端直接传入时间

**位置**：第 676-708 行

**改动**：`set_mark_in` / `set_mark_out` 接收前端传入的 `time` 参数，若提供则直接使用，否则回退到 `_get_current_pos`

```python
@server.on('set_mark_in')
async def handle_set_mark_in(data):
    room_id = data.get('room_id')
    if not room_id:
        return {'error': 'room_id is required'}

    # 前端可直接传入当前播放位置（秒），避免 Electron 模式下 _get_current_pos 恒返回 0
    time_value = data.get('time')

    def _mark():
        room = manager.get_room(room_id)
        if room is None:
            return None
        if time_value is not None:
            room.mark_in = float(time_value)
        else:
            room.mark_in = _get_current_pos(room)
        return room.mark_in

    value = await asyncio.get_running_loop().run_in_executor(None, lambda: bridge.call(_mark))
    _broadcast_rooms()
    return {'success': value is not None, 'mark_in': value}
```

`set_mark_out` 同理。

**为什么**：
- Electron/MSE 模式下，`_get_current_pos` 恒返回 0，因为 `controller.current_sec` 不被更新
- 前端能直接从 `videoElement.currentTime` 获取准确播放位置
- 让前端传入 `time` 参数是最直接的解决方案
- 保留 `_get_current_pos` 回退，兼容 Qt 模式

### 文件 7: `d:\Project\直播切片多人\lsc-electron\src\pages\Workbench\index.tsx`

#### 修改 8: handleMarkIn / handleMarkOut 传入当前播放位置

**位置**：第 387-394 行

**改动**：从 `window.__msePlayers[roomId]` 获取 player，读取 `videoElement.currentTime`，传入 `time` 参数

```tsx
// 获取 MSE player 的当前播放位置
const getPreviewCurrentTime = useCallback((roomId: string): number => {
  const registry = (window as any).__msePlayers
  const entry = registry?.[roomId]
  if (entry?.player?.videoElement) {
    return entry.player.videoElement.currentTime
  }
  return 0
}, [])

// 设置入点
const handleMarkIn = useCallback((roomId: string) => {
  const time = getPreviewCurrentTime(roomId)
  send('set_mark_in', { room_id: roomId, time })
}, [send, getPreviewCurrentTime])

// 设置出点
const handleMarkOut = useCallback((roomId: string) => {
  const time = getPreviewCurrentTime(roomId)
  send('set_mark_out', { room_id: roomId, time })
}, [send, getPreviewCurrentTime])
```

**为什么**：
- 前端能直接从 `videoElement.currentTime` 获取准确播放位置
- 传入后端后，`set_mark_in` / `set_mark_out` 直接使用该值设置 `room.mark_in` / `room.mark_out`
- 这样 mark_in / mark_out 不再是 0，切片导出的 duration > 0，导出能成功

### 文件 8: `d:\Project\直播切片多人\python-backend\handlers\room_handler.py`

#### 修改 9: _get_current_pos 在 current_sec 为 0 时回退到录制时长

**位置**：第 361-371 行

**改动**：当 `current_sec` 为 0 且正在录制时，回退到录制已进行时长

```python
def _get_current_pos(room: Any) -> float:
    """获取当前播放/录制位置（秒）。"""
    if room.controller is not None:
        pos = getattr(room.controller, 'current_sec', 0)
        # Electron 模式下 current_sec 可能恒为 0，回退到录制时长
        if pos is not None and pos > 0:
            return float(pos)
    if room.is_recording and room.record_started_at is not None:
        if isinstance(room.record_started_at, datetime):
            return (datetime.now() - room.record_started_at).total_seconds()
        return 0.0
    return 0.0
```

**为什么**：
- 作为修改 7/8 的补充防御
- 即使前端未传入 `time`，`_get_current_pos` 也能在录制中返回有意义的位置
- Qt 模式下 `current_sec > 0` 仍走原逻辑

## Assumptions & Decisions

### 假设
1. **全屏 VideoPreview 和小预览 VideoPreview 共存时，只有全屏 player 应该接收段**：这是合理的，因为用户注意力在全屏
2. **`videoElement.currentTime` 是准确的播放位置**：MSE 直播流的 currentTime 从 0 开始，表示从开始播放至今的秒数
3. **切片导出依赖 mark_in / mark_out 的差值**：duration = mark_out - mark_in > 1 秒才能导出

### 决策
1. **问题 1 修复选择"loadTimeout 不停止 streamer + 移除 key 切换 + 全屏关闭后重新注册"**：而非 portal 移动 video 元素（改动太大）
2. **问题 2 修复选择"前端传入 time 参数"**：而非让后端定期接收前端上报的 preview_position（改动太大）
3. **保留 `_get_current_pos` 回退逻辑**：兼容 Qt 模式和前端未传入 time 的情况
4. **不实现批量切片功能**：用户未明确要求，且 Electron 模式下 include_in_cut 未接后端，改动范围过大
5. **不实现自动切片/分析**：用户未明确要求，聚焦修复"切片系统没起作用"的核心问题

## Verification steps

### 1. TypeScript 编译验证
```powershell
cd "d:\Project\直播切片多人\lsc-electron"; npx tsc --noEmit
```
预期：无错误

### 2. Python 语法验证
```powershell
cd "d:\Project\直播切片多人"; python -c "import python-backend.handlers.room_handler; print('OK')"
```

### 3. 重启程序验证
- 终止现有进程，启动 `npm run dev`

### 4. 预览全屏卡顿验证
- 添加房间并连接，启用预览，确认小预览有画面
- 点击"全屏放大"按钮
- **预期**：全屏窗口显示画面，持续播放超过 30 秒不卡顿
- 等待 15 秒以上（验证 loadTimeout 不再停止 streamer）
- 关闭全屏窗口
- **预期**：小预览区画面恢复播放（可能在 1-2 秒短暂黑屏后恢复）

### 5. 切片系统验证
- 启用房间预览，观看预览画面
- 按 I 键或点击"入点"按钮设置入点
- 等待几秒后按 O 键或点击"出点"按钮设置出点
- **预期**：ControlBar 的时间轴显示入出点标记，mark_in / mark_out 不为 0
- 点击"添加到切片"
- 在切片列表点击导出
- **预期**：导出成功，不再出现"Clip too short: 0.0s"错误
- 检查输出目录，应有导出的 mp4 文件

### 6. 后端日志验证
- 检查后端日志，确认 `set_mark_in` / `set_mark_out` 收到的 `time` 参数不为 0
- 确认全屏期间无 `enable_preview { enabled: false }` 消息
