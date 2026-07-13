/** 从浏览器 <video> 元素捕获音频 PCM 用于预览对齐。
 *
 * 使用 captureStream() + MediaStreamAudioSourceNode（非 deprecated），
 * 每次捕获从当前流创建新 source，video.src 变化（MSE 重连）后依然有效。
 * 类为模块级单例，AudioWorklet 模块只加载一次。
 */

// ── AudioWorklet 处理器代码（内联 Blob） ──────────────────
const WORKLET_CODE = `
class PCMRecorder extends AudioWorkletProcessor {
  constructor(options) {
    super();
    this.buffer = new Float32Array(options.processorOptions.targetSamples);
    this.offset = 0;
    this.done = false;
  }
  process(inputs) {
    if (this.done) return false;
    const input = inputs[0];
    if (!input || !input[0]) return true;
    const ch = input[0];
    const len = Math.min(ch.length, this.buffer.length - this.offset);
    this.buffer.set(ch.subarray(0, len), this.offset);
    this.offset += len;
    if (this.offset >= this.buffer.length) {
      this.port.postMessage({ type: 'complete', samples: this.buffer }, [this.buffer.buffer]);
      this.done = true;
      return false;
    }
    return true;
  }
}
registerProcessor('pcm-recorder', PCMRecorder);
`;

export interface PreviewAudioCaptureDiagnostics {
  reason: string
  ready_state?: number
  has_audio_track?: boolean
  rms?: number | null
  sample_count?: number
}

class PreviewAudioAligner {
  private ctx: AudioContext | null = null
  private workletLoaded = false
  private workletPromise: Promise<boolean> | null = null
  private lastCaptureDiagnostics: Record<string, PreviewAudioCaptureDiagnostics> = {}

  getLastCaptureDiagnostics(roomId: string): PreviewAudioCaptureDiagnostics | undefined {
    return this.lastCaptureDiagnostics[roomId]
  }

  private setCaptureDiagnostics(roomId: string, diag: PreviewAudioCaptureDiagnostics): void {
    this.lastCaptureDiagnostics[roomId] = diag
  }

  private async getContext(): Promise<AudioContext> {
    if (!this.ctx) {
      this.ctx = new AudioContext()
      console.log('[PreviewAudioAligner] AudioContext created, sampleRate=' + this.ctx.sampleRate)
    }
    if (this.ctx.state === 'suspended') {
      console.log('[PreviewAudioAligner] Resuming suspended AudioContext...')
      await this.ctx.resume()
    }
    return this.ctx
  }

  getContextSync(): AudioContext {
    if (!this.ctx) {
      this.ctx = new AudioContext()
      console.log('[PreviewAudioAligner] AudioContext created (sync), sampleRate=' + this.ctx.sampleRate)
    }
    if (this.ctx.state === 'suspended') {
      this.ctx.resume().catch((e) => {
        console.warn('[PreviewAudioAligner] Failed to resume suspended AudioContext:', e)
      })
    }
    return this.ctx
  }

  private loadWorklet(ctx: AudioContext): Promise<boolean> {
    if (this.workletLoaded) return Promise.resolve(true)
    if (this.workletPromise) return this.workletPromise

    this.workletPromise = (async () => {
      try {
        const blob = new Blob([WORKLET_CODE], { type: 'application/javascript' })
        const url = URL.createObjectURL(blob)
        await ctx.audioWorklet.addModule(url)
        URL.revokeObjectURL(url)
        this.workletLoaded = true
        console.log('[PreviewAudioAligner] AudioWorklet module loaded')
        return true
      } catch (e) {
        console.error('[PreviewAudioAligner] loadWorklet failed:', e)
        this.workletPromise = null
        return false
      }
    })()

    return this.workletPromise
  }

