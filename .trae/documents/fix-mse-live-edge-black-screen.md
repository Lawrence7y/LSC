# 修复 MSE 直播预览黑屏：live-edge 对齐与 trim 逻辑修复

## Summary

MSE 直播预览黑屏的核心根因是：**`video.currentTime` 始终为 0，而 FFmpeg 输出的 fMP4 首个 media segment 的 `tfdt`（track fragment decode time）可能不为 0，导致 `currentTime=0` 落在 `video.buffered` 范围之外**。Chromium 在这种情况下会：
1. `play()` Promise 一直 pending（无法在 currentTime=0 处解码出帧）
2. `readyState` 永远卡在 1（HAVE_METADATA）
3. 即使持续 append segment，画面也不渲染

此外发现一个潜在 bug：`_flushPending` 的 trim 逻辑使用 `video.duration > 45` 判断，但 `duration=Infinity`，`Infinity > 45` 为 true，会触发 `SourceBuffer.remove(0, Infinity - 30)`，可能抛 `InvalidAccessError` 导致 SourceBuffer 卡在 `updating=true` 状态。

## Current State Analysis

### 现象（来自用户日志）
- `updateend readyState=1 videoSize=1920x1080 duration=Infinity` 持续出现
- `play() timeout, retry 1/8` → `retry 5/8`，每次 800ms 超时
- FFmpeg 正常输出 fMP4（后端日志 `Init segment sent` 后持续输出 media segments）
- 前端正常 append segment（`Flushed pending segment` / `Media segment appended`）

### 根因分析
1. **`video.currentTime` 从未被设置**（默认 0），`MediaSource.duration` 从未被显式设置（Chromium 自动设为 Infinity）
2. **`updateend` 回调没有读取 `video.buffered`，也没有对齐 `currentTime`**
3. FFmpeg 使用 `-fflags +genpts` + lavfi `anullsrc` 输入，可能导致首段 `tfdt` 不为 0
4. 当 `currentTime=0` 不在 `buffered` 范围内时，`play()` Promise 会一直 pending，`readyState` 卡在 1
5. 之前尝试的 `_trySeekToBufferedStart`（seek 到 `start + 0.05`）失败，因为：
   - seek 和 play() 在同一事件循环中调用，导致冲突
   - `setLiveSeekableRange` 可能未生效
   - buffered 范围太小（0.51s）时 seek 无意义

### 次要问题
- `_flushPending` 的 trim 逻辑：`this._video.duration > 45` 在 `duration=Infinity` 时恒为 true，`SourceBuffer.remove(0, Infinity - 30)` 可能抛异常
- `_tryPlay` 在 pending Promise 上重复调用 `play()` 无效果（HTML 规范规定返回同一 Promise）
- `CODEC_FALLBACK_NEED_INIT` 错误码是死代码（`mediaSourcePlayer.ts` 从未抛出）

## Proposed Changes

### 文件 1: `d:\Project\直播切片多人\lsc-electron\src\services\mediaSourcePlayer.ts`

#### 修改 1: 添加 live-edge 对齐逻辑

**位置**：`updateend` 事件处理器（当前第 304-326 行）

**改动**：
- 在 `updateend` 回调中读取 `video.buffered` 和 `video.currentTime`
- 添加诊断日志：打印 `buffered.length`、`buffered.start(0)`、`buffered.end(0)`、`currentTime`
- 当 `readyState < 2` 且 `buffered.length > 0` 时，执行 **一次性** live-edge seek：
  - 计算目标 currentTime：`buffered.end(0) - 0.2`（跳到 live edge，留 200ms 余量避免末尾不完整帧）
  - 如果 `currentTime` 已在缓冲区内（`buffered.start(0) <= currentTime <= buffered.end(0)`），不 seek
  - 如果 `currentTime` 不在缓冲区内，设置 `currentTime = target`
  - 用 `_liveEdgeAligned` 标志确保只执行一次，避免反复 seek
