# 修复 MSE 预览无画面 Plan

## Summary

预览点击后无画面，根因是 **init 段（ftyp+moov）丢失**，导致 MediaSource 无法解码后续 media 段，player 永久卡死在 loading。存在两处叠加竞态，需多层防御彻底修复。

## Current State Analysis

### 数据流
1. 后端 `_handle_mse_preview` 启动 `MseStreamer`（FFmpeg 转码为 fMP4）
2. FFmpeg 产出 init 段 → `on_init_segment` 回调 → `srv.broadcast('mse_init')`
3. 后端 `bridge.call` 设 `preview_enabled=True` → `_broadcast_rooms()` 广播 `rooms_updated`
4. 前端收到 `rooms_updated` → `RoomCard` 渲染 `VideoPreview` → `useEffect` 创建 `MsePlayer` 并注册到 `window.__msePlayers`
5. 前端收到 `mse_init` → `feedMseSegment` 从 registry 取 player → `feedInit()`

### 竞态 A（主因，后端时序）
- [room_handler.py#L1044-L1060](file:///d:/Project/直播切片多人/python-backend/handlers/room_handler.py#L1044-L1060)
- `streamer.start()`（行 1044）启动 FFmpeg 后，`_read_segments` 守护线程并发读取。FFmpeg 产出 init 段时，通过 `asyncio.run_coroutine_threadsafe(srv.broadcast('mse_init'), loop)` 投递到主 asyncio 循环。
- 主协程此时在 `await run_in_executor(bridge.call(_set_preview_enabled))`（行 1057-1059）期间空闲，会立即拾取并执行 `mse_init` 广播。
- 因此 **`mse_init` 早于 `rooms_updated`（行 1060）到达前端**。
- 前端 `VideoPreview` 未挂载，`window.__msePlayers[roomId]` 为空。
- [useWebSocket.ts#L114-L132](file:///d:/Project/直播切片多人/lsc-electron/src/hooks/useWebSocket.ts#L114-L132) 的 `feedMseSegment` 在 player 不存在时**静默丢弃，无缓冲无重试**。
- init 段只发送一次（[mse_streamer.py#L209-L225](file:///d:/Project/直播切片多人/lsc/core/services/mse_streamer.py#L209-L225) `seen_ftyp`/`_init_sent` 不可逆），错过则无法恢复。

### 竞态 B（次因，前端 SourceBuffer 异步）
- [mediaSourcePlayer.ts#L157-L210](file:///d:/Project/直播切片多人/lsc-electron/src/services/mediaSourcePlayer.ts#L157-L210)
- `player.start()` 设 `video.src` 后 `sourceopen` 是异步事件，此时 `_sourceBuffer === null`。
- 若 `mse_init` 在 `sourceopen` 之前到达，[feedInit#L75-L78](file:///d:/Project/直播切片多人/lsc-electron/src/services/mediaSourcePlayer.ts#L75-L78) 把 init `unshift` 进 `_pendingSegments`。
- `sourceopen` 回调（行 166-197）创建 SourceBuffer 后**未调用 `_flushPending()`**，缓冲的 init 永远不会被消费。
- 后续 media 段因 `_initReceived === false` 也入队，队列超 60 时 `shift()` 丢弃（含队首 init），player 永久卡死。

## Proposed Changes

### 修复 1：后端缓存 init 段 + 支持重发（根治竞态 A）

**文件**：[lsc/core/services/mse_streamer.py](file:///d:/Project/直播切片多人/lsc/core/services/mse_streamer.py)

**What**：`MseStreamer` 缓存最近一次 init 段，新增 `replay_init()` 方法重发。

**Why**：init 段只发送一次且不可逆，前端错过则永久无法解码。缓存后支持按需重发，彻底消除时序依赖。

**How**：
- `__init__` 新增 `self._last_init_segment: bytes | None = None`
- `_read_segments` 发送 init 段时（行 222-223 附近）同步缓存：`self._last_init_segment = init_data`
- 新增方法：
  ```python
  def replay_init(self) -> bool:
      """重发缓存的 init 段。返回 True 表示有缓存可发，False 表示尚无 init 段。"""
      if self._last_init_segment is None:
          return False
      self._on_init(self._last_init_segment)
      return True
  ```

### 修复 2：后端新增 request_mse_init handler（配合修复 1）

**文件**：[python-backend/handlers/room_handler.py](file:///d:/Project/直播切片多人/python-backend/handlers/room_handler.py)

**What**：新增 `request_mse_init` 消息处理，前端挂载 VideoPreview 后主动请求补发 init 段。

**Why**：前端挂载时机由 `rooms_updated` 驱动，无法保证早于 FFmpeg 产出 init。主动请求 + 后端缓存重发 = 不依赖任何时序假设。

**How**：
- 在 `register_room_handlers` 内新增 handler：
  ```python
  @server.on('request_mse_init')
  async def handle_request_mse_init(data):
      room_id = data.get('room_id')
      if not room_id:
          return {'success': False, 'error': 'room_id is required'}
      with _mse_streamers_lock:
          streamer = _mse_streamers.get(room_id)
      if streamer is None:
          return {'success': False, 'error': 'MSE 流未启动'}
      ok = streamer.replay_init()
      return {'success': ok, 'note': 'init replayed' if ok else 'init not ready yet'}
  ```
- 注意：`replay_init()` 内部调用 `self._on_init(...)`，会通过 `asyncio.run_coroutine_threadsafe(srv.broadcast('mse_init'), loop)` 投递广播。`_on_init` 是 `MseStreamer` 的回调，线程安全（只读 `self._last_init_segment`）。

### 修复 3：前端 VideoPreview 挂载后主动请求 init（触发修复 2）

**文件**：[lsc-electron/src/components/VideoPreview.tsx](file:///d:/Project/直播切片多人/lsc-electron/src/components/VideoPreview.tsx)

**What**：第二个 useEffect（注册 player 到 registry）中，注册完成后立即发送 `request_mse_init`。

**Why**：player 注册成功后立即请求补发 init，确保即使竞态 A 发生（init 之前被丢弃），也能在此刻拿到缓存的 init 段。

**How**：
- 在第二个 useEffect（行 117-128）的 `registry[roomId] = {...}` 之后添加：
  ```ts
  // 主动请求后端补发 init 段，消除 mse_init 早于 rooms_updated 到达的竞态
  sendRef.current('request_mse_init', { room_id: roomId })
  ```
- 使用 `sendRef.current`（已存在的 ref）而非 `send`，避免引入新的依赖不稳定问题。

### 修复 4：前端 MsePlayer sourceopen 补 flush（根治竞态 B）

**文件**：[lsc-electron/src/services/mediaSourcePlayer.ts](file:///d:/Project/直播切片多人/lsc-electron/src/services/mediaSourcePlayer.ts)

**What**：`sourceopen` 回调创建 SourceBuffer 后调用 `_flushPending()`。

**Why**：若 init 在 SourceBuffer 就绪前到达被缓冲，`sourceopen` 后必须主动 flush，否则缓冲的 init 永远不会被消费。

**How**：
- 在 `sourceopen` 回调（行 166-197）内，`addSourceBuffer` 成功并注册事件监听器后（行 193 `_log` 之前）添加：
  ```ts
  // SourceBuffer 就绪后立即 flush 缓冲的 init/media 段，消除 sourceopen 异步导致的竞态
  this._flushPending()
  ```

## Assumptions & Decisions

1. **不调整后端时序**（不把 `_broadcast_rooms` 移到 `streamer.start()` 之前）：保持当前"FFmpeg 启动成功才设 preview_enabled"的语义，避免 FFmpeg 失败后前端渲染 VideoPreview 又需回滚状态。改用"前端主动请求 init 重发"彻底解耦时序。

2. **不在前端 WS 层缓冲 init**：有了 `request_mse_init` 重发机制后，WS 层缓冲是冗余的复杂度。三层防御（后端缓存 + 前端请求 + sourceopen flush）已足够。

3. **`replay_init` 线程安全**：`_last_init_segment` 是 `bytes`（不可变），`replay_init` 只读不写。`_on_init` 回调内部用 `asyncio.run_coroutine_threadsafe` 投递到事件循环，线程安全。无需加锁。

4. **`request_mse_init` 在 FFmpeg 尚未产出 init 时返回 not ready**：前端收到 `{success: false, note: 'init not ready yet'}` 时不做特殊处理——FFmpeg 会持续推送 media 段，但无 init 无法解码。这种情况下用户需点"重试"重新触发完整流程。实际场景中 FFmpeg 产出 init 通常在几百 ms 内，而前端挂载 + request 往返也是几百 ms，绝大多数情况下 `replay_init` 能命中缓存。

## Verification

1. **类型检查**：`cd lsc-electron && npx tsc --noEmit` 通过
2. **后端编译**：`python -m py_compile python-backend/handlers/room_handler.py lsc/core/services/mse_streamer.py` 通过
3. **后端测试**：`python -m pytest -q` 不新增失败（预存 10 个失败与本修改无关）
4. **用户视角验证**：
   - 启动程序，banner 不再误报"无法连接到后端"（MainLayout 防抖已在前序修复）
   - 点击"启用预览"后房间状态稳定不闪烁（前序 spec 修复）
   - 点击"启用预览"后**画面正常显示**（本计划核心目标）
   - FFmpeg 失败后自动回到"启用预览"按钮状态（前序 spec 修复）
