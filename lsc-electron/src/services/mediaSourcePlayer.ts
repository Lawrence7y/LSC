import {
  DEFAULT_TIMELINE_REPLAY_SECONDS,
  effectivePlaybackBufferSeconds,
  REPLAY_TRIM_HEADROOM_SECONDS,
} from '@/utils/replaySettings'

export type MsePlayerState = 'idle' | 'loading' | 'playing' | 'paused' | 'error'

export interface MsePlayerOptions {
  videoElement: HTMLVideoElement
  /** 用户可见 DVR 回放时长；0 表示关闭历史回放但保留播放安全缓存。 */
  replayBufferSeconds?: number
  /** 文件回看使用文件起点对齐，不应套用直播 live-edge 对齐。 */
  isFile?: boolean
  channel?: 'live' | 'review'
  sessionId?: string
  onStateChange?: (state: MsePlayerState) => void
  onError?: (error: string) => void
  onSourceOpen?: () => void
  /** pending 过高/回落时通知（用于后端背压） */
  onBackpressure?: (state: 'pause' | 'resume', pending: number) => void
  debug?: boolean
}

export function getMp4MimeFromInitSegment(data: Uint8Array | ArrayBuffer): string {
  const bytes = data instanceof Uint8Array ? data : new Uint8Array(data)
  const avcC = findAscii(bytes, 'avcC')
  let videoCodec = 'avc1.42E01E'

  if (avcC !== -1 && avcC + 7 < bytes.length) {
    const profile = bytes[avcC + 5]
    const compatibility = bytes[avcC + 6]
    const level = bytes[avcC + 7]
    videoCodec = `avc1.${toHex(profile)}${toHex(compatibility)}${toHex(level)}`
  }

  const codecs = [videoCodec]
  if (findAscii(bytes, 'mp4a') !== -1 || findAscii(bytes, 'esds') !== -1) {
    codecs.push('mp4a.40.2')
  }

  return `video/mp4; codecs="${codecs.join(',')}"`
}

function findAscii(bytes: Uint8Array, text: string): number {
  const needle = Array.from(text, (char) => char.charCodeAt(0))
  for (let i = 0; i <= bytes.length - needle.length; i++) {
    let matched = true
    for (let j = 0; j < needle.length; j++) {
      if (bytes[i + j] !== needle[j]) {
        matched = false
        break
      }
    }
    if (matched) return i
  }
  return -1
}

function toHex(value: number): string {
  return value.toString(16).padStart(2, '0')
}

/**
 * MediaSource Player for Electron MSE preview.
 *
 * 基于 MediaSource Extensions (MSE) 的直播流播放器，用于在 Electron 渲染进程中
 * 实时预览多房间直播切片。接收后端通过 WebSocket 推送的 fMP4 分片（init segment +
 * media segments），喂给 `<video>` 元素实现低延迟播放。
 *
 * 核心流程：
 * 1. 后端 FFmpeg 将 H.264/AAC 直播流转码为 fMP4 格式，通过 WebSocket 推送 init
 *    段（ftyp + moov）和 media 段（moof + mdat）。
 * 2. 前端创建 MediaSource，绑定到 video.src，监听 sourceopen 后创建 SourceBuffer。
 * 3. init 段首先 append，建立解码上下文；media 段持续 append，video 自动播放。
 * 4. 缓冲区按用户设置自动 trim，保留有限的最近媒体，既保留回看能力，又防止内存泄漏。
 * 5. live-edge 对齐：MSE 直播流 duration=Infinity，currentTime 默认 0，
 *    可能落在 buffered 范围外导致 play() pending；首次 updateend 检测到该情况时
 *    自动 seek 至 live edge（buffered.end - 0.2s），确保 readyState 升到 2+。
 *
 * 与后端 MSE Streamer 的对接：
 * - WebSocket 消息类型 `mse_init` → 调用 feedInit()
 * - WebSocket 消息类型 `mse_segment` → 调用 feedMedia()
 * - 后端负责按 GOP 边界切分 fMP4 分片并推送；前端只负责 append 和播放控制。
 */
export class MsePlayer {
  private _video: HTMLVideoElement
  private _mediaSource: MediaSource | null = null
  private _sourceBuffer: SourceBuffer | null = null
  private _state: MsePlayerState = 'idle'
  private _onStateChange?: (state: MsePlayerState) => void
  private _onError?: (error: string) => void
  private _onSourceOpen?: () => void
  private _onBackpressure?: (state: 'pause' | 'resume', pending: number) => void
  private _debug: boolean
  private _pendingSegments: Uint8Array[] = []
  private _initReceived = false
  private _initSegment: Uint8Array | null = null
  // init 段是否已成功 append 到 SourceBuffer。init 段不进入 pending 队列
  // （队列超限会 shift 丢弃队头，可能把 init 丢掉且 _initReceived 已置位、
  // 后端补发的重复 init 被忽略，导致不可恢复的黑屏），由 _flushPending 优先补 append。
  private _initAppended = false
  // 用 AbortController 统一管理 MediaSource/SourceBuffer 事件监听器，便于清理时移除（M14）
  private _abortController: AbortController | null = null
  // play() 延迟重试机制：避免 play() 被 pause() 中断或静默失败
  private _playRetryTimer: ReturnType<typeof setTimeout> | null = null
  // 标记是否已执行 live-edge 对齐（去顶，避免反复 seek）
  private _liveEdgeAligned = false
  // 标记是否正在执行 SourceBuffer trim（remove）：trim 完成触发的 updateend
  // 不再递归进入 trim 分支，仅处理 _pendingSegments，避免链式 updateend 卡死
  private _isTrimming = false
  // 最大待处理分段数：超出时丢弃最旧的，避免主线程卡顿时无限堆积
  private readonly _maxPendingSegments = 20
  private readonly _backpressurePauseAt = 10
  private readonly _backpressureResumeAt = 3
  private _backpressurePaused = false
  private _lastBackpressureSentAt = 0
  private readonly _backpressureMinIntervalMs = 500
  // 用户主动暂停时停止向该播放器继续堆积直播分片，避免停在缓冲左缘时无限增长。
  private _userPaused = false
  // 卡顿检测：记录上次 currentTime 变化的时间和位置
  private _stallCheckTimer: ReturnType<typeof setInterval> | null = null
  private _lastStallTime = 0
  private _lastStallPosition = 0
  // 数据饥饿检测：记录 buffer 末端最后增长时间，超过阈值判定为流中断
  private _lastBufferEnd = 0
  private _lastBufferEndTime = 0
  private _stallRecoveryCount = 0
  private readonly _stallRecoveryLimit = 3
  private readonly _bufferStallTimeoutMs = 8000
  // play() 已耗尽全部重试仍失败：media clock 可能冻结，stall recovery 应强制 seek 而非无限重试。
  // 置位于 _tryPlay 重试耗尽；清除于 play() 成功 / playing / seeked / currentTime 前进。
  private _playExhausted = false
  // 强制 seek 恢复的次数：仅 currentTime 前进时重置（不受 buffer 增长重置），
  // 防止"buffer 持续增长掩盖 media clock 冻结"时无限 seek 死循环。
  private _forcedSeekRecoveryCount = 0
  private _currentBlobUrl: string | null = null
  // 记录最近一次主动 seek 的时间戳，用于卡顿恢复保护期
  private _lastSeekTime = 0
  // 配额超出连续恢复计数，成功写入后归零
  private _quotaRetryCount = 0
  // 当前 MSE 实际保留时长。0（关闭 DVR）映射为短安全缓存，而不是完全无缓存。
  private _replayBufferSeconds: number
  // 文件回看首个 media 到达时，从文件缓冲起点启动；直播才跳到 live edge。
  private readonly _isFile: boolean
  public readonly channel: 'live' | 'review'
  public sessionId?: string

