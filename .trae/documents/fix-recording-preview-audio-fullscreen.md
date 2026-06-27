# 修复录制未知错误、预览静音、全屏关闭后小预览停止

## Summary

用户报告三个问题：
1. **开始录制显示"未知错误"** + antd message 静态函数警告
2. **预览没有声音**，取消静音按键后依旧没声音
3. **全屏放大后关闭**，小预览区画面停止无法继续预览

三个问题的根因均已通过代码探索定位：
- 问题 1：后端 `handle_start_recording` 在 `success=False`（非异常）时未返回 error 字段；前端未用 antd `<App>` 包裹
- 问题 2：FFmpeg 用 `anullsrc` 静音源 + `-map 0:a` 丢弃直播流真实音频；`<video muted>` 硬编码，`MsePlayer.setMuted()` 从未被调用
- 问题 3：全屏 Modal 新建第二个 VideoPreview 实例，与房间卡片共享 `__msePlayers[roomId]` 槽位，卸载时 `delete registry[roomId]` 把小预览区的注册也清掉

## Current State Analysis

### 问题 1：录制"未知错误"

**根因链路**：
```
后端 manager.start_recording() 返回 False（如房间未连接）
    ↓
room_handler.py:595  return {'success': bool(success)}  ← 无 error 字段
    ↓
前端 Workbench/index.tsx:101  message.error(`录制启动失败：${data?.error || '未知错误'}`)
    ↓
显示"录制启动失败：未知错误"
```

