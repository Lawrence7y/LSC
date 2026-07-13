/** 预览音频峰值环缓冲 — 复用 MSE player 的共享 MediaElementSource。 */
export class WaveformPeakBuffer {
  readonly bucketSec: number
  private peaks = new Map<number, number>()
  private analyser: AnalyserNode | null = null
  private zeroGain: GainNode | null = null
  private raf = 0

  constructor(bucketSec = 0.05) {
    this.bucketSec = bucketSec
  }

  attachFromRegistry(roomId: string): boolean {
    this.detach()
    const registry = (window as unknown as { __msePlayers?: Record<string, {
      audioSource?: MediaElementAudioSourceNode
      audioContext?: AudioContext
    }> }).__msePlayers
    const entry = registry?.[roomId]
    const audioSource = entry?.audioSource
    const ctx = entry?.audioContext
    if (!audioSource || !ctx) return false

    const analyser = ctx.createAnalyser()
    analyser.fftSize = 2048
    const zeroGain = ctx.createGain()
    zeroGain.gain.value = 0
    audioSource.connect(analyser)
    analyser.connect(zeroGain)
    zeroGain.connect(ctx.destination)
    this.analyser = analyser
    this.zeroGain = zeroGain
    return true
  }

  start(getCommonTime: () => number): void {
    if (!this.analyser) return
    const data = new Uint8Array(this.analyser.fftSize)
    const tick = () => {
      if (!this.analyser) return
      this.analyser.getByteTimeDomainData(data)
      let peak = 0
      for (let i = 0; i < data.length; i++) {
        const v = Math.abs(data[i] - 128) / 128
        if (v > peak) peak = v
      }
      const t = getCommonTime()
      if (Number.isFinite(t) && t >= 0) {
        const idx = Math.floor(t / this.bucketSec)
        const prev = this.peaks.get(idx) ?? 0
        this.peaks.set(idx, Math.max(prev, peak))
        const minIdx = idx - Math.floor(14400 / this.bucketSec)
        for (const k of this.peaks.keys()) {
          if (k < minIdx) this.peaks.delete(k)
        }
      }
      this.raf = requestAnimationFrame(tick)
    }
    this.raf = requestAnimationFrame(tick)
  }

  stop(): void {
    if (this.raf) cancelAnimationFrame(this.raf)
    this.raf = 0
  }

  detach(): void {
    this.stop()
    if (this.analyser) {
      try { this.analyser.disconnect() } catch { /* cleanup */ }
    }
    if (this.zeroGain) {
      try { this.zeroGain.disconnect() } catch { /* cleanup */ }
    }
    this.analyser = null
    this.zeroGain = null
  }

  sample(start: number, end: number, bars: number): number[] {
    const out: number[] = []
    const span = Math.max(end - start, 1e-6)
    for (let i = 0; i < bars; i++) {
      const t = start + (span * i) / bars
      const idx = Math.floor(t / this.bucketSec)
      out.push(this.peaks.get(idx) ?? 0)
    }
    return out
  }
}