  constructor(options: MsePlayerOptions) {
    this._video = options.videoElement
    this._onStateChange = options.onStateChange
    this._onError = options.onError
    this._onSourceOpen = options.onSourceOpen
    this._onBackpressure = options.onBackpressure
    this._debug = options.debug ?? false
    this._isFile = options.isFile === true
    this.channel = options.channel ?? (this._isFile ? 'review' : 'live')
    this.sessionId = options.sessionId
    this._replayBufferSeconds = effectivePlaybackBufferSeconds(
      options.replayBufferSeconds ?? DEFAULT_TIMELINE_REPLAY_SECONDS,
    )
  }

  get state(): MsePlayerState {
    return this._state
  }

  get videoElement(): HTMLVideoElement {
    return this._video
  }

  /** 动态更新 MSE 缓冲保留时长；已被 remove 的历史分片不会重新出现。 */
  setReplayBufferSeconds(seconds: number): void {
    this._replayBufferSeconds = effectivePlaybackBufferSeconds(seconds)
    this._flushPending()
  }

  /** Start receiving init + media segments.
   *
   * 重置所有内部状态（pending segments、init received、live-edge 标志），
   * 创建新的 MediaSource 并绑定到 video 元素，进入 loading 状态等待后端推送 init 段。
   *
   * @param _url - 预留参数，当前版本未使用（流地址由后端 WebSocket 推送决定）
   */
  start(_url: string): void {
    this.stop()
    this._pendingSegments = []
    this._initReceived = false
    this._initSegment = null
    this._initAppended = false
    this._liveEdgeAligned = false
    this._lastStallTime = 0
    this._lastStallPosition = 0
    this._lastBufferEnd = 0
    this._lastBufferEndTime = 0
    this._isTrimming = false
    this._stallRecoveryCount = 0
    this._playExhausted = false
    this._forcedSeekRecoveryCount = 0
    this._lastSeekTime = 0
    this._backpressurePaused = false
    this._setState('loading')
    this._initMediaSource()
    this._startStallDetection()
  }

  /**
   * 喂入 init segment（ftyp + moov boxes）。
   *
   * 建立 SourceBuffer 解码上下文，必须在 media segment 之前调用。
   * 若 SourceBuffer 尚未就绪，init 段缓存在 _initSegment 中（不进入 pending
   * 队列，避免队列超限丢弃队头时把 init 丢掉），由 _flushPending() 在
   * sourceopen 或 updateend 时优先补 append。
   *
   * 重复调用会被忽略（init received 标志位保护）。
   *
   * @param data - init segment 的原始二进制数据（ArrayBuffer）
   */
  feedInit(data: ArrayBuffer): void {
    const isError = this._state === 'error'
    const newBytes = new Uint8Array(data)
    let isDifferent = false
    if (this._initSegment && this._initSegment.byteLength === newBytes.byteLength) {
      for (let i = 0; i < newBytes.byteLength; i++) {
        if (this._initSegment[i] !== newBytes[i]) {
          isDifferent = true
          break
        }
      }
    } else if (this._initSegment) {
      isDifferent = true
    }

    if (this._initReceived && !isError && !isDifferent) {
      this._log('Init already received, ignoring duplicate')
      return
    }

    // 收到新的不同 init 段（推流源切换如录制回看/直播切换）或从错误中恢复
    if (this._initReceived && (isDifferent || isError)) {
      this._log(`New init segment received (different=${isDifferent}, recovering=${isError}), resetting stream pipeline`)
      this._setState('loading')
      this._initReceived = true
      this._initSegment = newBytes
      this._initAppended = false
      this._liveEdgeAligned = false
      this._pendingSegments = []
      this._initMediaSource()
      return
    }

    this._initReceived = true
    this._initSegment = newBytes

    if (!this._sourceBuffer && this._mediaSource?.readyState === 'open') {
      this._createSourceBuffer()
    }

    if (this._sourceBuffer && !this._sourceBuffer.updating) {
      this._appendInitSegment()
    } else {
      // Buffer not ready yet：init 段缓存在 _initSegment，等 _flushPending 补 append
      this._log(`Init segment deferred (${data.byteLength} bytes)`)
    }
  }

  /**
   * 将缓存的 init 段 append 到 SourceBuffer（幂等）。
   *
   * init 段只含 ftyp+moov 元数据，无视频帧。不在此处切到 playing ——
   * 等首个 media 段 append 完成（updateend）且 <video>.readyState >= 2
   * (HAVE_CURRENT_DATA) 时再切，避免出现 state='playing' 但画面黑屏的问题。
   *
   * @returns 本次是否成功发起 append（SourceBuffer 忙或未就绪时返回 false）
   */
  private _appendInitSegment(): boolean {
    if (!this._initSegment || this._initAppended) return false
    if (!this._sourceBuffer || this._sourceBuffer.updating) return false
    try {
      const seg = this._initSegment
      this._sourceBuffer.appendBuffer(seg.buffer.slice(seg.byteOffset, seg.byteOffset + seg.byteLength) as ArrayBuffer)
      this._initAppended = true
      this._log(`Init segment appended (${seg.byteLength} bytes)`)
      return true
    } catch (e) {
      this._handleError(`Init segment append failed: ${e}`)
      return false
    }
  }

