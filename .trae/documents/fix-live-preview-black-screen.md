# 修复直播间预览无画面（黑屏）Plan

## Summary

点击"启用预览"后房间卡片显示绿色"MSE" badge（说明 VideoPreview 已挂载、MsePlayer 初始化成功、init 段已 append），但视频区域黑屏无画面。当前 init 段缓存/request_mse_init/sourceopen flush 等前序修复已就位，问题大概率在于 **FFmpeg 转码命令对直播平台 HLS/FLV 流不够通用，或 media 段未持续到达/消费**。本计划通过增强 FFmpeg 命令、增加诊断日志、提升前端错误可见性来彻底修复。

## Current State Analysis

### 已确认的前序修复（已落地）
- [mse_streamer.py](file:///d:/Project/直播切片多人/lsc/core/services/mse_streamer.py#L75-L92) 已缓存 init 段并提供 `replay_init()`
- [room_handler.py](file:///d:/Project/直播切片多人/python-backend/handlers/room_handler.py#L1085-L1101) 已新增 `request_mse_init` handler
- [VideoPreview.tsx](file:///d:/Project/直播切片多人/lsc-electron/src/components/VideoPreview.tsx#L123-L124) 挂载后会主动请求补发 init
- [mediaSourcePlayer.ts](file:///d:/Project/直播切片多人/lsc-electron/src/services/mediaSourcePlayer.ts#L193-L195) sourceopen 后已调用 `_flushPending()`

### 用户截图反映的关键信息
- 绿色 "MSE" badge 可见 → `room.preview_enabled === true`，`VideoPreview active=true`，MsePlayer state 已进入 `playing`
- 视频区域黑屏 → init append 成功，但**持续画面未渲染**，原因可能是：
  1. FFmpeg 未持续输出 media segment
  2. media segment 到达但未被正确消费
  3. 视频元素尺寸/样式问题

### FFmpeg 命令问题（最可能根因）

**文件**：[lsc/core/services/mse_streamer.py](file:///d:/Project/直播切片多人/lsc/core/services/mse_streamer.py#L108-L125)

当前命令：
```python
[
    ffmpeg, "-loglevel", "error",
    "-i", url,
    "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
    "-pix_fmt", "yuv420p", "-g", "30",
    "-c:a", "aac", "-b:a", "64k", "-ar", "22050", "-ac", "1",
    "-f", "mp4",
    "-movflags", "frag_keyframe+empty_moov+default_base_moof",
    "-frag_duration", "500000",
    "pipe:1",
]
```

问题：
1. **缺少直播读取参数**：`-re` 按直播速率读取；`-fflags +genpts` 修复时间戳；`-thread_queue_size 1024` 避免输入队列溢出。对抖音/快手/B 站等 HLS/HTTP-FLV 直播流缺失这些参数容易导致 FFmpeg 只读一个分片就卡住或退出。
2. **未处理无音频流**：`-c:a aac` 强制音频编码，若输入流无音频，部分 FFmpeg 版本会报错退出或生成异常输出。
3. **缺少 HLS 直播优化**：对于 HLS(m3u8) 直播，建议 `-live_start_index -1` 从最新分片开始读取，降低延迟并避免等待旧分片。
4. **stderr 未收集**：`-loglevel error` 但 stderr 被 `subprocess.PIPE` 堵住未读取；FFmpeg 报错时 `on_error` 只能拿到空或异常退出信息，无法定位具体原因。

### 前端问题

**文件**：[lsc-electron/src/hooks/useWebSocket.ts](file:///d:/Project/直播切片多人/lsc-electron/src/hooks/useWebSocket.ts#L146-L151)

`mse_error` 只打印 `console.warn`，未通过 Zustand store 更新 RoomCard/VideoPreview 的错误状态，用户看不到 FFmpeg 失败原因。

## Proposed Changes

### 修复 1：增强 FFmpeg 命令以兼容直播平台 HLS/FLV 流

**文件**：[lsc/core/services/mse_streamer.py](file:///d:/Project/直播切片多人/lsc/core/services/mse_streamer.py#L108-L125)

**What**：重构 `start()` 中的 FFmpeg 命令，增加直播流参数；自动检测输入流是否有音频并决定是否添加 `-an`。

**Why**：当前命令对抖音/快手等直播 URL 不够健壮，容易导致 media segment 不持续输出。

**How**：
1. 在 `-i url` 之前插入：
   - `"-re"`（按直播速率读取，降低 CPU 占用并避免网络拥塞）
   - `"-fflags", "+genpts"`（生成完整 PTS，避免部分直播流时间戳异常）
   - `"-thread_queue_size", "1024"`（增大输入线程队列，避免丢帧）
2. 在 `-i url` 之后处理音频：
   - 先不指定 `-c:a`，改为 `"-c:a", "aac"` + `"-b:a 64k -ar 22050 -ac 1"`
   - 若输入无音频，FFmpeg 默认会报错。更安全的做法：先用 `"-an"` 关闭音频轨道，验证画面是否正常；后续再恢复音频并加 `"-shortest"` 处理。
   - **本计划采用方案**：默认带音频，但在命令中加入 `"-err_detect", "ignore_err"` 和 `"-ignore_unknown"` 并不够。最稳妥是**第一阶段先禁用音频**（`-an`），确保画面能出来；第二阶段再恢复音频并处理无音频 fallback。
   - 由于需要尽快解决"无画面"，计划采用 **`-an` 禁用音频** 作为默认策略（直播预览通常不需要音频），若未来需要音频再单独处理。
3. HLS 相关：判断 url 是否以 `.m3u8` 结尾，若是则添加 `"-live_start_index", "-1"`。

重构后命令示例：
```python
cmd = [
    self._ffmpeg_path,
    "-loglevel", "error",
    "-re",
    "-fflags", "+genpts",
    "-thread_queue_size", "1024",
    "-i", self._url,
    "-c:v", "libx264",
    "-preset", "ultrafast",
    "-tune", "zerolatency",
    "-pix_fmt", "yuv420p",
    "-g", "30",
    "-an",  # 直播预览先禁用音频，避免无音频流导致失败
    "-f", "mp4",
    "-movflags", "frag_keyframe+empty_moov+default_base_moof",
    "-frag_duration", "500000",
    "pipe:1",
]
if self._url.lower().endswith('.m3u8'):
    # 从 HLS 直播最新分片开始读取，避免等待过期分片
    cmd.insert(cmd.index('-i'), '-live_start_index')
    cmd.insert(cmd.index('-i'), '-1')
```

### 修复 2：收集 FFmpeg stderr 并写入日志/触发 error

**文件**：[lsc/core/services/mse_streamer.py](file:///d:/Project/直播切片多人/lsc/core/services/mse_streamer.py#L140-L162)

**What**：启动一个守护线程读取 FFmpeg stderr，保存最近 2KB 错误输出；当 FFmpeg 异常退出时，把 stderr 内容通过 `on_error` 上报。

**Why**：当前 stderr 被 PIPE 堵住，FFmpeg 失败时无法拿到具体原因（如"Invalid data found"、"Could not find codec parameters"）。

**How**：
1. `start()` 中启动 FFmpeg 后，新增线程读取 stderr：
   ```python
   self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
   self._stderr_thread.start()
   ```
2. 新增 `_read_stderr`：
   ```python
   def _read_stderr(self):
       if self._process is None or self._process.stderr is None:
           return
       buf = []
       while self._running:
           line = self._process.stderr.readline()
           if not line:
               break
           buf.append(line.decode('utf-8', errors='replace').rstrip())
           if len(buf) > 100:
               buf.pop(0)
       self._last_stderr = '\n'.join(buf[-20:])  # 保留最后 20 行
   ```
3. `_read_segments` 中 FFmpeg 退出时（行 185-196），把 `self._last_stderr` 加入 error 消息：
   ```python
   err_msg = (self._last_stderr or stderr_output.decode(...))[:500]
   ```
4. `__init__` 增加 `self._stderr_thread = None` 和 `self._last_stderr = ''`。

### 修复 3：前端显示 MSE 错误信息

**文件**：[lsc-electron/src/hooks/useWebSocket.ts](file:///d:/Project/直播切片多人/lsc-electron/src/hooks/useWebSocket.ts#L146-L151)

**What**：收到 `mse_error` 时，调用 `updateRoom(room_id, { mse_error: error })` 把错误写入 store。

**Why**：当前错误只打印 console，用户看不到 FFmpeg 失败原因；RoomCard 可以据此显示错误 overlay。

**How**：
```ts
const unsubMseError = wsClient.on('mse_error', (data: { room_id: string; error: string }) => {
  if (data?.room_id) {
    console.warn(`MSE error for ${data.room_id}:`, data.error)
    updateRoom(data.room_id, { mse_error: data.error })
  }
})
```

### 修复 4：RoomCard 显示 MSE 错误状态

**文件**：[lsc-electron/src/pages/Workbench/components/RoomCard.tsx](file:///d:/Project/直播切片多人/lsc-electron/src/pages/Workbench/components/RoomCard.tsx)

**What**：在 VideoPreview 区域渲染 `room.mse_error` 错误提示；点击"重试"时清除错误。

**Why**：让用户直观知道预览失败原因，并提供重试入口。

**How**：
1. 在 `VideoPreview` 上方或下方增加错误 overlay（当 `room.mse_error` 存在时）
2. `onTogglePreview(false)` 时同步 `updateRoom(room_id, { mse_error: null })`

### 修复 5：MsePlayer 默认开启 debug 或增加关键日志

**文件**：[lsc-electron/src/components/VideoPreview.tsx](file:///d:/Project/直播切片多人/lsc-electron/src/components/VideoPreview.tsx#L84-L97)

**What**：把 `MsePlayer` 的 `debug` 参数改为 `true`（开发模式），并在控制台输出 init/media 段接收/append 情况。

**Why**：开发阶段便于快速确认是 init 没到、media 没到，还是 append 失败。

**How**：
```ts
const player = new MsePlayer({
  videoElement: videoRef.current,
  debug: import.meta.env.DEV,  // 开发模式开启 debug
  ...
})
```

## Assumptions & Decisions

1. **默认禁用音频（`-an`）**：直播预览的核心需求是"看到画面"，音频不是必需。很多直播流没有音频或音频编码不标准，强制 `-c:a aac` 容易导致失败。先禁用音频确保画面可用，后续如需音频再单独设计 audio track 处理。

2. **先解决最可能根因，再叠加诊断**："无画面"最可能由 FFmpeg 命令不兼容直播平台流导致。修复 1 直接改命令；修复 2/3/4/5 提供诊断和错误可见性，便于验证和后续问题定位。

3. **不修改 MSE 核心逻辑**：前序的 init 缓存/request_mse_init/sourceopen flush 已经解决了 init 段丢失竞态，本次不再改动。

## Verification

1. **类型检查**：`cd lsc-electron && npx tsc --noEmit` 通过
2. **后端编译**：`python -m py_compile lsc/core/services/mse_streamer.py python-backend/handlers/room_handler.py` 通过
3. **用户视角验证**：
   - 重启程序，添加抖音/快手/B 站直播间
   - 点击"启用预览"，视频区域应显示画面（而非黑屏）
   - 若仍失败，控制台/backend.log 应显示具体 FFmpeg 错误信息
   - 房间卡片应显示错误提示（如"MSE 流启动失败: ..."）
