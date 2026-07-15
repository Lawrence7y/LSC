export type MsePlayerState = 'idle' | 'loading' | 'playing' | 'paused' | 'error'

export interface MsePlayerOptions {
  videoElement: HTMLVideoElement
  onStateChange?: (state: MsePlayerState) => void
  onError?: (error: string) => void
  onSourceOpen?: () => void
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
 * 4. 缓冲区超过 310s 时自动 trim 至 300s，既保留回看能力，又防止内存泄漏。
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
  private _debug: boolean
  private _pendingSegments: Uint8Array[] = []
  private _initReceived = false
  private _initSegment: Uint8Array | null = null
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
  private _currentBlobUrl: string | null = null

  constructor(options: MsePlayerOptions) {
    this._video = options.videoElement
    this._onStateChange = options.onStateChange
    this._onError = options.onError
    this._onSourceOpen = options.onSourceOpen
    this._debug = options.debug ?? false
  }

  get state(): MsePlayerState {
    return this._state
  }

  get videoElement(): HTMLVideoElement {
    return this._video
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
    this._liveEdgeAligned = false
    this._lastStallTime = 0
    this._lastStallPosition = 0
    this._lastBufferEndTime = 0
    this._stallRecoveryCount = 0
    this._setState('loading')
    this._initMediaSource()
    this._startStallDetection()
  }