- seek 完成后（通过 `seeked` 事件或延迟 200ms），再触发 `_markPlaying()`
- 移除当前 `readyState < 2 && vw > 0` 时直接 `_markPlaying()` 的逻辑（改为先对齐再播放）

**为什么**：
- `currentTime=0` 落在 buffered 之外是 `play()` pending 的直接原因
- 跳到 live edge（`end - 0.2`）比跳到 `start` 更可靠，因为 live edge 总是有最新数据
- 一次性标志避免反复 seek 导致 Chromium 进入无限 seeking 状态

**新增字段**：
```ts
// 标记是否已执行 live-edge 对齐（去重，避免反复 seek）
private _liveEdgeAligned = false
```

**新增 `seeked` 事件监听**（在 `_createSourceBuffer` 中）：
```ts
this._video.addEventListener('seeked', () => {
  this._log(`video seeked event, currentTime=${this._video?.currentTime}`)
  // seek 完成后触发播放
  if (this._state !== 'error' && this._state !== 'paused') {
    this._markPlaying()
  }
}, videoSignal ? { signal: videoSignal } : undefined)
```

#### 修改 2: 修复 `_flushPending` 的 trim 逻辑

**位置**：`_flushPending` 方法（当前第 269-278 行）

**改动**：
- 将 `this._video.duration > 45` 判断改为基于 `video.buffered` 的判断
- 使用 `video.buffered.length > 0 && (buffered.end(last) - buffered.start(0)) > 30` 代替 `duration > 45`
- `remove` 的结束时间改为 `buffered.start(0) + (buffered.end(last) - buffered.start(0) - 30)`，避免 `Infinity - 30`

**为什么**：
- `duration=Infinity` 时 `Infinity > 45` 恒为 true
- `SourceBuffer.remove(0, Infinity - 30)` 中 `Infinity - 30 = Infinity`，`remove(0, Infinity)` 可能抛 `InvalidAccessError`
- 异常被 catch 静默吞掉，但 SourceBuffer 可能卡在 `updating=true` 状态，阻止后续 append

**修改后逻辑**：
```ts
// Trim SourceBuffer to prevent memory leak (keep last 30s)
if (this._sourceBuffer && !this._sourceBuffer.updating && this._video) {
  const buffered = this._video.buffered
  if (buffered.length > 0) {
    const bufStart = buffered.start(0)
    const bufEnd = buffered.end(buffered.length - 1)
    const bufDuration = bufEnd - bufStart
    if (bufDuration > 45) {
      const removeEnd = bufEnd - 30
      if (removeEnd > bufStart) {
        try {
          this._sourceBuffer.remove(bufStart, removeEnd)
        } catch {
          // Remove may fail if buffer is being updated, ignore
        }
      }
    }
  }
}
```

#### 修改 3: 优化 `_tryPlay` 重试逻辑

**位置**：`_tryPlay` 方法（当前第 406-444 行）

**改动**：
- 将最大重试次数从 8 降到 5（避免日志过多）
- 超时时间从 800ms 降到 500ms（更快触发重试）
- 添加注释说明：live-edge 对齐后 `play()` 应该能快速 resolve，若仍 pending 说明对齐失败

**为什么**：
- 修复 1（live-edge 对齐）后，`play()` 应该能在首次或重试 1-2 次内 resolve
- 8 次 × 800ms = 6.4s 的重试窗口过长，用户感知卡顿
- 5 次 × 500ms = 2.5s 足够覆盖 live-edge 对齐后的正常重试

#### 修改 4: 在 `start()` 方法中重置 `_liveEdgeAligned`

**位置**：`start` 方法（当前第 88-95 行）

**改动**：
- 添加 `this._liveEdgeAligned = false` 重置

#### 修改 5: 清理死代码 `CODEC_FALLBACK_NEED_INIT` 引用

