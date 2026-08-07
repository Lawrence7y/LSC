/** 从浏览器 <video> 元素捕获音频 PCM 用于预览对齐。
 *
 * 使用 captureStream() + MediaStreamAudioSourceNode（非 deprecated），
 * 每次捕获从当前流创建新 source，video.src 变化（MSE 重连）后依然有效。
 * 类为模块级单例，AudioWorklet 模块只加载一次。
 */
import { withMuteSyncSuppressed } from '@/utils/muteSyncGuard'

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
        const detail = e instanceof Error ? `${e.name}: ${e.message}` : String(e)
        console.error('[PreviewAudioAligner] loadWorklet failed:', detail, e)
        this.workletPromise = null
        return false
      }
    })()

    return this.workletPromise
  }

  /** Worklet 不可用时回退到 ScriptProcessorNode（CSP/环境限制下仍可对齐）。 */
  private captureWithScriptProcessor(
    roomId: string,
    source: AudioNode,
    ctx: AudioContext,
    duration: number,
    video: HTMLVideoElement,
    restoreMutedOverride: () => void,
  ): Promise<Float32Array | null> {
    const sampleRate = ctx.sampleRate
    const targetSamples = Math.ceil(duration * sampleRate)
    const bufferSize = 4096
    // AudioWorklet 被 CSP 拦截时的必要回退（ScriptProcessor 已弃用但仍可用）
    const processor = ctx.createScriptProcessor(bufferSize, 1, 1)
    const collected = new Float32Array(targetSamples)
    let offset = 0
    let settled = false

    const zeroGain = ctx.createGain()
    zeroGain.gain.value = 0

    return new Promise((resolve) => {
      const cleanup = () => {
        restoreMutedOverride()
        try { processor.onaudioprocess = null } catch { /* ignore */ }
        try { source.disconnect(processor) } catch { /* ignore */ }
        try { processor.disconnect() } catch { /* ignore */ }
        try { zeroGain.disconnect() } catch { /* ignore */ }
      }

      const finish = (samples: Float32Array | null, reason: string, extra?: Partial<PreviewAudioCaptureDiagnostics>) => {
        if (settled) return
        settled = true
        clearTimeout(timeout)
        cleanup()
        if (!samples) {
          this.setCaptureDiagnostics(roomId, { reason, ready_state: video.readyState, ...extra })
          resolve(null)
          return
        }
        this.setCaptureDiagnostics(roomId, {
          reason: 'ok',
          ready_state: video.readyState,
          has_audio_track: true,
          ...extra,
          sample_count: samples.length,
        })
        resolve(samples)
      }

      const timeout = setTimeout(() => {
        finish(null, 'capture_timeout')
      }, (duration + 4) * 1000)

      processor.onaudioprocess = (ev) => {
        if (settled) return
        const input = ev.inputBuffer.getChannelData(0)
        const len = Math.min(input.length, collected.length - offset)
        if (len > 0) {
          collected.set(input.subarray(0, len), offset)
          offset += len
        }
        if (offset < collected.length) return

        let sumSq = 0
        let peak = 0
        for (let i = 0; i < collected.length; i++) {
          const v = collected[i]
          const a = v < 0 ? -v : v
          if (a > peak) peak = a
          sumSq += v * v
        }
        const rms = Math.sqrt(sumSq / collected.length)
        if (peak < 1e-5 || rms < 1e-5) {
          console.warn(`[PreviewAudioAligner] ScriptProcessor silent for room ${roomId}`)
          finish(null, 'silent_audio', { rms, sample_count: collected.length })
          return
        }
        let normalized = collected
        if (peak > 0 && peak < 0.2) {
          const scale = 0.5 / peak
          normalized = new Float32Array(collected.length)
          for (let i = 0; i < collected.length; i++) normalized[i] = collected[i] * scale
        }
        const downsampled = this.downsample(normalized, sampleRate, 16000)
        console.log(
          `[PreviewAudioAligner] ScriptProcessor capture OK: room=${roomId}, samples=${downsampled.length}`,
        )
        finish(downsampled, 'ok', { rms, sample_count: downsampled.length })
      }

      try {
        source.connect(processor)
        processor.connect(zeroGain)
        zeroGain.connect(ctx.destination)
      } catch (e) {
        console.error(`[PreviewAudioAligner] ScriptProcessor connect failed for ${roomId}:`, e)
        finish(null, 'capture_exception')
      }
    })
  }

  async captureAudio(
    roomId: string,
    video: HTMLVideoElement,
    duration: number = 5.0,
  ): Promise<Float32Array | null> {
    const previousVolume = video.volume
    let mutedOverridden = false
    let volumeOverridden = false
    // 捕获期间覆盖成的目标值（取消静音）。restore 不再 remute，避免卡死预览。
    const overriddenMutedValue = false

    const restoreMutedOverride = () => {
      // 禁止 remute 回 true：Chromium + MediaElementSource 下会卡死 MSE play()。
      // 扬声器静音始终由 VideoPreview 的 GainNode 控制。
      if (mutedOverridden) {
        if (video.muted) {
          withMuteSyncSuppressed(video, () => {
            video.muted = false
          })
        }
        mutedOverridden = false
      }
      if (volumeOverridden) {
        if (Math.abs(video.volume - 1) < 1e-6) {
          video.volume = previousVolume
        }
        volumeOverridden = false
      }
    }

    /** Chromium：muted / volume=0 时 MediaElementSource 与 captureStream 都会出全零 PCM。 */
    const ensureElementAudible = async () => {
      if (video.muted) {
        withMuteSyncSuppressed(video, () => {
          video.muted = overriddenMutedValue
        })
        mutedOverridden = true
      }
      if (!(video.volume > 0.01)) {
        video.volume = 1
        volumeOverridden = true
      }
      // 已在播则不要 await play()：MSE 直播流上 play() Promise 可能长期 pending，拖死对齐。
      if (video.paused) {
        try {
          const playP = video.play()
          await Promise.race([
            playP,
            new Promise<void>((r) => setTimeout(r, 200)),
          ])
        } catch (e) {
          console.warn(`[PreviewAudioAligner] video.play() failed for room ${roomId}:`, e)
        }
        await new Promise<void>((r) => setTimeout(r, 80))
      }
    }

    // 捕获路径上可能已建立连接的节点，异常退出时统一断开，避免泄漏
    let connectedSource: AudioNode | null = null
    let workletNode: AudioWorkletNode | null = null
    let zeroGainNode: GainNode | null = null

    const disconnectPartial = () => {
      try { if (connectedSource && workletNode) connectedSource.disconnect(workletNode) } catch {}
      try { workletNode?.disconnect() } catch {}
      try { zeroGainNode?.disconnect() } catch {}
    }

    try {
      const ctx = await this.getContext()
      const ok = await this.loadWorklet(ctx)
      await ensureElementAudible()

      // 优先使用 VideoPreview 创建的共享 MediaElementSourceNode
      // 扬声器仍由 GainNode 控音；对齐只从 source 并联抽 PCM，不改扬声器增益
      const registry = window.__msePlayers
      let sharedSource = registry?.[roomId]?.audioSource as MediaElementAudioSourceNode | undefined

      // 注册表丢了图但 video 尚未 createMediaElementSource：现场建图（gain=0，对齐时不外放）
      if (!sharedSource) {
        try {
          const mes = ctx.createMediaElementSource(video)
          const gain = ctx.createGain()
          gain.gain.value = 0
          mes.connect(gain)
          gain.connect(ctx.destination)
          sharedSource = mes
          if (registry?.[roomId]) {
            registry[roomId] = {
              ...registry[roomId],
              audioSource: mes,
              gainNode: gain,
            }
          }
          console.log(`[PreviewAudioAligner] Created on-the-fly MediaElementSource for room ${roomId}`)
        } catch (e) {
          console.warn(
            `[PreviewAudioAligner] createMediaElementSource unavailable for ${roomId}, will try captureStream:`,
            e,
          )
        }
      }

      let source: AudioNode

      if (sharedSource) {
        source = sharedSource
        console.log(`[PreviewAudioAligner] Using shared MediaElementSource for room ${roomId}`)
      } else {
        // 回退：captureStream（必须先 ensureElementAudible，否则 muted 时全零；可能短暂外放）
        const v = video as HTMLVideoElement & {
          captureStream?: () => MediaStream
          mozCaptureStream?: () => MediaStream
        }
        const stream: MediaStream | undefined = v.captureStream?.() ?? v.mozCaptureStream?.()
        if (!stream) {
          console.error(`[PreviewAudioAligner] captureStream() not available for room ${roomId}`)
          this.setCaptureDiagnostics(roomId, { reason: 'capture_stream_unavailable', ready_state: video.readyState })
          restoreMutedOverride()
          return null
        }
        const audioTracks = stream.getAudioTracks()
        if (audioTracks.length === 0) {
          console.warn(`[PreviewAudioAligner] No audio tracks for room ${roomId}`)
          this.setCaptureDiagnostics(roomId, { reason: 'no_audio_track', ready_state: video.readyState, has_audio_track: false })
          restoreMutedOverride()
          return null
        }
        const audioStream = new MediaStream(audioTracks)
        source = ctx.createMediaStreamSource(audioStream)
        console.log(`[PreviewAudioAligner] Using captureStream fallback for room ${roomId}`)
      }

      if (!ok) {
        console.warn(`[PreviewAudioAligner] Worklet unavailable, falling back to ScriptProcessor for room ${roomId}`)
        return await this.captureWithScriptProcessor(
          roomId, source, ctx, duration, video, restoreMutedOverride,
        )
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
      // 记录已建立连接的节点，供异常路径对称断开
      connectedSource = source
      workletNode = node
      zeroGainNode = zeroGain

      console.log(`[PreviewAudioAligner] Capture started: room=${roomId}, target=${targetSamples} samples (${duration}s @ ${sampleRate}Hz)`)

      return new Promise((resolve) => {
        let settled = false

      const cleanup = () => {
        restoreMutedOverride()
        // 共享 MediaElementSource 只断开当前 recorder，保留扬声器路由
        disconnectPartial()
        connectedSource = null
        workletNode = null
        zeroGainNode = null
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

          // 静音检测：只丢弃「近乎全零」的数字静音。
          // 弱信号（如 rms≈3e-4）仍可能含可对齐的游戏音效；后端会做 std 归一化。
          let sumSq = 0
          let peak = 0
          for (let i = 0; i < samples.length; i++) {
            const v = samples[i]
            const a = v < 0 ? -v : v
            if (a > peak) peak = a
            sumSq += v * v
          }
          const rms = Math.sqrt(sumSq / samples.length)
          if (peak < 1e-5 || rms < 1e-5) {
            console.warn(`[PreviewAudioAligner] Room ${roomId} audio is silent (RMS=${rms.toFixed(8)}, peak=${peak.toFixed(8)}), discarding`)
            this.setCaptureDiagnostics(roomId, {
              reason: 'silent_audio',
              ready_state: video.readyState,
              rms,
              sample_count: samples.length,
            })
            resolve(null)
            return
          }

          // 峰值归一化：弱电平预览流也能稳定互相关（避免 quiet 房间被前端误杀）
          let normalized = samples
          if (peak > 0 && peak < 0.2) {
            const scale = 0.5 / peak
            normalized = new Float32Array(samples.length)
            for (let i = 0; i < samples.length; i++) normalized[i] = samples[i] * scale
          }

          const downsampled = this.downsample(normalized, sampleRate, 16000)
          this.setCaptureDiagnostics(roomId, {
            reason: 'ok',
            ready_state: video.readyState,
            has_audio_track: true,
            rms,
            sample_count: downsampled.length,
          })
          console.log(`[PreviewAudioAligner] Capture OK: room=${roomId}, samples=${samples.length} → ${downsampled.length} (16kHz), RMS=${rms.toFixed(6)}, peak=${peak.toFixed(6)}`)
          resolve(downsampled)
        }
      })
    } catch (e) {
      restoreMutedOverride()
      // 异常路径：断开已建立的音频节点连接（source→node→zeroGain→destination），避免泄漏
      disconnectPartial()
      console.error(`[PreviewAudioAligner] captureAudio failed for room ${roomId}:`, e)
      this.setCaptureDiagnostics(roomId, { reason: 'capture_exception', ready_state: video.readyState })
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
// 模块级可取消定时器，防止多次对齐堆叠。
// WeakMap：video 元素销毁后键自动释放，避免短生命周期元素被 Map 持有泄漏。
const _driftCorrectionTimers: WeakMap<HTMLVideoElement, ReturnType<typeof setTimeout>> = new WeakMap()

export function cancelDriftCorrection(video: HTMLVideoElement): void {
  const timer = _driftCorrectionTimers.get(video)
  if (timer) {
    clearTimeout(timer)
    _driftCorrectionTimers.delete(video)
    video.playbackRate = 1.0
  }
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
  video.play().catch((e) => {
    console.warn('[PreviewAudioAligner] video.play() failed after drift seek:', e)
  })
}