  /**
   * 喂入 init segment（ftyp + moov boxes）。
   *
   * 建立 SourceBuffer 解码上下文，必须在 media segment 之前调用。
   * 若 SourceBuffer 尚未就绪，init 段会被 unshift 到 pending 队列头部，
   * 等待 _flushPending() 在 sourceopen 或 updateend 时处理。
   *
   * 重复调用会被忽略（init received 标志位保护）。
   *
   * @param data - init segment 的原始二进制数据（ArrayBuffer）
   */
  feedInit(data: ArrayBuffer): void {
    if (this._state === 'error') return
    if (this._initReceived) {
      this._log('Init already received, ignoring duplicate')
      return
    }
    this._initReceived = true
    this._initSegment = new Uint8Array(data)

    if (!this._sourceBuffer && this._mediaSource?.readyState === 'open') {
      this._createSourceBuffer()
    }

    if (this._sourceBuffer && !this._sourceBuffer.updating) {
      try {
        this._sourceBuffer.appendBuffer(data)
        this._log(`Init segment appended (${data.byteLength} bytes)`)
        // 注意：init 段只含 ftyp+moov 元数据，无视频帧。
        // 不在此处切到 playing —— 等首个 media 段 append 完成（updateend）
        // 且 <video>.readyState >= 2 (HAVE_CURRENT_DATA) 时再切，
        // 避免出现 state='playing' 但画面黑屏的问题。
        // Flush any pending media segments
        this._flushPending()
      } catch (e) {
        this._handleError(`Init segment append failed: ${e}`)
      }
    } else {
      // Buffer not ready yet, queue
      this._pendingSegments.unshift(new Uint8Array(data))
      this._log(`Init segment queued (${data.byteLength} bytes)`)
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

    const seg = new Uint8Array(data)
    if (!this._initReceived || (this._sourceBuffer && this._sourceBuffer.updating)) {
      this._pendingSegments.push(seg)
      if (this._pendingSegments.length > this._maxPendingSegments) {
        // 保留最新的分段，丢弃最旧的（直播流丢弃旧帧优于堆积）
        this._pendingSegments.shift()
        this._log(`Dropping oldest segment (pending > ${this._maxPendingSegments})`)
      }
      return
    }

    if (this._sourceBuffer && !this._sourceBuffer.updating) {
      try {
        this._sourceBuffer.appendBuffer(seg.buffer.slice(seg.byteOffset, seg.byteOffset + seg.byteLength) as ArrayBuffer)
        this._log(`Media segment appended (${data.byteLength} bytes)`)
        // 持续收到媒体分段说明流正在播放，确保状态为 playing。
        this._markPlaying()
      } catch (e) {
        this._handleError(`Media segment append failed: ${e}`)
      }
    } else {
      this._pendingSegments.push(seg)
    }
  }

  /** Play the video. */
  play(): void {
    if (this._video && this._state !== 'error') {
      this._video.play().catch((err) => {
        console.warn('[MsePlayer] play() failed:', err)
      })
    }
  }

  /** Pause the video. */
  pause(): void {
    if (this._video) {
      this._video.pause()
    }
    if (this._state === 'playing') {
      this._setState('paused')
    }
  }

  /** Seek to a time in seconds. */
  seek(time: number): void {
    if (this._video && this._video.duration) {
      this._video.currentTime = Math.min(time, this._video.duration)
    }
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
  resumePlayback(userInitiated = false): void {
    if (this._state === 'error' || this._state === 'idle') return
    if (this._video && this._video.buffered.length > 0) {
      const bufStart = this._video.buffered.start(0)
      const bufEnd = this._video.buffered.end(this._video.buffered.length - 1)
      if (this._video.currentTime < bufStart || this._video.currentTime > bufEnd) {
        this._video.currentTime = Math.max(bufStart, bufEnd - 0.5)
      }
    }
    this._liveEdgeAligned = false
    if (userInitiated && this._state === 'paused') {
      this._setState('playing')
      return
    }
    if (this._state !== 'paused') {
      this._tryPlay(0)
    }
  }

  /**
   * 强制跳到直播最新位置。
   *
   * 与 resumePlayback() 不同，这里即使 currentTime 仍在缓冲区内，也会主动
   * seek 到缓冲区末尾附近，用于控制栏“直播”按钮。
   */
  goLive(): void {
    if (this._state === 'error' || this._state === 'idle') return
    if (this._video && this._video.buffered.length > 0) {
      const bufStart = this._video.buffered.start(0)
      const bufEnd = this._video.buffered.end(this._video.buffered.length - 1)
      const target = Math.max(bufStart, bufEnd - 0.3)
      this._video.currentTime = target
    } else {
      this._log('goLive: buffer empty, waiting for next segment')
    }
    this._liveEdgeAligned = false
    if (this._state === 'paused') {
      this._setState('playing')
    } else {
      this._tryPlay(0)
    }
  }

  /** 返回当前 SourceBuffer 可 seek 区间（preview 轴秒）；无缓冲则 null */
  getBufferedRange(): { start: number; end: number } | null {
    if (!this._video || this._video.buffered.length === 0) return null
    const start = this._video.buffered.start(0)
    const end = this._video.buffered.end(this._video.buffered.length - 1)
    if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return null
    return { start, end }
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
   * - 当 buffered 总时长超过 310s 时，移除 [bufStart, bufEnd - 300] 区间的旧数据，
   *   保留最近 5 分钟供用户回看。
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

    // 修复：每次只 append 一个 segment，不使用 while 循环。
    // Chromium 可能在下一个微任务才设置 sourceBuffer.updating=true，
    // while 循环中多次 append 会触发 InvalidStateError。
    // 下一个 updateend 事件会继续消费 pending 队列。
    if (this._pendingSegments.length > 0) {
      const seg = this._pendingSegments.shift()!
      try {
        this._sourceBuffer.appendBuffer(seg.buffer.slice(seg.byteOffset, seg.byteOffset + seg.byteLength) as ArrayBuffer)
        this._log(`Flushed pending segment (${seg.byteLength} bytes)`)
      } catch (e) {
        this._handleError(`Pending segment append failed: ${e}`)
      }
    }

    // Trim SourceBuffer to prevent memory leak (keep last 2min for timeline seek-back)
    // 优化：阈值从 310s 降到 130s，减少单次 trim 数据量和阻塞时间
    if (!wasTrimming && this._sourceBuffer && !this._sourceBuffer.updating && this._video) {
      const buffered = this._video.buffered
      if (buffered.length > 0) {
        const bufStart = buffered.start(0)
        const bufEnd = buffered.end(buffered.length - 1)
        const bufDuration = bufEnd - bufStart
        if (bufDuration > 130) {
          const removeEnd = bufEnd - 120
          if (removeEnd > bufStart) {
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

    if (selectedMime === fallback && mime !== fallback) {
      console.warn('[MsePlayer] Audio codec not supported by browser, falling back to video-only — no audio will be available')
    }

    if (selectedMime === fallback && mime.includes(',mp4a')) {
      console.warn('[MsePlayer] Audio codec not supported, falling back to video-only — no audio will be available')
    }

    if (selectedMime === fallback && mime.includes(',mp4a')) {
      console.warn('[MsePlayer] Audio codec not supported by browser, falling back to video-only — no audio will be available')
    }

    this._sourceBuffer = this._mediaSource.addSourceBuffer(selectedMime)
    const signal = this._abortController?.signal
    this._sourceBuffer.addEventListener('updateend', () => {
      this._flushPending()
      // 诊断：记录每次 updateend 时的 readyState、buffered 范围、currentTime
      const rs = this._video?.readyState ?? 0
      const vw = this._video?.videoWidth ?? 0
      const vh = this._video?.videoHeight ?? 0
      const dur = this._video?.duration ?? 0
      const buf = this._video?.buffered
      const bufLen = buf?.length ?? 0
      const bufStart = bufLen > 0 ? buf!.start(0) : -1
      const bufEnd = bufLen > 0 ? buf!.end(bufLen - 1) : -1
      const curTime = this._video?.currentTime ?? 0
      this._log(`updateend readyState=${rs} videoSize=${vw}x${vh} duration=${dur} buffered=${bufLen}[${bufStart.toFixed(2)}-${bufEnd.toFixed(2)}] currentTime=${curTime.toFixed(2)}`)

      if (this._video && this._video.readyState >= 2) {
        // readyState 已升到 2+：正常播放
        this._markPlaying()
      } else if (this._video && this._video.readyState < 2 && bufLen > 0 && !this._liveEdgeAligned) {
        // live-edge 对齐：duration=Infinity 的 MSE 直播流，currentTime 默认为 0，
        // 但首段 tfdt 可能不为 0，导致 currentTime 落在 buffered 之外，
        // play() Promise 一直 pending，readyState 卡在 1。
        // 一次性跳到 live edge（buffered.end - 0.2），让 currentTime 进入缓冲区。
        this._liveEdgeAligned = true
        const target = Math.max(bufStart, bufEnd - 0.2)
        // 仅当 currentTime 不在缓冲区内时才 seek
        if (curTime < bufStart || curTime > bufEnd) {
          this._log(`Live-edge align: currentTime ${curTime.toFixed(2)} -> ${target.toFixed(2)} (buffered ${bufStart.toFixed(2)}-${bufEnd.toFixed(2)})`)
          try {
            this._video.currentTime = target
          } catch (e) {
            this._log(`Live-edge align failed: ${e}`)
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
        if (this._state !== 'error' && this._state !== 'paused') {
          this._markPlaying()
        }
      }, videoSignal ? { signal: videoSignal } : undefined)
      // 'seeked' 事件：live-edge 对齐的 seek 完成后触发播放。
      // seek 完成后 currentTime 已在缓冲区内，play() 应能快速 resolve。
      this._video.addEventListener('seeked', () => {
        this._log(`video seeked event, currentTime=${this._video?.currentTime?.toFixed(2)}`)
        if (this._state !== 'error' && this._state !== 'paused') {
          this._markPlaying()
        }
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
        }
      }, 300)
      this._video.play().then(() => {
        if (settled) return
        settled = true
        clearTimeout(playTimeout)
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
        }
      })
    }, retry === 0 ? 50 : 200)
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

  private _log(msg: string): void {
    if (this._debug) {
      console.log(`[MsePlayer] ${msg}`)
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
        this._handleError('直播流连接中断，正在尝试自动恢复...')
        return
      }

      // 连续恢复次数上限：超过 3 次停止自动恢复，避免无限循环占满主线程
      this._stallRecoveryCount++
      if (this._stallRecoveryCount > this._stallRecoveryLimit) {
        this._log(`Stall recovery limit reached (${this._stallRecoveryCount}/${this._stallRecoveryLimit}), stopping auto-recovery`)
        this._handleError('预览恢复失败，请手动重新开启预览')
        return
      }

      const bufEnd = video.buffered.end(video.buffered.length - 1)
      const bufStart = video.buffered.start(0)

      if (ct < bufStart || ct > bufEnd - 0.3) {
        const target = Math.max(bufStart, bufEnd - 0.3)
        this._log(`Stall recovery: seek ${ct.toFixed(2)} -> ${target.toFixed(2)} (buffered ${bufStart.toFixed(2)}-${bufEnd.toFixed(2)})`)
        try {
          video.currentTime = target
        } catch {}
        this._liveEdgeAligned = false
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