  /**
   * 喂入 media segment（moof + mdat boxes）。
   *
   * 若 init 段未就绪或 SourceBuffer 正在 updating，media 段进入 pending 队列。
   * pending 超过 {@link _maxPendingSegments}（20 条）时丢弃最旧分段，优先保证直播低延迟。
   * SourceBuffer 空闲时 append，触发 updateend → _flushPending() 循环，持续消费队列。
   *
   * @param data - media segment 的原始二进制数据（ArrayBuffer）
   */
  feedMedia(data: ArrayBuffer): void {
    if (this._state === 'error') return
    // 直播用户暂停时不追赶直播沿；文件回看必须继续接收有限分片，
    // 否则暂停期间文件流走完后再播放会找不到连续的 SourceBuffer 数据。
    if (this._userPaused && !this._isFile) return

    const seg = new Uint8Array(data)
    if (!this._initAppended || (this._sourceBuffer && this._sourceBuffer.updating)) {
      this._pendingSegments.push(seg)
      if (this._pendingSegments.length > this._maxPendingSegments) {
        // 保留最新的分段，丢弃最旧的（直播流丢弃旧帧优于堆积）
        this._pendingSegments.shift()
        this._log(`Dropping oldest segment (pending > ${this._maxPendingSegments})`)
      }
      this._maybeEmitBackpressure()
      return
    }

    if (this._sourceBuffer && !this._sourceBuffer.updating) {
      try {
        this._sourceBuffer.appendBuffer(seg.buffer.slice(seg.byteOffset, seg.byteOffset + seg.byteLength) as ArrayBuffer)
        this._log(`Media segment appended (${data.byteLength} bytes)`)
        this._quotaRetryCount = 0
        // 持续收到媒体分段说明流正在播放，确保状态为 playing。
        this._markPlaying()
        this._maybeEmitBackpressure()
      } catch (e) {
        this._handleAppendError(e, seg, 'Media segment append')
      }
    } else {
      this._pendingSegments.push(seg)
      this._maybeEmitBackpressure()
    }
  }

  private _maybeEmitBackpressure(): void {
    if (!this._onBackpressure) return
    const pending = this._pendingSegments.length
    let next: 'pause' | 'resume' | null = null
    if (!this._backpressurePaused && pending >= this._backpressurePauseAt) {
      next = 'pause'
      this._backpressurePaused = true
    } else if (this._backpressurePaused && pending <= this._backpressureResumeAt) {
      next = 'resume'
      this._backpressurePaused = false
    }
    if (!next) return
    const now = Date.now()
    if (now - this._lastBackpressureSentAt < this._backpressureMinIntervalMs) return
    this._lastBackpressureSentAt = now
    try {
      this._onBackpressure(next, pending)
    } catch (e) {
      this._log(`backpressure callback failed: ${e}`)
    }
  }

  private _setUserPaused(paused: boolean): void {
    if (this._userPaused === paused) return
    this._userPaused = paused
    try {
      this._onBackpressure?.(paused ? 'pause' : 'resume', this._pendingSegments.length)
    } catch (e) {
      this._log(`user pause backpressure callback failed: ${e}`)
    }
  }

  /** Play the video. */
  play(): void {
    if (this._video && this._state !== 'error') {
      this._setUserPaused(false)
      this._video.play().catch((err) => {
        console.warn('[MsePlayer] play() failed:', err)
      })
    }
  }

  /** Pause the video. */
  pause(): void {
    this._setUserPaused(true)
    if (this._video) {
      this._video.pause()
    }
    if (this._state === 'playing') {
      this._setState('paused')
    }
  }

  /** Seek to a time in seconds.
   *
   * 直播流 duration=Infinity，clamp 到 buffered 区间，避免 seek 到缓冲外
   * 导致 play() Promise 永久 pending。
   */
  seek(time: number): void {
    if (!this._video) return
    this._lastSeekTime = Date.now()
    this._setUserPaused(false)
    if (this._state === 'paused') {
      this._setState('playing')
    }
    if (this._video.buffered.length > 0) {
      const ranges = this.getBufferedRanges()
      const activeRange = ranges.find(range => time >= range.start && time <= range.end)
      const range = activeRange ?? ranges[0]
      if (!range) return
      const safeStart = Math.min(range.end, range.start + 0.3)
      this._video.currentTime = Math.min(Math.max(time, safeStart), range.end)
      this._tryPlay(0)
    } else if (Number.isFinite(this._video.duration) && this._video.duration > 0) {
      this._video.currentTime = Math.min(time, this._video.duration)
      this._tryPlay(0)
    }
  }

  /** 标记发生过主动 seek，激活 3 秒卡顿保护窗口（防止卡顿自恢复误跳直播沿） */
  markSeeked(): void {
    this._lastSeekTime = Date.now()
  }

  /** Toggle mute. */
  setMuted(muted: boolean): void {
    if (this._video) {
      this._video.muted = muted
    }
  }

  /**
   * 重置 live-edge 对齐标志，允许下次 updateend 重新对齐 currentTime。
   *
   * 用于全屏切换后恢复小预览播放：player 实例仍存活，但 currentTime 可能已落后
   * 于 buffered 范围；重置后新 segment 到达会触发 live-edge 对齐逻辑。
   */
  resetLiveEdgeAligned(): void {
    this._liveEdgeAligned = false
  }

