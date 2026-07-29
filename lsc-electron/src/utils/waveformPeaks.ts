/** 预览音频峰值环缓冲 — 复用 MSE player 的共享 MediaElementSource。 */
export class WaveformPeakBuffer {
  readonly bucketSec: number
  private peaks = new Map<number, number>()
  private analyser: AnalyserNode | null = null
  private zeroGain: GainNode | null = null
  private raf = 0
  // 存活下界：key 随时间单调递增，小于该值的 key 已剪枝，
  // 剪枝时按下界区间增量删除（摊还 O(1)），避免每帧全量遍历 Map。
  private minLiveIdx = 0

  constructor(bucketSec = 0.05) {
    this.bucketSec = bucketSec
  }

  attachFromRegistry(roomId: string): boolean {
    this.detach()
    const registry = window.__msePlayers
    const entry = registry?.[roomId]
    const audioSource = entry?.audioSource
    if (!audioSource) return false
    const ctx = audioSource.context as AudioContext

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
    // 重入保护：先停掉旧循环，避免重复 start 叠加多个 rAF 循环（旧循环只能靠
    // analyser 置 null 自然终止，且 stop() 只能取消最后一个）
    this.stop()
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
        // 剪枝：key 随时间单调递增（用户回看时 t 回退则 minIdx 不前进），
        // 按 [minLiveIdx, minIdx) 区间增量删除，不每帧全量遍历 Map
        const minIdx = idx - Math.floor(14400 / this.bucketSec)
        if (minIdx > this.minLiveIdx) {
          for (let k = this.minLiveIdx; k < minIdx; k++) {
            this.peaks.delete(k)
          }
          this.minLiveIdx = minIdx
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
    // 清空旧波形数据，避免 reattach 其他房间后残留显示
    this.peaks.clear()
    this.minLiveIdx = 0
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