  async captureAudio(
    roomId: string,
    video: HTMLVideoElement,
    duration: number = 5.0,
  ): Promise<Float32Array | null> {
    try {
      const ctx = await this.getContext()
      const ok = await this.loadWorklet(ctx)
      if (!ok) {
        console.error(`[PreviewAudioAligner] Worklet not loaded for room ${roomId}`)
        this.setCaptureDiagnostics(roomId, { reason: 'worklet_not_loaded', ready_state: video.readyState })
        return null
      }

      // 优先使用 VideoPreview 创建的共享 MediaElementSourceNode
      // 这样不受 video.muted 影响（音频已通过 Web Audio 路由，绕过 video 原生管线）
      const registry = (window as any).__msePlayers
      const sharedSource = registry?.[roomId]?.audioSource as MediaElementAudioSourceNode | undefined

      let source: AudioNode
      let isSharedSource = false

      if (sharedSource) {
        source = sharedSource
        isSharedSource = true
        console.log(`[PreviewAudioAligner] Using shared MediaElementSource for room ${roomId}`)
      } else {
        // 回退：captureStream（video.muted=true 时会产出全零数据）
        const v = video as any
        const stream: MediaStream | undefined = v.captureStream?.() ?? v.mozCaptureStream?.()
        if (!stream) {
          console.error(`[PreviewAudioAligner] captureStream() not available for room ${roomId}`)
          this.setCaptureDiagnostics(roomId, { reason: 'capture_stream_unavailable', ready_state: video.readyState })
          return null
        }
        const audioTracks = stream.getAudioTracks()
        if (audioTracks.length === 0) {
          console.warn(`[PreviewAudioAligner] No audio tracks for room ${roomId}`)
          this.setCaptureDiagnostics(roomId, { reason: 'no_audio_track', ready_state: video.readyState, has_audio_track: false })
          return null
        }
        const audioStream = new MediaStream(audioTracks)
        source = ctx.createMediaStreamSource(audioStream)
      }

      const sampleRate = ctx.sampleRate
      const targetSamples = Math.ceil(duration * sampleRate)

      const node = new AudioWorkletNode(ctx, 'pcm-recorder', {
        processorOptions: { targetSamples },
      })

      const zeroGain = ctx.createGain()
      zeroGain.gain.value = 0
      source.connect(node)
      node.connect(zeroGain)
      zeroGain.connect(ctx.destination)

      console.log(`[PreviewAudioAligner] Capture started: room=${roomId}, target=${targetSamples} samples (${duration}s @ ${sampleRate}Hz)`)

      return new Promise((resolve) => {
        let settled = false

      const cleanup = () => {
        // 共享 MediaElementSource 不能 disconnect（会影响 GainNode → 扬声器输出）
        if (!isSharedSource) {
          try { source.disconnect() } catch {}
        }
        try { node.disconnect() } catch {}
        try { zeroGain.disconnect() } catch {}
      }

        const timeout = setTimeout(() => {
          if (settled) return
          settled = true
          cleanup()
          console.warn(`[PreviewAudioAligner] Capture timeout for room ${roomId} (${duration + 4}s)`)
          this.setCaptureDiagnostics(roomId, { reason: 'capture_timeout', ready_state: video.readyState })
          resolve(null)
        }, (duration + 4) * 1000)

        node.port.onmessage = (e: MessageEvent) => {
          if (settled) return
          settled = true
          clearTimeout(timeout)
          cleanup()

          const samples = e.data.samples as Float32Array
          if (!samples || samples.length === 0) {
            console.warn(`[PreviewAudioAligner] Empty samples for room ${roomId}`)
            this.setCaptureDiagnostics(roomId, { reason: 'buffer_empty', ready_state: video.readyState })
            resolve(null)
            return
          }

          // 静音检测
          let sumSq = 0
          for (let i = 0; i < samples.length; i++) sumSq += samples[i] * samples[i]
          const rms = Math.sqrt(sumSq / samples.length)
          if (rms < 1e-6) {
            console.warn(`[PreviewAudioAligner] Room ${roomId} audio is silent (RMS=${rms.toFixed(8)}), discarding`)
            this.setCaptureDiagnostics(roomId, {
              reason: 'silent_audio',
              ready_state: video.readyState,
              rms,
              sample_count: samples.length,
            })
            resolve(null)
            return
          }

          const downsampled = this.downsample(samples, sampleRate, 16000)
          this.setCaptureDiagnostics(roomId, {
            reason: 'ok',
            ready_state: video.readyState,
            has_audio_track: true,
            rms,
            sample_count: downsampled.length,
          })
          console.log(`[PreviewAudioAligner] Capture OK: room=${roomId}, samples=${samples.length} → ${downsampled.length} (16kHz), RMS=${rms.toFixed(4)}`)
          resolve(downsampled)
        }
      })
    } catch (e) {
      console.error(`[PreviewAudioAligner] captureAudio failed for room ${roomId}:`, e)
      return null
    }
  }