  /**
   * 恢复播放（从后台切回前台时主动调用）。
   *
   * 操作：
   * 1. 若 currentTime 不在 buffered 范围内，seek 到缓冲区内最近的点（bufEnd - 0.5s）。
   * 2. 重置 live-edge 对齐标志，允许重新对齐。
   * 3. 调用 _tryPlay() 以延迟重试机制恢复播放。
   *
   * @remarks
   * 仅当状态非 idle/error 时生效；paused 状态不自动恢复（尊重用户主动暂停）。
   */
  resumePlayback(_userInitiated = false): void {
    if (this._state === 'error' || this._state === 'idle') return
    this._setUserPaused(false)
    if (this._video && this._video.buffered.length > 0) {
      const bufStart = this._video.buffered.start(0)
      const bufEnd = this._video.buffered.end(this._video.buffered.length - 1)
      if (this._video.currentTime < bufStart || this._video.currentTime > bufEnd) {
        this._video.currentTime = Math.max(bufStart, bufEnd - 0.5)
      }
    }
    this._liveEdgeAligned = false
    this._setState('playing')
    this._tryPlay(0)
  }

  /**
   * 强制跳到直播最新位置。
   *
   * 与 resumePlayback() 不同，这里即使 currentTime 仍在缓冲区内，也会主动
   * seek 到缓冲区末尾附近，用于控制栏“直播”按钮。
   */
  goLive(): void {
    this._setUserPaused(false)
    if (this._state === 'error') {
      this._log('goLive: recovering from error state, re-initializing media source')
      this._setState('loading')
      this._initReceived = false
      this._initAppended = false
      this._liveEdgeAligned = false
      this._initMediaSource()
      return
    }
    if (this._state === 'idle') return
    if (this._video && this._video.buffered.length > 0) {
      const bufStart = this._video.buffered.start(0)
      const bufEnd = this._video.buffered.end(this._video.buffered.length - 1)
      const safeStart = Math.min(bufEnd, bufStart + 0.3)
      const target = Math.max(bufStart, bufEnd - 0.3)
      const safeTarget = Math.max(safeStart, target)
      this._lastSeekTime = Date.now()
      this._video.currentTime = target // this._video.currentTime = target (safeTarget fallback)
      this._video.currentTime = safeTarget
    } else {
      this._log('goLive: buffer empty, waiting for next segment')
    }
    this._liveEdgeAligned = false
    if (this._state === 'paused') {
      this._setState('playing')
    }
    this._tryPlay(0)
  }

  /** 返回当前 SourceBuffer 的所有连续 seek 区间（preview 轴秒）。 */
  getBufferedRanges(): Array<{ start: number; end: number }> {
    if (!this._video || this._video.buffered.length === 0) return []
    const ranges: Array<{ start: number; end: number }> = []
    for (let i = 0; i < this._video.buffered.length; i += 1) {
      const start = this._video.buffered.start(i)
      const end = this._video.buffered.end(i)
      if (Number.isFinite(start) && Number.isFinite(end) && end > start) {
        ranges.push({ start, end })
      }
    }
    return ranges
  }

  /** 返回当前 SourceBuffer 的整体边界（兼容旧调用方）。 */
  getBufferedRange(): { start: number; end: number } | null {
    const ranges = this.getBufferedRanges()
    if (ranges.length === 0) return null
    return { start: ranges[0].start, end: ranges[ranges.length - 1].end }
  }

  /** Get current playback time. */
  get currentTime(): number {
    return this._video?.currentTime ?? 0
  }

  /**
   * 停止播放并清理所有资源。
   *
   * 操作：
   * 1. 取消待执行的 play() 重试定时器。
   * 2. 暂停 video，移除 src，调用 load() 释放解码器。
   * 3. 调用 _cleanup() 结束 MediaSource 流，释放 Object URL。
   * 4. 清空 pending segments、init received 标志，回到 idle 状态。
   */
  stop(): void {
    // 停止卡顿检测
    this._stopStallDetection()
    // 取消所有待执行的 play 重试
    if (this._playRetryTimer) {
      clearTimeout(this._playRetryTimer)
      this._playRetryTimer = null
    }
    this._setState('idle')
    this._userPaused = false
    this._backpressurePaused = false
    // S5: abort SourceBuffer 防止 pending 的 append 阻塞 _cleanup
    if (this._sourceBuffer) {
      try { this._sourceBuffer.abort() } catch {}
    }
    if (this._video) {
      this._video.pause()
      this._video.removeAttribute('src')
      this._video.load()
    }
    this._cleanup()
    this._pendingSegments = []
    this._initReceived = false
    this._initSegment = null
    this._initAppended = false
  }

  /**
   * 初始化 MediaSource 并绑定到 video 元素。
   *
   * 步骤：
   * 1. 清理旧 MediaSource/SourceBuffer（_cleanup）。
   * 2. 创建新 MediaSource，通过 URL.createObjectURL 赋值给 video.src。
   * 3. 创建 AbortController，统一管理所有 MediaSource 事件监听器，便于 _cleanup 时批量移除。
   * 4. 监听 sourceopen：MediaSource 就绪后，若有 init segment 则创建 SourceBuffer，
   *    并 flush 缓冲的 pending segments。
   * 5. 监听 sourceended / sourceclose 用于日志诊断。
   */
  private _initMediaSource(): void {
    this._cleanup()
    try {
      this._mediaSource = new MediaSource()
      this._currentBlobUrl = URL.createObjectURL(this._mediaSource)
      this._video.src = this._currentBlobUrl
      // 用 AbortController 统一管理事件监听器，_cleanup 时 abort 即可全部移除（M14）
      this._abortController = new AbortController()
      const { signal } = this._abortController

      this._mediaSource.addEventListener('sourceopen', () => {
        if (!this._mediaSource || this._mediaSource.readyState !== 'open') return

        // 通知外部（VideoPreview）MediaSource 已打开，此时 video.src 已绑定到新 MediaSource，
        // 可以安全地创建 Web Audio 路由（createMediaElementSource）
        this._onSourceOpen?.()

        try {
          if (this._initSegment) {
            this._createSourceBuffer()
          }

          // SourceBuffer 就绪后立即 flush 缓冲的 init/media 段，消除 sourceopen
          // 异步导致的竞态（init 在 sourceopen 前到达会被 unshift 进 pending）
          this._flushPending()

          this._log(`MediaSource opened${this._sourceBuffer ? ', SourceBuffer created' : ''}`)

        } catch (e) {
          this._handleError(`MediaSource init failed: ${e}`)
        }
      }, { signal })

      this._mediaSource.addEventListener('sourceended', () => {
        this._log('MediaSource ended')
      }, { signal })

      this._mediaSource.addEventListener('sourceclose', () => {
        this._log('MediaSource closed')
      }, { signal })

    } catch (e) {
      this._handleError(`MediaSource creation failed: ${e}`)
    }
  }

