# 优化全屏切换卡顿

## 背景与现状

用户反馈：退出全屏再全屏会卡顿。这是在前一次修复"退出全屏后小预览卡住"后出现的新问题——前一次修复通过**完全重建 MsePlayer** 解决了卡死，但引入了重建延迟。

### 根因分析

**全屏切换的完整时序与延迟来源**：

每次全屏切换都涉及 MsePlayer 的创建/重建，延迟来自 4 个环节：
1. `new MsePlayer()` + `player.start()` → MediaSource 异步初始化（sourceopen 事件）
2. `request_mse_init` → 等待后端响应 mse_init（网络往返 200-500ms）
3. 等待后端推送下一个 mse_segment（streamer 推送间隔 200-500ms）
4. live-edge 对齐 + `_tryPlay` 重试（50-200ms）

**退出全屏**（[VideoPreview.tsx#L202-L276](file:///d:/Project/直播切片多人/lsc-electron/src/components/VideoPreview.tsx#L202-L276)）：
- 当前逻辑：`stop()` 旧 player → `new MsePlayer()` → `start()` → 注册 registry → `request_mse_init` → 等待 init/media 段
- 延迟：1-2 秒

**再次全屏**（[VideoPreview.tsx#L92-L153](file:///d:/Project/直播切片多人/lsc-electron/src/components/VideoPreview.tsx#L92-L153)）：
- Modal VideoPreview 新挂载 → `new MsePlayer()` → `start()` → 注册 registry（覆盖小预览）→ `request_mse_init` → 等待 init/media 段
- 延迟：1-2 秒

**累计延迟**：2-4 秒，用户感知为"卡顿"。

**关键洞察**：退出全屏时，小预览的 MsePlayer 在全屏期间并未被 `stop()`（只是 registry 被覆盖、收不到 segment），其 MediaSource/SourceBuffer 仍然存活。当前修复方案无条件重建 player 是过度处理——大多数情况下 player 状态正常，只需恢复 registry 注册并重新对齐 live-edge 即可。

## 修复方案

### 修改 1：MsePlayer 暴露 resetLiveEdgeAligned 方法

**文件**：[d:\Project\直播切片多人\lsc-electron\src\services\mediaSourcePlayer.ts](file:///d:/Project/直播切片多人/lsc-electron/src/services/mediaSourcePlayer.ts)

**位置**：在 `setMuted` 方法（第 185-189 行）附近添加

```ts
/** 重置 live-edge 对齐标志，允许下次 updateend 重新对齐 currentTime。
 * 用于全屏切换后恢复小预览播放：player 仍存活但 currentTime 可能落后 buffered 范围，
 * 重置后新 segment 到达会触发 live-edge 对齐。 */
resetLiveEdgeAligned(): void {
  this._liveEdgeAligned = false
}
```

**为什么**：全屏期间小预览 player 收不到 segment，currentTime 停留在旧位置。恢复 registry 后新 segment 到达，但 `_liveEdgeAligned=true`（首次播放时设置过）导致 live-edge 对齐逻辑（[mediaSourcePlayer.ts#L336-L359](file:///d:/Project/直播切片多人/lsc-electron/src/services/mediaSourcePlayer.ts#L336-L359)）不再执行，currentTime 落在 buffered 之外，播放卡死。重置标志后对齐逻辑会重新生效。

### 修改 2：useWebSocket 导出 getMseInitCache 函数

**文件**：[d:\Project\直播切片多人\lsc-electron\src\hooks\useWebSocket.ts](file:///d:/Project/直播切片多人/lsc-electron/src/hooks/useWebSocket.ts)

**位置**：在 `drainPendingMseSegments` 导出函数（第 57-60 行）附近添加

```ts
/** 获取某房间缓存的 init 段（不解码，返回 ArrayBuffer 或 null）。
 * 供 VideoPreview 创建 player 时优先用缓存 init 段 feedInit，
 * 避免等待 request_mse_init 往返。 */
export function getMseInitCache(roomId: string): ArrayBuffer | null {
  const hex = _mseInitCache[roomId]
  if (!hex) return null
  return _decodeHexSegment(hex)
}
```

**为什么**：`_mseInitCache`（第 15 行）是模块级私有变量，已有缓存逻辑（`_cacheMseInit` 在收到 mse_init 时自动缓存）。导出 getter 让 VideoPreview 创建 player 时能优先用缓存 init 段，跳过 `request_mse_init` 网络往返（200-500ms）。

### 修改 3：退出全屏时优先复用 player，不重建

**文件**：[d:\Project\直播切片多人\lsc-electron\src\components\VideoPreview.tsx](file:///d:/Project/直播切片多人/lsc-electron/src/components/VideoPreview.tsx)

**位置**：第 202-276 行的 `isFullscreenActive` useEffect

**修改后**：
```tsx
// 全屏从打开变为关闭时，优先复用小预览 player（不重建），仅恢复 registry + live-edge 对齐。
// 仅当 player 状态异常（error/idle）时才回退到重建。
// 解决前一次修复"无条件重建"导致的全屏切换卡顿（1-2 秒 → < 200ms）
const lastFullscreenRef = useRef(false)
useEffect(() => {
  if (lastFullscreenRef.current && !isFullscreenActive && videoRef.current) {
    const existingPlayer = playerRef.current
    // 优先复用：player 存活且状态正常（非 error/idle）
    if (existingPlayer && existingPlayer.state !== 'error' && existingPlayer.state !== 'idle') {
      console.log(`[VideoPreview] Fullscreen closed, reusing player (${roomId})`)
      // 1. 重置 live-edge 对齐标志，允许新 segment 到达后重新对齐 currentTime
      existingPlayer.resetLiveEdgeAligned()
      // 2. 恢复 registry 注册（全屏 player 卸载时已删除槽位）
      const registry = (window as any).__msePlayers || {}
      registry[roomId] = { feedInit, feedMedia, player: existingPlayer }
      ;(window as any).__msePlayers = registry
      // 3. 主动 seek 到 live edge 并 play，立即恢复播放
      //    （不等 updateend 事件，避免 200-500ms segment 等待）
      const video = videoRef.current
      if (video.buffered.length > 0) {
        const bufEnd = video.buffered.end(video.buffered.length - 1)
        const target = Math.max(video.buffered.start(0), bufEnd - 0.5)
        if (video.currentTime < video.buffered.start(0) || video.currentTime > bufEnd) {
          video.currentTime = target
          console.log(`[VideoPreview] Seek to live edge: ${target.toFixed(2)} (buffered ${video.buffered.start(0).toFixed(2)}-${bufEnd.toFixed(2)})`)
        }
      }
      video.play().catch(() => {})
      // 4. 回放缓存的 media 段（全屏期间可能积累了新段）
      const pendingSegments = drainPendingMseSegments(roomId)
      if (pendingSegments.length > 0) {
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
    } else {
      // 回退：player 不存在或状态异常，完全重建（原逻辑）
      console.log(`[VideoPreview] Fullscreen closed, rebuilding player (${roomId}, state=${existingPlayer?.state ?? 'null'})`)
      // ... 保留原有的重建逻辑（stop + new MsePlayer + start + 注册 + request_mse_init + drain）
    }
  }
  lastFullscreenRef.current = isFullscreenActive
}, [isFullscreenActive, roomId, feedInit, feedMedia])
```

**为什么**：
- 小预览 player 在全屏期间未被 stop，MediaSource/SourceBuffer 仍存活，状态通常正常
- 只需恢复 registry + seek + play，延迟 < 200ms（相比重建 1-2 秒）
- `resetLiveEdgeAligned` 确保后续 segment 到达时能重新对齐
- 主动 seek 到 `buffered.end - 0.5` 立即恢复播放，不等 updateend 事件
- `drainPendingMseSegments` 回放全屏期间积累的段（如果有）
- player 状态异常（error/idle）时回退到重建，保证健壮性

### 修改 4：VideoPreview 创建 player 时优先用缓存 init 段

**文件**：[d:\Project\直播切片多人\lsc-electron\src\components\VideoPreview.tsx](file:///d:/Project/直播切片多人/lsc-electron/src/components/VideoPreview.tsx)

**位置**：第 92-153 行的初始化 useEffect（影响全屏 VideoPreview 和首次挂载）

**修改**：在 `player.start(roomId)`（第 137 行）之后添加缓存 init 段喂入

```tsx
playerRef.current = player
onReadyRef.current?.(player)
player.start(roomId)

// 优先用缓存的 init 段 feedInit，避免等待 request_mse_init 往返（200-500ms）
// _mseInitCache 在后端推送 mse_init 时自动缓存，首次挂载无缓存则等 request_mse_init
const cachedInit = getMseInitCache(roomId)
if (cachedInit) {
  player.feedInit(cachedInit)
  console.log(`[VideoPreview] Used cached init segment (${roomId})`)
}
```

**为什么**：
- 全屏 VideoPreview 新挂载时，`_mseInitCache` 通常已有该房间的 init 段（小预览之前播放时缓存过）
- 用缓存 init 段立即 feedInit，SourceBuffer 立即创建，不等 `request_mse_init` 往返
- 节省 200-500ms 延迟
- 首次挂载（无缓存）时退化为原有逻辑（等 request_mse_init）

**注意**：此修改同时影响重建 player 的路径（修改 3 的回退分支）。重建时也需要在 `player.start(roomId)` 后用缓存 init 段。由于回退分支保留原有重建逻辑，需要确保重建逻辑中也加入缓存 init 段的使用。为简化实现，将缓存 init 段逻辑提取到 `player.start` 调用后的公共代码路径。

实际上，修改 3 的"复用"分支不需要 feedInit（player 已有 init 段），只有"重建"分支和"首次挂载"需要。为避免重复代码，将缓存 init 段逻辑放在修改 4 的初始化 useEffect 中（第 92-153 行），重建分支（修改 3 的回退）也复用相同的 player 创建代码。

## 验证步骤

1. **TypeScript 编译**：
   ```bash
   cd d:\Project\直播切片多人\lsc-electron && npx tsc --noEmit
   ```

2. **功能验证**：
   - 添加房间 → 连接 → 启用预览（小预览正常播放）
   - 点击全屏 → **预期**：全屏 0.5-1 秒内开始播放（缓存 init 段加速）
   - 关闭全屏 → **预期**：小预览 < 200ms 内恢复播放（复用 player + seek）
   - 再次全屏 → **预期**：同首次全屏，0.5-1 秒内开始播放
   - 控制台应看到：
     - `[VideoPreview] Used cached init segment (xxx)`（全屏打开时）
     - `[VideoPreview] Fullscreen closed, reusing player (xxx)`（全屏关闭时）
     - `[VideoPreview] Seek to live edge: xxx`（恢复播放时）

3. **多次切换验证**：
   - 反复打开/关闭全屏 10 次
   - 每次切换都应快速响应（< 1 秒）
   - 不应有 ffmpeg 进程泄漏

4. **异常回退验证**：
   - 模拟 player 错误（如断网导致 MSE error）
   - 退出全屏时应看到 `rebuilding player` 日志（回退到重建）
   - 重建后小预览应恢复

## 假设与决策

1. **优先复用而非重建**：小预览 player 在全屏期间未被 stop，状态通常正常。复用比重建快 5-10 倍（200ms vs 1-2s）。
2. **保留重建作为回退**：player 状态异常（error/idle）时仍需重建，保证健壮性。
3. **不修改后端**：后端 streamer 持续推送 segment，`_mseInitCache` 自动缓存 init 段，无需后端改动。
4. **不引入 player 共享或多实例 registry**：当前架构 registry 是单槽位，复用 player 已足够解决卡顿，无需大改。
5. **主动 seek 而非等 updateend**：全屏关闭时 player 的 buffered 范围可能已推进，主动 seek 到 `buffered.end - 0.5` 立即恢复播放，不等下一个 segment 的 updateend 事件。
6. **缓存 init 段对首次挂载无影响**：首次挂载时 `_mseInitCache` 为空，`getMseInitCache` 返回 null，退化为原有 `request_mse_init` 逻辑。