  private downsample(buffer: Float32Array, fromRate: number, toRate: number): Float32Array {
    if (fromRate === toRate) return buffer
    const ratio = fromRate / toRate

    // 简易低通滤波器（抗锯齿）：窗口大小 = ceil(ratio)*2+1
    const filterLen = Math.ceil(ratio) * 2 + 1
    const halfLen = Math.floor(filterLen / 2)
    const filtered = new Float32Array(buffer.length)
    for (let i = 0; i < buffer.length; i++) {
      let sum = 0
      let count = 0
      const lo = Math.max(0, i - halfLen)
      const hi = Math.min(buffer.length - 1, i + halfLen)
      for (let j = lo; j <= hi; j++) {
        sum += buffer[j]
        count++
      }
      filtered[i] = sum / count
    }

    const newLength = Math.floor(buffer.length / ratio)
    const result = new Float32Array(newLength)
    for (let i = 0; i < newLength; i++) {
      const srcIdx = Math.round(i * ratio)
      result[i] = srcIdx < filtered.length ? filtered[srcIdx] : 0
    }
    return result
  }

  base64Encode(samples: Float32Array): string {
    const bytes = new Uint8Array(samples.buffer, samples.byteOffset, samples.byteLength)
    const CHUNK = 8192
    let binary = ''
    for (let i = 0; i < bytes.length; i += CHUNK) {
      const end = Math.min(i + CHUNK, bytes.length)
      const chunk = bytes.subarray(i, end)
      binary += String.fromCharCode.apply(null, chunk as unknown as number[])
    }
    return btoa(binary)
  }
}

// ── 模块级单例 ────────────────────────────────────────────
let _instance: PreviewAudioAligner | null = null

export function getAligner(): PreviewAudioAligner {
  if (!_instance) {
    _instance = new PreviewAudioAligner()
  }
  return _instance
}

// ── 漂移修正 ──────────────────────────────────────────────────────
// 模块级可取消定时器，防止多次对齐堆叠
 let _driftCorrectionTimers: Map<HTMLVideoElement, ReturnType<typeof setTimeout>> = new Map()

export function cancelDriftCorrection(video: HTMLVideoElement): void {
  const timer = _driftCorrectionTimers.get(video)
  if (timer) {
    clearTimeout(timer)
    _driftCorrectionTimers.delete(video)
    video.playbackRate = 1.0
  }
}

export function cancelAllDriftCorrections(): void {
  _driftCorrectionTimers.forEach((timer, video) => {
    clearTimeout(timer)
    video.playbackRate = 1.0
  })
  _driftCorrectionTimers.clear()
}

export function applyOffsetWithDriftCorrection(video: HTMLVideoElement, offset: number): void {
  // 先取消之前的漂移修正
  cancelDriftCorrection(video)

  const originalTime = video.currentTime
  const newTime = Math.max(originalTime - offset, 0)

  if (Math.abs(newTime - originalTime) < 0.05) return

  let seeked = false

  const onSeeked = () => {
    if (seeked) return
    seeked = true
    video.removeEventListener('seeked', onSeeked)

    const actualTime = video.currentTime
    const appliedOffset = originalTime - actualTime
    const residual = offset - appliedOffset

    if (Math.abs(residual) > 0.05) {
      // 缩小 playbackRate 调整范围到 [0.95, 1.05]，用户不可感知
      const maxCorrection = 0.05
      const correctionDuration = Math.abs(residual) / maxCorrection
      const rate = residual > 0 ? 1 - maxCorrection : 1 + maxCorrection
      video.playbackRate = rate
      const timer = setTimeout(() => {
        video.playbackRate = 1.0
        _driftCorrectionTimers.delete(video)
      }, correctionDuration * 1000)
      _driftCorrectionTimers.set(video, timer)
    }
  }

  video.addEventListener('seeked', onSeeked)
  try {
    video.currentTime = newTime
  } catch {
    video.removeEventListener('seeked', onSeeked)
  }
  video.play().catch(() => {})
}