  /**
   * 刷新 pending 队列中的 segments 到 SourceBuffer。
   *
   * 行为：
   * - 每次只 append 一个 segment，避免 SourceBuffer.updating 溢出。
   * - append 后若 buffer 仍在 updating，立即退出，等待下一次 updateend 触发继续。
   *
   * 缓冲区管理（trim 策略）：
   * - 当 buffered 总时长超过“用户设置时长 + headroom”时，移除旧数据，
   *   保留用户设置的最近媒体供回看；关闭 DVR 时仅保留短安全缓存。
   * - 使用 _isTrimming 标志防止 remove() 触发的 updateend 递归进入 trim 分支，
   *   避免链式回调导致 SourceBuffer 卡在 updating=true。
   *
   * @remarks
   * 此方法在多个时机被调用：
   * - sourceopen 事件（MediaSource 就绪）
   * - SourceBuffer updateend 事件（每次 append 完成）
   * - feedInit / feedMedia（数据到达时尝试直接 append）
   */
  private _flushPending(): void {
    if (!this._sourceBuffer || this._sourceBuffer.updating) return

    // 记录进入时是否为 trim 触发的 updateend（_isTrimming=true 表示上一次
    // remove() 刚完成）。若为 true 则本次仅处理 _pendingSegments，跳过 trim
    // 分支，避免 trim 的 updateend 链式递归导致 SourceBuffer 卡在 updating=true。
    const wasTrimming = this._isTrimming

    // init 段优先：init 未 append 前不消费 media 队列。
    // append 是异步的（updating 置位），media 段等下一次 updateend。
    if (!this._initAppended) {
      this._appendInitSegment()
      return
    }

    // 修复：每次只 append 一个 segment，不使用 while 循环。
    // Chromium 可能在下一个微任务才设置 sourceBuffer.updating=true，
    // while 循环中多次 append 会触发 InvalidStateError。
    // 下一个 updateend 事件会继续消费 pending 队列。
    if (this._pendingSegments.length > 0) {
      const seg = this._pendingSegments.shift()!
      try {
        this._sourceBuffer.appendBuffer(seg.buffer.slice(seg.byteOffset, seg.byteOffset + seg.byteLength) as ArrayBuffer)
        this._log(`Flushed pending segment (${seg.byteLength} bytes)`)
        this._quotaRetryCount = 0
        this._maybeEmitBackpressure()
      } catch (e) {
        this._handleAppendError(e, seg, 'Pending segment append')
      }
    }

    // Trim SourceBuffer to prevent memory leak.
    // 保留时长由用户设置（如 300s/600s）；额外留出少量 headroom，避免每个分片都触发 remove。
    // 内存保护由 QuotaExceededError 机制自适应兜底，不人为硬编码截断用户设置。
    if (!wasTrimming && this._sourceBuffer && !this._sourceBuffer.updating && this._video) {
      const buffered = this._video.buffered
      if (buffered.length > 0) {
        const bufStart = buffered.start(0)
        const bufEnd = buffered.end(buffered.length - 1)
        const bufDuration = bufEnd - bufStart
        const targetReplaySeconds = Math.max(30, this._replayBufferSeconds)
        const trimThreshold = targetReplaySeconds + REPLAY_TRIM_HEADROOM_SECONDS
        if (bufDuration > trimThreshold) {
          let removeEnd = bufEnd - targetReplaySeconds
          // 保护当前播放头：绝不能移除当前播放头前至少 20 秒的数据。
          const curTime = this._video.currentTime
          if (curTime >= bufStart && curTime < bufEnd) {
            removeEnd = Math.min(
              removeEnd,
              Math.max(bufStart, curTime - REPLAY_TRIM_HEADROOM_SECONDS),
            )
          }
          if (removeEnd > bufStart + 5) {
            try {
              this._isTrimming = true
              this._sourceBuffer.remove(bufStart, removeEnd)
            } catch {
              this._isTrimming = false
            }
          }
        }
      }
    }

    // trim 触发的 updateend 已处理完 _pendingSegments，重置标志，后续 updateend 可正常 trim
    if (wasTrimming) {
      this._isTrimming = false
    }
  }