**位置**：`d:\Project\直播切片多人\lsc-electron\src\components\VideoPreview.tsx` 第 125-129 行

**改动**：
- 移除 `if (msg === 'CODEC_FALLBACK_NEED_INIT')` 分支（`mediaSourcePlayer.ts` 从未抛出此错误码）
- 保留 `setError(msg)` 和 `onErrorRef.current?.(msg)` 逻辑

**为什么**：
- 死代码会造成维护混淆
- `mediaSourcePlayer.ts` 的 codec fallback 是在 `_createSourceBuffer` 中内部处理（`isTypeSupported` 检查），不会通过 `onError` 上报

## Assumptions & Decisions

### 假设
1. **FFmpeg 输出的 fMP4 格式正确**：后端日志 `Init segment sent` 和持续输出 media segments 证明 FFmpeg 正常工作
2. **codec 兼容**：`SourceBuffer created with video/mp4; codecs="avc1.42c02a,mp4a.40.2"` 成功创建，codec 不是问题
3. **`video.buffered` 在 append media segment 后非空**：根据 MSE 规范，append moof+mdat 后 buffered 会更新
4. **Chromium 对 `duration=Infinity` 的 MSE 流需要 live-edge 对齐**：这是 hls.js / flv.js 等成熟库的通用做法

### 决策
1. **选择 `buffered.end(0) - 0.2` 而非 `buffered.start(0)`**：live edge 更可靠，且 200ms 余量避免末尾不完整帧
2. **一次性 live-edge 对齐**：用 `_liveEdgeAligned` 标志避免反复 seek，防止 Chromium 进入无限 seeking 状态
3. **不显式设置 `MediaSource.duration`**：Chromium 自动推断为 Infinity，显式设置可能触发不必要的 duration change 事件
4. **保留 `_tryPlay` 重试机制**：作为 live-edge 对齐的补充，应对网络抖动等瞬时问题
5. **不修改 FFmpeg 命令**：后端 FFmpeg 输出正常，问题在前端处理；修改 FFmpeg 命令（如 `-avoid_negative_ts`）可能影响录制流程

## Verification steps

### 1. TypeScript 编译验证
```powershell
cd "d:\Project\直播切片多人\lsc-electron"; npx tsc --noEmit
```
预期：无错误

### 2. 单元测试验证
```powershell
cd "d:\Project\直播切片多人\lsc-electron"; npx jest --config package.json 2>$null
```
或
```powershell
cd "d:\Project\直播切片多人\lsc-electron"; node tests/mediaSourceCodec.test.cjs
```
预期：现有测试通过

### 3. 重启程序验证
- 终止现有 electron / python 进程
- 在新 PowerShell 窗口启动 `npm run dev`
- 等待 Python 后端启动完成

### 4. MSE 预览功能验证
- 在 Electron 窗口点击房间卡片预览按钮
- 观察前端控制台日志，预期出现：
  - `updateend readyState=1 videoSize=1920x1080 duration=Infinity buffered=1 [X.XX-X.XX] currentTime=0`（诊断日志）
  - `Live-edge align: currentTime 0 -> X.XX (buffered X.XX-X.XX)`（对齐日志）
  - `video seeked event, currentTime=X.XX`（seek 完成日志）
  - `play() succeeded`（play 成功日志）
  - `video playing event`（播放就绪日志）
- 预览画面应显示直播画面，非黑屏

### 5. 回归验证
- 预览正常后，点击停止预览，再次启用预览，确认可重复
- 切换到其他房间预览，确认 live-edge 对齐逻辑正常工作
- 长时间预览（>1 分钟），确认 trim 逻辑不会导致 SourceBuffer 卡死

### 6. 后端日志验证
- 检查 `C:\Users\Administrator\AppData\Roaming\lsc-electron\logs\backend.log`
- 确认 `FFmpeg command:` 日志中参数顺序正确
- 确认无 `FFmpeg exited unexpectedly` 错误
