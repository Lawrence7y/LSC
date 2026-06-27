# 修复退出全屏后小预览卡住

## 背景与现状

用户反馈：退出全屏后，原来房间的小预览区画面卡住，无法继续播放。

### 根因分析

全屏 Modal 的 VideoPreview 与 RoomCard 小预览的 VideoPreview **共用同一个 roomId**，注册到 `window.__msePlayers[roomId]` 同一槽位，导致冲突：

**完整时序**：

1. **全屏打开**：
   - Modal 的 VideoPreview 挂载（[Workbench/index.tsx#L1131-L1138](file:///d:/Project/直播切片多人/lsc-electron/src/pages/Workbench/index.tsx#L1131-L1138)）
   - 其 useEffect（[VideoPreview.tsx#L156-L190](file:///d:/Project/直播切片多人/lsc-electron/src/components/VideoPreview.tsx#L156-L190)）注册到 `registry[roomId]`，**覆盖**小预览的注册
   - 后端 mse_segment 全部喂给全屏 player，小预览 player 收不到数据

2. **全屏期间**：
   - 小预览的 MsePlayer 仍挂载，但收不到 segment
   - 小预览的 `<video>` 元素可能因缓冲区耗尽而停顿，显示最后一帧（卡住）

3. **全屏关闭**：
   - Modal 的 VideoPreview 卸载 → cleanup（[VideoPreview.tsx#L181-L188](file:///d:/Project/直播切片多人/lsc-electron/src/components/VideoPreview.tsx#L181-L188)）检查 `currentRegistry[roomId]?.player === playerRef.current`，全屏 player 已覆盖槽位，所以条件成立，**删除 registry 槽位**
   - `isFullscreenActive` useEffect（[VideoPreview.tsx#L205-L214](file:///d:/Project/直播切片多人/lsc-electron/src/components/VideoPreview.tsx#L205-L214)）检测到 true→false，重新注册小预览 player 到 registry

4. **关键缺陷**：
   - 小预览的 MsePlayer 在全屏期间收不到数据，其 MediaSource/SourceBuffer 可能处于异常状态（如 SourceBuffer 被 endOfStream 关闭、buffered 范围与 currentTime 严重不匹配、readyState 降级）
   - 重新注册后，segment 喂给这个状态异常的 player，无法恢复播放
   - 即使 player 状态正常，`currentTime` 仍停留在全屏打开前的位置，而新 segment 的时间戳已推进，live-edge 对齐逻辑（[mediaSourcePlayer.ts#L336-L359](file:///d:/Project/直播切片多人/lsc-electron/src/services/mediaSourcePlayer.ts#L336-L359)）的 `_liveEdgeAligned` 标志已是 true（首次播放时设置过），不会再次对齐，导致 currentTime 落在 buffered 之外，播放卡死

**根本问题**：两个 VideoPreview 实例共享同一 roomId + registry 槽位，全屏切换时小预览的 player 状态被破坏且无法自愈。

## 修复方案

### 修改 1：全屏关闭后强制重建小预览的 MsePlayer

**文件**：[d:\Project\直播切片多人\lsc-electron\src\components\VideoPreview.tsx](file:///d:/Project/直播切片多人/lsc-electron/src/components/VideoPreview.tsx)

**思路**：当 `isFullscreenActive` 从 true→false 时，不只是重新注册 registry，而是**完全重建** MsePlayer（stop 旧的 + 创建新的），确保 player 状态干净。重建后重新注册 registry 并请求 init 段。

**修改位置**：第 202-214 行的 `isFullscreenActive` useEffect

**修改后**：
```tsx
// 全屏从打开变为关闭时，完全重建 MsePlayer 并重新注册 registry
// 解决全屏期间小预览 player 状态被破坏（收不到 segment、currentTime 错位、
// _liveEdgeAligned 标志未重置）导致退出全屏后卡住的问题
const lastFullscreenRef = useRef(false)
useEffect(() => {
  if (lastFullscreenRef.current && !isFullscreenActive && videoRef.current) {
    console.log(`[VideoPreview] Fullscreen closed, rebuilding player (${roomId})`)

    // 1. 停止旧 player，清理状态
    if (playerRef.current) {
      playerRef.current.stop()
      playerRef.current = null
    }

    // 2. 创建新 player，状态干净（_liveEdgeAligned=false, _initReceived=false）
    hasReceivedDataRef.current = false
    if (loadTimeoutRef.current) {
      clearTimeout(loadTimeoutRef.current)
    }
    loadTimeoutRef.current = setTimeout(() => {
      if (!hasReceivedDataRef.current && playerRef.current) {
        console.warn(`[VideoPreview] 预览加载超时 (${roomId})`)
        setError('预览加载超时，请检查直播流是否正常')
      }
    }, 15000)

    const player = new MsePlayer({
      videoElement: videoRef.current,
      debug: (import.meta as unknown as { env?: { DEV?: boolean } }).env?.DEV ?? false,
      onStateChange: (newState) => {
        setState(newState)
        if (newState === 'playing') {
          setError(null)
          hasReceivedDataRef.current = true
          if (loadTimeoutRef.current) {
            clearTimeout(loadTimeoutRef.current)
            loadTimeoutRef.current = null
          }
        }
      },
      onError: (msg) => {
        setError(msg)
        hasReceivedDataRef.current = true
        onErrorRef.current?.(msg)
      },
    })

    playerRef.current = player
    onReadyRef.current?.(player)
    player.start(roomId)

    // 3. 重新注册到 registry
    const registry = (window as any).__msePlayers || {}
    registry[roomId] = { feedInit, feedMedia, player: playerRef.current }
    ;(window as any).__msePlayers = registry

    // 4. 请求后端补发 init 段
    sendRef.current('request_mse_init', { room_id: roomId })

    // 5. 回放缓存的 media 段
    const pendingSegments = drainPendingMseSegments(roomId)
    if (pendingSegments.length > 0 && playerRef.current) {
      setTimeout(() => {
        pendingSegments.forEach(buf => {
          try {
            playerRef.current?.feedMedia(buf)
          } catch (e) {
            console.warn(`[VideoPreview] drain pending segment failed for ${roomId}:`, e)
          }
        })
      }, 0)
    }
  }
  lastFullscreenRef.current = isFullscreenActive
}, [isFullscreenActive, roomId, feedInit, feedMedia])
```

**为什么**：
- `stop()` 清理旧 player 的 MediaSource/SourceBuffer/事件监听器，避免资源泄漏
- 新 player 的 `_liveEdgeAligned=false`，会在首个 segment 到达时重新执行 live-edge 对齐
- 新 player 的 `_initReceived=false`，会正确处理后端补发的 init 段
- 重新注册 registry 确保后续 segment 喂给新 player
- `request_mse_init` 请求后端补发 init 段（旧 player 的 init 段已随 stop 清理）
- `drainPendingMseSegments` 回放缓存的 media 段，避免重建期间丢帧

### 修改 2：全屏 VideoPreview 不注册到共享 registry（避免覆盖小预览）

**文件**：[d:\Project\直播切片多人\lsc-electron\src\components\VideoPreview.tsx](file:///d:/Project/直播切片多人/lsc-electron/src/components/VideoPreview.tsx)

**思路**：全屏 VideoPreview 不应覆盖小预览在 registry 中的注册。让全屏 VideoPreview 通过独立的 feedInit/feedMedia 闭包接收 segment，但不写入共享 registry。但这样会导致后端 segment 只能喂给 registry 中的一个 player。

**更优方案**：全屏 VideoPreview 复用小预览的 player（不创建新 player），只是把 video 元素切换到全屏的 video。但 React 组件隔离使这难以实现。

**实际采用的方案**：保持修改 1 的重建逻辑，但优化时序——全屏关闭时，先确保小预览 player 重建完成，再清理全屏 player 的 registry 注册。

由于修改 1 已通过"重建小预览 player + 重新注册"解决了问题，修改 2 不再需要。全屏 player 卸载时删除 registry 槽位是正确行为，因为小预览的重建逻辑会立即重新注册。

### 修改 3：RoomCard VideoPreview 的 key 稳定性

**文件**：[d:\Project\直播切片多人\lsc-electron\src\pages\Workbench\components\RoomCard.tsx](file:///d:/Project/直播切片多人/lsc-electron/src/pages/Workbench/components/RoomCard.tsx)

**检查**：第 235-238 行的 VideoPreview `key={`preview-${room.room_id}`}` 是稳定的，不随全屏状态变化。这是正确的——小预览在全屏期间不应被重新挂载。

无需修改。

## 验证步骤

1. **TypeScript 编译**：
   ```bash
   cd d:\Project\直播切片多人\lsc-electron && npx tsc --noEmit
   ```

2. **功能验证**：
   - 添加房间 → 连接 → 启用预览（小预览正常播放）
   - 点击全屏按钮（Modal 打开，全屏预览正常播放）
   - 关闭全屏（点击 Modal 关闭按钮或 ESC）
   - **预期**：小预览在 1-2 秒内恢复播放，不再卡住
   - 控制台应看到 `[VideoPreview] Fullscreen closed, rebuilding player (xxx)` 日志

3. **多次切换验证**：
   - 反复打开/关闭全屏 5 次
   - 每次关闭后小预览都应恢复播放
   - 不应有 ffmpeg 进程泄漏（后端 streamer 生命周期不变）

4. **多房间验证**：
   - 添加 2+ 房间并启用预览
   - 房间 A 打开全屏 → 关闭 → 小预览恢复
   - 切换到房间 B 打开全屏 → 关闭 → 小预览恢复
   - 房间 A 的小预览不应受影响

## 假设与决策

1. **不修改后端**：后端 streamer 在全屏切换期间持续推送 segment，无需改动。重建 player 后通过 `request_mse_init` 补发 init 段即可恢复。
2. **不引入 player 池或多实例 registry**：当前架构 registry 是单槽位设计，改为多实例会涉及 useWebSocket 的 segment 分发逻辑，改动过大。重建 player 是最小侵入的修复。
3. **保留 isFullscreenActive prop**：它是触发重建的关键信号，不能删除。
4. **loadTimeout 不发送 enable_preview false**：保留现有行为（[VideoPreview.tsx#L106-L108](file:///d:/Project/直播切片多人/lsc-electron/src/components/VideoPreview.tsx#L106-L108)），避免重建期间误停后端 streamer。
5. **重建可能有 1-2 秒黑屏**：从 request_mse_init 到 init 段到达需要网络往返，期间显示 loading 状态。这是可接受的，比卡死好。