  /**
   * 创建 SourceBuffer 并注册事件监听器。
   *
   * 编解码器选择逻辑：
   * 1. 从 init segment 中解析 avcC box，提取 H.264 profile/level，构造精确 codecs 字符串。
   * 2. 若检测到 mp4a/esds 则附加 AAC 音频轨道（mp4a.40.2）。
   * 3. 用 MediaSource.isTypeSupported() 验证浏览器支持；不支持则尝试去掉音频轨的 fallback。
   *
   * 注册的事件：
   * - updateend：append 完成后 flush pending，诊断日志，触发 live-edge 对齐或 _markPlaying。
   * - error：转发到 _handleError。
   * - video loadeddata / canplay / playing / seeked：作为 readyState 升级的备用触发器，
   *   确保 _markPlaying 在解码就绪时被调用。
   */
  private _createSourceBuffer(): void {
    if (!this._mediaSource || this._mediaSource.readyState !== 'open' || this._sourceBuffer) return

    const mime = this._initSegment
      ? getMp4MimeFromInitSegment(this._initSegment)
      : 'video/mp4; codecs="avc1.42E01E,mp4a.40.2"'
    const fallback = mime.includes(',mp4a')
      ? mime.replace(',mp4a.40.2', '')
      : 'video/mp4; codecs="avc1.42E01E"'

    const selectedMime = MediaSource.isTypeSupported(mime)
      ? mime
      : MediaSource.isTypeSupported(fallback)
        ? fallback
        : null

    if (!selectedMime) {
      this._handleError(`Browser does not support H.264 MSE playback (${mime})`)
      return
    }

    if (selectedMime === fallback && mime.includes(',mp4a')) {
      console.warn('[MsePlayer] Audio codec not supported by browser, falling back to video-only — no audio will be available')
    }

    this._sourceBuffer = this._mediaSource.addSourceBuffer(selectedMime)
    const signal = this._abortController?.signal
    this._sourceBuffer.addEventListener('updateend', () => {
      this._flushPending()
      // 诊断：记录每次 updateend 时的 readyState、buffered 范围、currentTime
      // （惰性求值：debug 关闭时不构造字符串，高频 append 下避免主线程浪费）
      const buf = this._video?.buffered
      const bufLen = buf?.length ?? 0
      const bufStart = bufLen > 0 ? buf!.start(0) : -1
      const bufEnd = bufLen > 0 ? buf!.end(bufLen - 1) : -1
      const curTime = this._video?.currentTime ?? 0
      this._log(() => {
        const rs = this._video?.readyState ?? 0
        const vw = this._video?.videoWidth ?? 0
        const vh = this._video?.videoHeight ?? 0
        const dur = this._video?.duration ?? 0
        return `updateend readyState=${rs} videoSize=${vw}x${vh} duration=${dur} buffered=${bufLen}[${bufStart.toFixed(2)}-${bufEnd.toFixed(2)}] currentTime=${curTime.toFixed(2)}`
      })

      if (this._video && this._video.readyState >= 2) {
        // readyState 已升到 2+：正常播放
        this._markPlaying()
      } else if (this._video && this._video.readyState < 2 && bufLen > 0 && !this._liveEdgeAligned) {
        // 首段 tfdt 可能不从 0 开始，currentTime 落在 buffered 之外时必须先对齐。
        // 直播从 live edge 启动；文件回看从请求对应的 buffered 起点启动，
        // 绝不能把用户点选的历史位置改成文件末端。
        this._liveEdgeAligned = true
        const target = this._isFile
          ? Math.min(bufEnd, bufStart + 0.3)
          : Math.max(bufStart, bufEnd - 0.2)
        if (curTime < bufStart || curTime > bufEnd) {
          this._log(`${this._isFile ? 'File-start' : 'Live-edge'} align: currentTime ${curTime.toFixed(2)} -> ${target.toFixed(2)} (buffered ${bufStart.toFixed(2)}-${bufEnd.toFixed(2)})`)
          try {
            this._lastSeekTime = Date.now()
            this._video.currentTime = target
          } catch (e) {
            this._log(`${this._isFile ? 'File-start' : 'Live-edge'} align failed: ${e}`)
          }
        } else {
          this._log(`currentTime ${curTime.toFixed(2)} already in buffered range, no seek needed`)
        }
        // 即使无需 seek，也触发 _markPlaying 让 _tryPlay 启动
        this._markPlaying()
      } else if (this._video && this._video.readyState < 2 && this._liveEdgeAligned) {
        // 已对齐过但仍 readyState < 2：继续重试 play()
        this._markPlaying()
      }
    }, signal ? { signal } : undefined)
    this._sourceBuffer.addEventListener('error', () => {
      this._handleError('SourceBuffer error')
    }, signal ? { signal } : undefined)
    this._log(`SourceBuffer created with ${selectedMime}`)

    // 监听 video 元素的 canplay 事件作为 readyState 升级的备用触发：
    // 某些情况下 updateend 触发时 readyState 还没更新，canplay/canplaythrough
    // 会在解码就绪后触发，此时再尝试 _markPlaying。
    if (this._video) {
      const videoSignal = signal
      this._video.addEventListener('loadeddata', () => {
        this._log(`video loadeddata readyState=${this._video?.readyState}`)
        if (this._video && this._video.readyState >= 2) {
          this._markPlaying()
        }
      }, videoSignal ? { signal: videoSignal } : undefined)
      this._video.addEventListener('canplay', () => {
        this._log(`video canplay readyState=${this._video?.readyState}`)
        if (this._video && this._video.readyState >= 2) {
          this._markPlaying()
        }
      }, videoSignal ? { signal: videoSignal } : undefined)
      // 'playing' 事件：video 元素真正开始播放。此时清除 play() 重试定时器，
      // 并确保状态为 playing。这是最可靠的播放就绪信号。
      this._video.addEventListener('playing', () => {
        this._log(`video playing event, readyState=${this._video?.readyState}`)
        if (this._playRetryTimer) {
          clearTimeout(this._playRetryTimer)
          this._playRetryTimer = null
        }
        this._playExhausted = false
        if (this._state !== 'error' && this._state !== 'paused') {
          this._markPlaying()
        }
      }, videoSignal ? { signal: videoSignal } : undefined)
      // 'seeked' 事件：live-edge 对齐的 seek 完成后触发播放。
      // seek 完成后 currentTime 已在缓冲区内，play() 应能快速 resolve。
      this._video.addEventListener('seeked', () => {
        this._log(`video seeked event, currentTime=${this._video?.currentTime?.toFixed(2)}`)
        this._playExhausted = false
        if (this._state !== 'error' && this._state !== 'paused') {
          this._markPlaying()
        }
      }, videoSignal ? { signal: videoSignal } : undefined)
      // 原生 controls 触发的暂停/播放也要同步上游背压状态，
      // 否则用户停在缓冲左缘时仍会持续积累直播分片。
      this._video.addEventListener('pause', () => {
        // seeking 期间或刚执行过主动 seek（3 秒内）触发的 pause 属于浏览器内部行为，
        // 绝不是用户主动暂停，禁止触发上游背压丢弃分片与 UI 暂停遮罩！
        if (this._video?.seeking || Date.now() - this._lastSeekTime < 3000) {
          return
        }
        if (this._state === 'playing') {
          this._setUserPaused(true)
          this._setState('paused')
        }
      }, videoSignal ? { signal: videoSignal } : undefined)
      this._video.addEventListener('play', () => {
        this._setUserPaused(false)
      }, videoSignal ? { signal: videoSignal } : undefined)
    }
  }

  /**
   * 清理 MediaSource / SourceBuffer 资源。
   *
   * 操作：
   * 1. 若 MediaSource 仍处于 open，调用 endOfStream() 正常结束流。
   * 2. 释放 Object URL（URL.revokeObjectURL）。
   * 3. 将 _sourceBuffer 和 _mediaSource 置 null，等待下次 start() 重新初始化。
   */
  private _cleanup(): void {
    this._abortController?.abort()
    this._abortController = null
    if (this._sourceBuffer) {
      try {
        if (this._mediaSource?.readyState === 'open') {
          this._mediaSource.endOfStream()
        }
      } catch { /* ignore */ }
      this._sourceBuffer = null
    }
    if (this._mediaSource) {
      if (this._currentBlobUrl) {
        URL.revokeObjectURL(this._currentBlobUrl)
      }
      this._currentBlobUrl = null
      this._mediaSource = null
    }
  }

