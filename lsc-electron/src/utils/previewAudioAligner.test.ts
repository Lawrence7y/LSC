import { describe, expect, it } from 'vitest'
import { getAligner } from './previewAudioAligner'

/**
 * `previewAudioAligner` 的采样收尾链（静音判据 / 峰值归一化 / 降采样 / base64）。
 *
 * 这一簇原先只有 `tests/test_frontend_stability_guards.py` 里的源码子串匹配
 * （审计 §5：141 条文子守卫里 137 条零真实断言）。静音阈值 `1e-5` 与归一化目标
 * `0.5 / peak` 都是**数值行为**：写成 `1e-3` 或把 `peak` 换成 `rms` 时，
 * 文子守卫照样绿，而现场表现是"弱电平房间永远报有效音频不足"。
 */
type AlignerInternals = {
  _lastFinalizeRms: number | null
  _lastFinalizePeak: number | null
  finalizeSamples(samples: Float32Array, sampleRate: number): Float32Array | null
  downsample(buffer: Float32Array, fromRate: number, toRate: number): Float32Array
  base64Encode(samples: Float32Array): string
}

function internals(): AlignerInternals {
  return getAligner() as unknown as AlignerInternals
}

function filled(values: number[]): Float32Array {
  return Float32Array.from(values)
}

function peakOf(a: Float32Array): number {
  let m = 0
  for (const v of a) m = Math.max(m, Math.abs(v))
  return m
}

describe('预览音频采样收尾', () => {
  it('数字静音（peak/rms < 1e-5）被丢弃，并把实测电平留在实例上供诊断', () => {
    const a = internals()
    expect(a.finalizeSamples(filled([0, 0, 0, 0]), 48000)).toBeNull()
    expect(a._lastFinalizeRms).toBe(0)
    expect(a._lastFinalizePeak).toBe(0)
  })

  it('阈值两侧各测一次：1e-6 丢、1e-4 留（改阈值会被这两条同时抓到）', () => {
    const a = internals()
    const tiny = filled(Array.from({ length: 64 }, (_, i) => (i % 2 ? -1e-6 : 1e-6)))
    expect(a.finalizeSamples(tiny, 16000)).toBeNull()
    const weak = filled(Array.from({ length: 64 }, (_, i) => (i % 2 ? -1e-4 : 1e-4)))
    expect(a.finalizeSamples(weak, 16000)).not.toBeNull()
  })

  it('弱信号按峰值归一化到 0.5，避免后端拿到近平板的相关面', () => {
    const a = internals()
    const quiet = filled(Array.from({ length: 16000 }, (_, i) => Math.sin(i / 40) * 3e-4))
    const out = a.finalizeSamples(quiet, 16000)
    expect(out).not.toBeNull()
    expect(peakOf(out as Float32Array)).toBeGreaterThan(0.4)
    expect(peakOf(out as Float32Array)).toBeLessThan(0.7)
  })

  it('正常电平不被放大（peak ≥ 0.2 时原样降采样）', () => {
    const a = internals()
    const loud = filled(Array.from({ length: 16000 }, (_, i) => Math.sin(i / 40) * 0.9))
    const out = a.finalizeSamples(loud, 16000) as Float32Array
    expect(peakOf(out)).toBeGreaterThan(0.8)
    expect(peakOf(out)).toBeLessThanOrEqual(1.0)
  })

  it('降采样按 3:1 抽样并做低通，直流常量保持常量', () => {
    const a = internals()
    const dc = filled(Array.from({ length: 48000 }, () => 0.25))
    const out = a.downsample(dc, 48000, 16000)
    expect(out.length).toBe(16000)
    expect(peakOf(out.map((v) => v - 0.25) as Float32Array)).toBeLessThan(1e-6)
  })

  it('降采样前的低通必须压住 48k 的奈奎斯特纹波（防混叠）', () => {
    /**
     * 逐样本交替 ±1：3:1 直接抽例会原样抽出 ±1（假信号混进 16k 谱里），
     * 而实现里的 7 抽头均值窗把它压到 ≈1/7。负控证明：把窗改成单样本
     * （等价于"去掉低通"）本用例立刻变红 —— 只有直流常量的那条用例是测不到这件事的。
     */
    const a = internals()
    const ripple = filled(Array.from({ length: 48000 }, (_, i) => (i % 2 ? 1 : -1)))
    const out = a.downsample(ripple, 48000, 16000)
    expect(out.length).toBe(16000)
    expect(peakOf(out)).toBeLessThan(0.3)
  })

  it('同采样率时 downsample 是恒等（不改长度也不改值）', () => {
    const a = internals()
    const src = filled([1, -2, 3])
    const out = a.downsample(src, 16000, 16000)
    expect(Array.from(out)).toEqual([1, -2, 3])
  })

  it('base64Encode 与 atob 往返一致（后端按 float32 小端解析）', () => {
    const a = internals()
    const src = filled([0.5, -0.25, 0.125, 0])
    const b64 = a.base64Encode(src)
    const bin = atob(b64)
    const back = new Float32Array(bin.length / 4)
    for (let i = 0; i < back.length; i += 1) {
      const bytes = new Uint8Array(4)
      for (let j = 0; j < 4; j += 1) bytes[j] = bin.charCodeAt(i * 4 + j)
      back[i] = new Float32Array(bytes.buffer)[0]
    }
    expect(Array.from(back)).toEqual([0.5, -0.25, 0.125, 0])
    // 4 × float32 = 16 字节 ⇒ base64 长度 4·ceil(16/3) = 24
    expect(b64.length).toBe(24)
  })

  it('长数组分块编码不越界（8192 分块的边界回归）', () => {
    const a = internals()
    const big = filled(Array.from({ length: 8192 * 2 + 7 }, (_, i) => (i % 2 ? 0.5 : -0.5)))
    const b64 = a.base64Encode(big)
    // 长度按字节算：4·ceil(bytes/3)（先除再乘会因浮点取整少一个字符）
    expect(b64.length).toBe(4 * Math.ceil(big.byteLength / 3))
    expect(atob(b64).length).toBe(big.byteLength)
  })
})
