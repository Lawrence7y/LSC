/**
 * MediaSource Player for Electron MSE preview.
 *
 * Receives fragmented MP4 segments (init + media) from WebSocket
 * and feeds them to a <video> element via MediaSource API.
 */

export type MsePlayerState = 'idle' | 'loading' | 'playing' | 'paused' | 'error'

export interface MsePlayerOptions {
  videoElement: HTMLVideoElement
  onStateChange?: (state: MsePlayerState) => void
  onError?: (error: string) => void
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

export class MsePlayer {
  private _video: HTMLVideoElement
  private _mediaSource: MediaSource | null = null
  private _sourceBuffer: SourceBuffer | null = null
  private _state: MsePlayerState = 'idle'
  private _onStateChange?: (state: MsePlayerState) => void
  private _onError?: (error: string) => void
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

  constructor(options: MsePlayerOptions) {
    this._video = options.videoElement
    this._onStateChange = options.onStateChange
    this._onError = options.onError
    this._debug = options.debug ?? false
  }

  get state(): MsePlayerState {
    return this._state
  }

  get videoElement(): HTMLVideoElement {
    return this._video
  }

  /** Start receiving init + media segments. */
  start(_url: string): void {
    this.stop()
    this._pendingSegments = []
    this._initReceived = false
    this._initSegment = null
    this._liveEdgeAligned = false
    this._setState('loading')
    this._initMediaSource()
  }

  /** Feed init segment (ftyp + moov boxes). Must be called before media segments. */
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

  /** Feed media segment (moof + mdat boxes). */
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
        this._sourceBuffer.appendBuffer(seg.buffer as ArrayBuffer)
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
      this._video.play().catch(() => {})
    }
  }

  /** Pause the video. */
  pause(): void {
    if (this._video) {
      this._video.pause()
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

  /** 重置 live-edge 对齐标志，允许下次 updateend 重新对齐 currentTime。
   * 用于全屏切换后恢复小预览播放：player 仍存活但 currentTime 可能落后 buffered 范围，
   * 重置后新 segment 到达会触发 live-edge 对齐。 */
  resetLiveEdgeAligned(): void {
    this._liveEdgeAligned = false
  }

  /** 恢复播放（从后台切回前台时主动调用）。
   *  重置 _tryPlay 重试计数，seek 到 live edge，调用 play()。 */
  resumePlayback(): void {
    if (this._state === 'error' || this._state === 'idle') return
    if (this._video && this._video.buffered.length > 0) {
      const bufStart = this._video.buffered.start(0)
      const bufEnd = this._video.buffered.end(this._video.buffered.length - 1)
      if (this._video.currentTime < bufStart || this._video.currentTime > bufEnd) {
        this._video.currentTime = Math.max(bufStart, bufEnd - 0.5)
      }
    }
    this._liveEdgeAligned = false
    if (this._state !== 'paused') {
      this._tryPlay(0)
    }
  }

  /** Get current playback time. */
  get currentTime(): number {
    return this._video?.currentTime ?? 0
  }

  /** Stop and clean up. */
  stop(): void {
    // 取消所有待执行的 play 重试
    if (this._playRetryTimer) {
      clearTimeout(this._playRetryTimer)
      this._playRetryTimer = null
    }
    this._setState('idle')
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

  private _initMediaSource(): void {
    this._cleanup()
    try {
      this._mediaSource = new MediaSource()
      this._video.src = URL.createObjectURL(this._mediaSource)
      // 用 AbortController 统一管理事件监听器，_cleanup 时 abort 即可全部移除（M14）
      this._abortController = new AbortController()
      const { signal } = this._abortController

      this._mediaSource.addEventListener('sourceopen', () => {
        if (!this._mediaSource || this._mediaSource.readyState !== 'open') return

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

  private _flushPending(): void {
    if (!this._sourceBuffer || this._sourceBuffer.updating) return

    // 记录进入时是否为 trim 触发的 updateend（_isTrimming=true 表示上一次
    // remove() 刚完成）。若为 true 则本次仅处理 _pendingSegments，跳过 trim
    // 分支，避免 trim 的 updateend 链式递归导致 SourceBuffer 卡在 updating=true。
    const wasTrimming = this._isTrimming

    while (this._pendingSegments.length > 0) {
      const seg = this._pendingSegments.shift()!
      try {
        this._sourceBuffer.appendBuffer(seg.buffer as ArrayBuffer)
        this._log(`Flushed pending segment (${seg.byteLength} bytes)`)
        // Only append one per updateend cycle
        if (this._sourceBuffer.updating) break
      } catch (e) {
        this._handleError(`Pending segment append failed: ${e}`)
        break
      }
    }

    // Trim SourceBuffer to prevent memory leak (keep last 5min for timeline seek-back)
    // 扩大缓冲区到 300 秒（5 分钟），让用户可以回看最近 5 分钟的内容
    // 阈值设为 310 秒，每次仅删除 10 秒数据，减少单次 remove 阻塞时间
    if (!wasTrimming && this._sourceBuffer && !this._sourceBuffer.updating && this._video) {
      const buffered = this._video.buffered
      if (buffered.length > 0) {
        const bufStart = buffered.start(0)
        const bufEnd = buffered.end(buffered.length - 1)
        const bufDuration = bufEnd - bufStart
        if (bufDuration > 310) {
          const removeEnd = bufEnd - 300
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

  private _cleanup(): void {
    if (this._sourceBuffer) {
      try {
        if (this._mediaSource?.readyState === 'open') {
          this._mediaSource.endOfStream()
        }
      } catch { /* ignore */ }
      this._sourceBuffer = null
    }
    if (this._mediaSource) {
      URL.revokeObjectURL(this._video.src)
      this._mediaSource = null
    }
  }

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
    this._onError?.(msg)
  }

  /** 数据成功写入 SourceBuffer 后切到 playing（若当前可播放）。

  仅在 idle/loading 且非 error/paused 时切换，避免覆盖错误或用户主动暂停态。
  不直接调 _setState('playing') 以免在 paused 时抢回播放控制。
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
}