  /**
   * 切换播放器状态并通知外部回调。
   *
   * 状态流转：idle → loading → playing ⇄ paused → error
   * 切到 playing 时自动触发 _tryPlay() 以延迟重试机制启动 video 播放。
   *
   * @param state - 目标状态
   */
  private _setState(state: MsePlayerState): void {
    if (this._state !== state) {
      this._state = state
      this._onStateChange?.(state)
      // Auto-update video state
      if (state === 'playing') {
        // 延迟调用 play()，让浏览器完成当前事件循环中的内部处理
        // （如 SourceBuffer append、MediaSource 状态切换等），避免
        // "play() interrupted by pause()" 或静默失败。
        this._tryPlay(0)
      }
    }
  }

  /** 延迟播放并在失败时重试。
   *
   * Electron/Chromium 中，muted video 的 play() 通常不会被 autoplay policy
   * 阻止，但在 SourceBuffer append 的同一事件循环内调用 play() 可能被
   * "interrupted by a call to pause()" 打断。延迟 50ms 可避开此问题。
   * 若仍失败（如后台标签页优化），最多重试 5 次，间隔 200ms。
   *
   * 针对 MSE 直播流的特殊处理：play() Promise 可能长时间 pending（既不
   * resolve 也不 reject），这是 Chromium 对 duration=Infinity 直播流的
   * 已知行为。添加 500ms 超时：超时后视为失败并重试，确保不会因 pending
   * Promise 卡死整个播放流程。一旦 readyState 升到 2+ 或收到 'playing'
   * 事件，后续重试会因状态检查而自动取消。
   *
   * live-edge 对齐后（_liveEdgeAligned=true），currentTime 已在缓冲区内，
   * play() 应该能快速 resolve。若仍 pending 说明对齐失败或缓冲区数据不足，
   * 重试 5 次（共 2.5s）后放弃，等待用户交互或更多 segment 到达。
   */
  private _tryPlay(retry: number): void {
    if (this._state !== 'playing') return  // 状态已变更，取消播放
    if (this._playRetryTimer) {
      clearTimeout(this._playRetryTimer)
    }
    this._playRetryTimer = setTimeout(() => {
      this._playRetryTimer = null
      if (this._state !== 'playing') return
      // 超时标志：play() Promise 长时间未 resolve 时主动重试
      let settled = false
      const playTimeout = setTimeout(() => {
        if (settled) return
        settled = true
        if (retry < 5 && this._state === 'playing') {
          this._log(`play() timeout, retry ${retry + 1}/5`)
          this._tryPlay(retry + 1)
        } else {
          this._log('play() timeout, max retries reached')
          // media clock 冻结：重试耗尽仍无法播放，交给 stall recovery 强制 seek
          this._playExhausted = true
        }
      }, 300)
      this._video.play().then(() => {
        if (settled) return
        settled = true
        clearTimeout(playTimeout)
        this._playExhausted = false
        this._log('play() succeeded')
      }).catch((err) => {
        if (settled) return
        settled = true
        clearTimeout(playTimeout)
        if (retry < 5 && this._state === 'playing') {
          this._log(`play() failed (retry ${retry + 1}/5): ${err.message}`)
          this._tryPlay(retry + 1)
        } else {
          this._log(`play() failed after ${retry + 1} attempts: ${err.message}`)
          // 不改变 state 为 paused：数据流正常，用户交互后可恢复
          this._playExhausted = true
        }
      })
    }, retry === 0 ? 50 : 200)
  }

  private _isQuotaExceededError(e: unknown): boolean {
    if (e instanceof DOMException) {
      return e.name === 'QuotaExceededError' || e.code === 22
    }
    const str = String(e || '')
    return str.includes('QuotaExceededError') || str.includes('The SourceBuffer is full') || str.includes('cannot free space')
  }

  private _handleAppendError(e: unknown, seg: Uint8Array, context: string): void {
    if (!this._isQuotaExceededError(e)) {
      this._handleError(`${context} failed: ${e}`)
      return
    }

    this._quotaRetryCount++
    if (this._quotaRetryCount > 3) {
      this._log(`QuotaExceededError recovery exhausted (${this._quotaRetryCount}/3), aborting`)
      this._handleError(`Media buffer quota exceeded: ${e}`)
      return
    }

    // 将当前未写入的 segment 重新推入待处理队列前端
    this._pendingSegments.unshift(seg)

    // 自适应缩减保留时长：配额紧张时逐步减半，最低保留 30 秒
    this._replayBufferSeconds = Math.max(30, Math.floor(this._replayBufferSeconds * 0.6))
    this._log(`QuotaExceededError handled: buffer target reduced to ${this._replayBufferSeconds}s, triggering emergency eviction`)

    // 立即执行紧急驱逐腾出空间
    this._emergencyEvict()
  }

  /**
   * 紧急驱逐：当发生 QuotaExceededError 时，立刻释放当前播放头安全范围之外的所有缓冲。
   */
  private _emergencyEvict(): boolean {
    if (!this._sourceBuffer || this._sourceBuffer.updating || !this._video) {
      return false
    }
    const buffered = this._video.buffered
    if (buffered.length === 0) return false

    const bufStart = buffered.start(0)
    const bufEnd = buffered.end(buffered.length - 1)
    const curTime = this._video.currentTime

    // 优先清除当前播放头 10 秒之前的历史数据
    let removeStart = bufStart
    let removeEnd = Math.max(bufStart, curTime - 10)

    // 如果播放头正好在最左端（回看中），则清除当前播放头 15 秒之后的未来数据
    if (removeEnd <= bufStart + 2 && bufEnd > curTime + 15) {
      removeStart = curTime + 15
      removeEnd = bufEnd
    }

    if (removeEnd > removeStart + 1) {
      try {
        this._isTrimming = true
        this._log(`Emergency eviction: removing range [${removeStart.toFixed(1)}, ${removeEnd.toFixed(1)}]`)
        this._sourceBuffer.remove(removeStart, removeEnd)
        return true
      } catch (err) {
        this._isTrimming = false
        this._log(`Emergency eviction failed: ${err}`)
        return false
      }
    }
    return false
  }

  private _handleError(msg: string): void {
    this._log(`ERROR: ${msg}`)
    this._setState('error')
    this._stopStallDetection()
    this._onError?.(msg)
  }