**antd message 警告根因**：
- [App.tsx:50](file:///d:/Project/直播切片多人/lsc-electron/src/App.tsx#L50) 只用 `ConfigProvider` 包裹，未用 antd `<App>` 组件
- 全项目使用静态 `import { message } from 'antd'`，未使用 `App.useApp()`
- antd v5 动态主题下静态 message 无法消费 context token，触发警告

### 问题 2：预览没声音

**后端根因**（[mse_streamer.py:122-135](file:///d:/Project/直播切片多人/lsc/core/services/mse_streamer.py#L122-L135)）：
```python
"-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono",  # 输入 0：静音音频源
...
"-i", self._url,                                   # 输入 1：直播流（含真实音频）
"-map", "0:a",                                     # ★ 映射静音源音频，非直播流音频
"-map", "1:v",                                     # 映射直播流视频
```
直播流的真实音频被完全丢弃，fMP4 流的音频轨永远是 `anullsrc` 生成的静音。

**前端根因**（[VideoPreview.tsx:205](file:///d:/Project/直播切片多人/lsc-electron/src/components/VideoPreview.tsx#L205)）：
```tsx
<video muted  // ★ 硬编码，永远是 true
```
- `room.preview_muted` 状态变化不会同步到 `<video>.muted`
- [mediaSourcePlayer.ts:185-189](file:///d:/Project/直播切片多人/lsc-electron/src/services/mediaSourcePlayer.ts#L185-L189) 的 `setMuted()` 方法存在但**全项目从未调用**

### 问题 3：全屏关闭后小预览停止

**根因**：[Workbench/index.tsx:1092-1110](file:///d:/Project/直播切片多人/lsc-electron/src/pages/Workbench/index.tsx#L1092-L1110) 全屏 Modal 新建第二个 VideoPreview 实例，与 RoomCard 中的 VideoPreview 共享同一 `roomId`。

**故障链**：
1. 全屏打开 → 全屏 VideoPreview 挂载 → `window.__msePlayers[roomId]` 被覆盖为全屏 player
2. 全屏期间 → 所有 `mse_segment` 投喂给全屏 player，小预览区 player 收不到分片
3. 全屏关闭 → 全屏 VideoPreview 卸载 → `player.stop()` + `delete registry[roomId]`（[VideoPreview.tsx:180](file:///d:/Project/直播切片多人/lsc-electron/src/components/VideoPreview.tsx#L180)）
4. 关闭后 → 小预览区 VideoPreview 的 props 未变（`active=true, roomId` 不变），React 不重新挂载，不重新注册 registry，不重新 `player.start()`
5. 后端持续推送 `mse_segment`，但 `useWebSocket.ts:78-87` 查找 `window.__msePlayers[roomId]` 找不到 player → 分片被缓存但不投喂 → 画面停止

## Proposed Changes

### 文件 1: `d:\Project\直播切片多人\python-backend\handlers\room_handler.py`

#### 修改 1: `handle_start_recording` 失败时返回具体错误

**位置**：第 594-595 行

**改动**：当 `success=False` 时，从 `room.last_error` 获取具体错误原因并返回

```python
_broadcast_rooms()
if not success:
    # 获取房间的具体错误信息，避免前端显示"未知错误"
    room = manager.get_room(room_id)
    error_msg = (room.last_error if room else None) or '录制启动失败，请检查房间状态'
    return {'success': False, 'error': humanize_error(error_msg)}
return {'success': True}
```

**为什么**：
- `manager.start_recording()` 返回 False 时会设置 `room.last_error`（如"房间未连接"、"录制目录不可写"等）
- 当前代码只返回 `{'success': False}` 无 error 字段，前端 fallback 到"未知错误"
- 修改后前端能显示具体原因，用户可据此排查

### 文件 2: `d:\Project\直播切片多人\lsc-electron\src\App.tsx`

#### 修改 2: 用 antd `<App>` 组件包裹应用

**位置**：第 2 行 import 和第 49-50 行

**改动**：
1. import 添加 `App as AntdApp`
2. 用 `<AntdApp>` 包裹 `<AppContent />`

```tsx
import { ConfigProvider, theme, App as AntdApp } from 'antd'
...
function App() {
  return (
    <ConfigProvider ...>
      <AntdApp>
        <AppContent />
      </AntdApp>
    </ConfigProvider>
  )
}
```

**为什么**：
- antd v5 要求用 `<App>` 组件包裹才能让静态 `message` 函数消费动态主题 context
- 修复后消除"Static function can not consume context like dynamic theme"警告
- 保持现有 `import { message } from 'antd'` 用法不变（antd `<App>` 会自动注入 context）

### 文件 3: `d:\Project\直播切片多人\lsc\core\services\mse_streamer.py`

#### 修改 3: 修复 FFmpeg 音频映射，使用直播流真实音频

**位置**：第 119-150 行 FFmpeg 命令构造

**改动**：
- 移除 `anullsrc` 静音音频源输入
- 改为映射直播流的音频轨：`-map 1:a`（原为 `-map 0:a`）
- 移除 `-map 0:a` 和 lavfi anullsrc 输入
- 保留视频映射 `-map 1:v`（原为 `-map 1:v`，现输入 0 即直播流）
- 添加 fallback：如果直播流无音频轨，FFmpeg 会报错，此时回退到 anullsrc

**修改后命令**：
```python
cmd = [
    self._ffmpeg_path,
    "-loglevel", "error",
    "-re",
    "-fflags", "+genpts",
    "-thread_queue_size", "1024",
    "-timeout", "10000000",
    "-rw_timeout", "15000000",
    "-reconnect", "1",
    "-reconnect_streamed", "1",
    "-reconnect_delay_max", "5",
    "-i", self._url,                # 唯一输入：直播流（含音视频）
    "-map", "0:v",                  # 映射直播流视频
    "-map", "0:a?",                 # 映射直播流音频（?表示可选，无音频轨时不报错）
    "-c:v", "libx264",
    "-preset", "ultrafast",
    "-tune", "zerolatency",
    "-pix_fmt", "yuv420p",
    "-g", "30",
    "-c:a", "aac",
    "-b:a", "128k",                 # 提升音质到 128k
    "-ar", "44100",                 # 标准采样率
    "-ac", "2",                     # 立体声
    "-shortest",
    "-f", "mp4",
    "-movflags", "frag_keyframe+empty_moov+default_base_moof",
    "-frag_duration", "500000",
    "pipe:1",
]
```

**为什么**：
- 原代码用 `anullsrc` 静音源是为了"确保 init 段始终包含音视频，与前端默认 codec string 匹配"
- 但这导致永远听不到声音，违背了预览的核心目的
- `-map 0:a?` 的 `?` 后缀表示音频轨可选，无音频轨的直播流不会报错（FFmpeg 会自动跳过）
- 移除 anullsrc 简化了命令，避免了 lavfi 输入与直播流的 PTS 对齐问题
- 音质提升到 128k/44100/立体声，匹配标准直播质量

**注意**：m3u8 分支的 `-live_start_index -1` 仍需保留，但 insert_idx 逻辑要调整（因为 `-i self._url` 的位置变了，现在是第一个也是唯一的 `-i`）

### 文件 4: `d:\Project\直播切片多人\lsc-electron\src\components\VideoPreview.tsx`

#### 修改 4: 接收 preview_muted prop 并同步到 video 元素

**位置**：VideoPreviewProps 接口（第 6-21 行）和组件实现

**改动**：
1. VideoPreviewProps 新增 `muted?: boolean` prop
2. 组件内用 `useEffect` 监听 `muted` 变化，调用 `playerRef.current?.setMuted(muted ?? true)`
3. `<video muted>` 改为 `<video muted={muted ?? true}>`（受控，默认 true 避免 autoplay policy 问题）

```tsx
interface VideoPreviewProps {
  roomId: string
  active: boolean
  send: (type: string, data: any) => void
  onReady?: (player: MsePlayer) => void
  onError?: (error: string) => void
  controls?: boolean
  style?: React.CSSProperties
  muted?: boolean  // 新增
}

// 在组件内新增 useEffect
useEffect(() => {
  if (playerRef.current) {
    playerRef.current.setMuted(muted ?? true)
  }
  if (videoRef.current) {
    videoRef.current.muted = muted ?? true
  }
}, [muted])
```

**为什么**：
- 当前 `<video muted>` 硬编码，`room.preview_muted` 变化无法反映到 DOM
- `MsePlayer.setMuted()` 方法已存在但从未被调用
- 修改后静音按钮能真正控制 video 元素的 muted 状态

### 文件 5: `d:\Project\直播切片多人\lsc-electron\src\pages\Workbench\components\RoomCard.tsx`

#### 修改 5: 传递 preview_muted 到 VideoPreview

**位置**：第 231-237 行 VideoPreview 使用处

**改动**：新增 `muted={room.preview_muted}` prop

```tsx
<VideoPreview
  roomId={room.room_id}
  active={true}
  send={send}
  controls={false}
  style={{ width: '100%', height: '100%' }}
  muted={room.preview_muted}
/>
```

**为什么**：将后端广播的 `preview_muted` 状态传递给 VideoPreview，驱动 video 元素的 muted 属性

### 文件 6: `d:\Project\直播切片多人\lsc-electron\src\pages\Workbench\index.tsx`

#### 修改 6: 全屏 Modal 也传递 muted prop

**位置**：第 1102-1108 行全屏 Modal 中的 VideoPreview

**改动**：新增 `muted={room.preview_muted}` prop（需要从 store 获取 room）

```tsx
{fullscreenRoomId && (
  <div style={{ width: '100%', height: '70vh' }}>
    <VideoPreview
      roomId={fullscreenRoomId}
      active={true}
      send={send}
      controls={true}
      style={{ width: '100%', height: '100%' }}
      muted={useAppStore.getState().rooms.find(r => r.room_id === fullscreenRoomId)?.preview_muted ?? true}
    />
  </div>
)}
```

#### 修改 7: 修复全屏关闭后小预览区画面停止

**位置**：第 1091-1111 行全屏 Modal

**改动**：全屏 Modal 关闭时，触发小预览区 VideoPreview 重新注册 player

**方案**：给小预览区的 VideoPreview 添加一个 `key` prop，当 `fullscreenRoomId` 变化时强制重新挂载

但更简洁的方案是：**全屏 Modal 内的 VideoPreview 卸载时，不删除 registry 中的 roomId 槽位**。修改 VideoPreview 的 registry cleanup 逻辑，只在 `active` 变为 false 时才 delete。

**实际采用的方案**：在 Workbench 中为小预览区的 VideoPreview 添加 `key` 属性，全屏打开/关闭时改变 key 强制重新挂载。但这会导致全屏期间小预览区也重新挂载。

**最终方案（推荐）**：修改 [VideoPreview.tsx:179-181](file:///d:/Project/直播切片多人/lsc-electron/src/components/VideoPreview.tsx#L179-L181) 的 registry cleanup，不直接 `delete registry[roomId]`，而是检查当前注册的是否还是自己（playerRef.current）。但这有时序问题。

**最简方案**：全屏 Modal 关闭时，通过触发小预览区 VideoPreview 重新挂载来恢复。给 RoomCard 内的 VideoPreview 添加 `key` prop，包含 `fullscreenRoomId` 状态：

```tsx
// RoomCard.tsx
<VideoPreview
  key={`preview-${room.room_id}-${isFullscreenRoom ? 'fs' : 'normal'}`}
  roomId={room.room_id}
  active={true}
  send={send}
  controls={false}
  style={{ width: '100%', height: '100%' }}
  muted={room.preview_muted}
/>
```

其中 `isFullscreenRoom = fullscreenRoomId === room.room_id`。这样：
- 全屏打开时，小预览区 VideoPreview 的 key 变化 → 重新挂载（注册新 player）
- 全屏关闭时，小预览区 VideoPreview 的 key 变化 → 再次重新挂载（注册新 player，恢复预览）

但 RoomCard 当前不知道 `fullscreenRoomId`。需要从 Workbench 传递 prop 或从 store 获取。

**采用方案**：在 RoomCard 中从 `useAppStore` 获取 `fullscreenRoomId`（或新增 prop 传递），用于计算 key。

实际上更简单的方案：**让 VideoPreview 的 registry 注册 effect 依赖 `active` 和 `roomId`，并在 cleanup 时不立即 delete，而是延迟检查**。

**最终采用的最简方案**：修改 VideoPreview 的 registry cleanup 逻辑，**在 delete 前检查注册的是否还是自己的 player**：

```tsx
// VideoPreview.tsx registry useEffect cleanup
return () => {
  const currentRegistry = (window as any).__msePlayers || {}
  // 仅当注册的还是当前 player 时才删除，避免删除其他实例的注册
  if (currentRegistry[roomId]?.player === playerRef.current) {
    delete currentRegistry[roomId]
  }
}
```

这样全屏 VideoPreview 卸载时，registry 中已经是全屏 player，它会 delete；但小预览区的 player 在全屏打开时已被覆盖（registry[roomId] 指向全屏 player），所以小预览区的 cleanup 不会 delete（因为 `currentRegistry[roomId]?.player !== playerRef.current`）。

**但问题在于**：全屏关闭后，小预览区的 player 仍在 registry 之外，需要重新注册。由于小预览区 VideoPreview 的 props 没变，registry useEffect 不会重新执行。

**真正可行的方案**：给小预览区 VideoPreview 添加 `key` prop，全屏状态变化时强制重新挂载。这需要 RoomCard 知道全屏状态。

**实施**：
1. Workbench 传递 `fullscreenRoomId` 到 RoomCard（或 RoomCard 从 store 读取）
2. RoomCard 内的 VideoPreview 添加 `key` 包含全屏状态
3. 全屏打开/关闭时，小预览区 VideoPreview 重新挂载，重新注册 player，重新调用 `player.start()`

**为什么**：
- 这是最直接的解决方案，确保全屏关闭后小预览区有新的 player 实例和 registry 注册
- 代价是全屏打开时小预览区会短暂停止（重新挂载），但这是可接受的，因为用户注意力在全屏窗口

## Assumptions & Decisions

### 假设
1. **直播流通常包含音频轨**：大多数直播平台（抖音、B站、斗鱼等）的 FLV/HLS 流都包含 AAC 音频轨
2. **antd `<App>` 组件能解决静态 message 警告**：这是 antd v5 官方推荐做法
3. **`-map 0:a?` 的 `?` 后缀在 FFmpeg 中表示可选映射**：无音频轨时不报错，有音频轨时正常映射
4. **RoomCard 能从 store 获取 fullscreenRoomId**：项目已使用 zustand store（useAppStore）

### 决策
1. **录制错误修复选择从 `room.last_error` 获取**：而非让 `manager.start_recording` 抛异常，因为现有接口契约是返回 bool
2. **音频修复选择移除 anullsrc 而非保留**：anullsrc 是 workaround，副作用（永远静音）大于收益（codec 匹配），且 `-map 0:a?` 已处理无音频轨情况
3. **全屏问题修复选择 key 强制重新挂载**：比修改 registry cleanup 逻辑更直接、更可靠，且副作用（小预览区短暂停止）可接受
4. **不修改 VideoPreview 的 registry cleanup 逻辑**：保持现有行为，通过 key 重新挂载来解决
5. **m3u8 分支的 `-live_start_index -1` 保留**：但需调整 insert 位置（现在只有 1 个 `-i`，要插入到它前面）

## Verification steps

### 1. TypeScript 编译验证
```powershell
cd "d:\Project\直播切片多人\lsc-electron"; npx tsc --noEmit
```
预期：无错误

### 2. Python 语法验证
```powershell
cd "d:\Project\直播切片多人"; python -c "import lsc.core.services.mse_streamer; print('OK')"
```
预期：输出 `OK`

### 3. 重启程序验证
- 终止现有 electron / python 进程
- 在新 PowerShell 窗口启动 `npm run dev`

### 4. 录制功能验证
- 添加房间并连接
- 点击"开始录制"
- 预期：不再显示"未知错误"，而是显示具体错误原因（如"房间未连接"）或"录制已开始"
- 控制台不再出现 antd message 静态函数警告

### 5. 预览声音验证
- 启用房间预览
- 点击静音按钮切换为"取消静音"
- 预期：能听到直播流的声音
- 点击静音按钮切换为"静音"
- 预期：声音消失

### 6. 全屏关闭后小预览恢复验证
- 启用房间预览，确认小预览区有画面
- 点击"全屏放大"按钮
- 全屏窗口显示画面
- 关闭全屏窗口
- 预期：小预览区画面恢复播放，不再停止

### 7. 后端日志验证
- 检查 `C:\Users\Administrator\AppData\Roaming\lsc-electron\logs\backend.log`
- 确认 `FFmpeg command:` 日志中不再有 `anullsrc`，改为 `-map 0:a?`
- 确认无 FFmpeg 启动错误