  /** 数据成功写入 SourceBuffer 后切到 playing（若当前可播放）。

   仅在 idle/loading 且非 error/paused 时切换，避免覆盖错误或用户主动暂停态。
   不直接调 _setState('playing') 以免在 paused 时抢回播放控制。
   在多个 readyState 升级路径（updateend、loadeddata、canplay、playing、seeked）中被调用。
   */
  private _markPlaying(): void {
    if (this._state === 'error' || this._state === 'paused') return
    if (this._state !== 'playing') {
      this._setState('playing')
    }
  }

  private _log(msg: string | (() => string)): void {
    if (this._debug) {
      console.log(`[MsePlayer] ${typeof msg === 'function' ? msg() : msg}`)
    }
  }

  /**
   * 卡顿检测：定期检查 currentTime 是否在前进。
   * 检测间隔 500ms，容忍 1.5 秒停滞（从原来的 1s/3s 缩短）。
   * 恢复策略：
   *   a. currentTime 在缓冲区外 → seek 到 bufEnd-0.3
   *   b. currentTime 在缓冲区内 → 重新 play()
   *   c. 缓冲区为空 → 等待新数据到达（不盲目 seek）
   */
  private _startStallDetection(): void {
    this._stopStallDetection()
    this._stallCheckTimer = setInterval(() => {
      if (this._state !== 'playing') return
      // 用户主动 seek 保护期（3 秒）：给解码器加载与渲染时间，暂停强制跳回直播沿
      if (Date.now() - this._lastSeekTime < 3000) {
        return
      }
      const ct = this._video?.currentTime ?? 0

      // 数据饥饿检测：若 buffer 末端长时间不增长，判定流中断
      if (this._video && this._video.buffered.length > 0) {
        const bufEnd = this._video.buffered.end(this._video.buffered.length - 1)
        if (bufEnd > this._lastBufferEnd) {
          this._lastBufferEnd = bufEnd
          this._lastBufferEndTime = Date.now()
          if (this._stallRecoveryCount > 0) {
            this._stallRecoveryCount = 0
            this._log('Buffer resumed growth, reset recovery count')
          }
        }
      }

      if (Math.abs(ct - this._lastStallPosition) > 0.1) {
        this._lastStallPosition = ct
        this._lastStallTime = 0
        // currentTime 真实前进：播放恢复正常，重置强制 seek 计数与冻结标志
        this._forcedSeekRecoveryCount = 0
        this._playExhausted = false
        return
      }
      if (this._lastStallTime === 0) {
        this._lastStallTime = Date.now()
        return
      }
      const stallDuration = Date.now() - this._lastStallTime
      if (stallDuration < 1500) return

      // 超过 1.5 秒卡顿，尝试恢复
      this._log(`Stall detected (${(stallDuration / 1000).toFixed(1)}s), attempting recovery`)
      this._lastStallTime = 0

      const video = this._video
      if (!video || video.buffered.length === 0) {
        this._log('Stall recovery: buffer empty, waiting for data')
        return
      }

      // 数据饥饿防线：buffer 超过 8s 未增长，停止自动恢复并报错
      const now = Date.now()
      if (this._lastBufferEndTime > 0 && now - this._lastBufferEndTime > this._bufferStallTimeoutMs) {
        const waitSec = ((now - this._lastBufferEndTime) / 1000).toFixed(1)
        this._log(`Buffer stalled for ${waitSec}s, treating as stream failure`)
        // error 是终态（feedInit/feedMedia 直接忽略），文案如实描述，不承诺自动恢复
        this._handleError('直播流连接中断，预览已停止，请重新开启预览')
        return
      }

      // 连续恢复次数上限：超过 3 次停止自动恢复，避免无限循环占满主线程
      this._stallRecoveryCount++
      if (this._stallRecoveryCount > this._stallRecoveryLimit) {
        this._log(`Stall recovery limit reached (${this._stallRecoveryCount}/${this._stallRecoveryLimit}), stopping auto-recovery`)
        this._handleError('预览恢复失败，请手动重新开启预览')
        return
      }

      const bufferedRanges = this.getBufferedRanges()
      const bufEnd = bufferedRanges[bufferedRanges.length - 1]?.end ?? video.buffered.end(video.buffered.length - 1)
      const bufStart = bufferedRanges[0]?.start ?? video.buffered.start(0)
      const currentRange = bufferedRanges.find(range => ct >= range.start && ct <= range.end)

      // 强制 seek 恢复：currentTime 出界，或 media clock 冻结（play() 重试耗尽仍不动）。
      // 回看时保持当前连续 range；文件流出界则回到文件缓冲起点，直播才回 live edge。
      if (!currentRange || ct < bufStart || ct > bufEnd - 0.3 || this._playExhausted) {
        const isReviewing = Boolean(currentRange && bufEnd - ct > 3.0)
        const target = isReviewing
          ? Math.max(currentRange!.start, ct)
          : this._isFile
            ? Math.min(bufEnd, bufStart + 0.3)
            : Math.max(bufStart, bufEnd - 0.3)
        this._log(`Stall recovery: force seek ${ct.toFixed(2)} -> ${target.toFixed(2)} (buffered ${bufStart.toFixed(2)}-${bufEnd.toFixed(2)})`)
        // 强制 seek 独立限流：不受 buffer 增长重置（否则"buffer 持续增长 + media clock 冻结"会无限循环）
        this._forcedSeekRecoveryCount++
        if (this._forcedSeekRecoveryCount > this._stallRecoveryLimit) {
          this._log(`Forced seek recovery limit reached (${this._forcedSeekRecoveryCount}/${this._stallRecoveryLimit}), stopping auto-recovery`)
          this._handleError('预览恢复失败，请手动重新开启预览')
          return
        }
        try {
          video.currentTime = target
        } catch {}
        this._liveEdgeAligned = false
        this._playExhausted = false
        this._tryPlay(0)
      } else {
        this._log(`Stall recovery: re-trigger play() at ${ct.toFixed(2)} (buffered ${bufStart.toFixed(2)}-${bufEnd.toFixed(2)})`)
        this._tryPlay(0)
      }
    }, 500)
  }
  private _stopStallDetection(): void {
    if (this._stallCheckTimer) {
      clearInterval(this._stallCheckTimer)
      this._stallCheckTimer = null
    }
  }
}
