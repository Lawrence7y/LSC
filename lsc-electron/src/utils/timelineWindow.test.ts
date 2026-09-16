import { describe, expect, it } from 'vitest'
import {
  clampSeekToRange,
  computeDvrLeftEdge,
  computeExpandedPreviewWindow,
  computeTimelineWindow,
  DVR_LOOKBACK_SEC,
  isWithinSeekRange,
} from './timelineWindow'

describe('computeTimelineWindow', () => {
  it('1x followLive: windowStart is always 0 even when contentEnd > 600', () => {
    const r = computeTimelineWindow({
      contentEnd: 2400,
      zoomLevel: 1,
      followLive: true,
      scrubbing: false,
      frozenWindowStart: null,
      playhead: 2400,
      prevWindowStart: 1800,
      refining: null,
    })
    expect(r.windowStart).toBe(0)
    expect(r.duration).toBe(2400)
    expect(r.visibleSpan).toBe(2400)
  })

  it('zoom>1 followLive: local window ending at contentEnd (left > 0)', () => {
    const r = computeTimelineWindow({
      contentEnd: 2400,
      zoomLevel: 2,
      followLive: true,
      scrubbing: false,
      frozenWindowStart: null,
      playhead: 2400,
      prevWindowStart: 0,
      refining: null,
    })
    expect(r.visibleSpan).toBe(1200)
    expect(r.windowStart).toBe(1200)
    expect(r.duration).toBe(2400)
  })

  it('zoom>1 scrubbing: uses frozen window start', () => {
    const r = computeTimelineWindow({
      contentEnd: 2400,
      zoomLevel: 2,
      followLive: false,
      scrubbing: true,
      frozenWindowStart: 500,
      playhead: 1000,
      prevWindowStart: 500,
      refining: null,
    })
    expect(r.windowStart).toBe(500)
    expect(r.visibleSpan).toBe(1200)
  })

  it('refine window overrides zoom>1', () => {
    const r = computeTimelineWindow({
      contentEnd: 2400,
      zoomLevel: 2,
      followLive: true,
      scrubbing: false,
      frozenWindowStart: null,
      playhead: 2400,
      prevWindowStart: 0,
      refining: { start: 100, end: 130 },
    })
    expect(r.windowStart).toBeGreaterThan(0)
    expect(r.windowStart).toBeLessThan(100)
  })

  it('1x always starts at 0 even when scrubbing/not followLive/refining', () => {
    const r = computeTimelineWindow({
      contentEnd: 2400,
      zoomLevel: 1,
      followLive: false,
      scrubbing: true,
      frozenWindowStart: 500,
      playhead: 800,
      prevWindowStart: 500,
      refining: { start: 100, end: 130 },
    })
    expect(r.windowStart).toBe(0)
    expect(r.duration).toBe(2400)
    expect(r.visibleSpan).toBe(2400)
  })
})

describe('computeDvrLeftEdge', () => {
  it('returns max(0, liveEdge - configured replay duration)', () => {
    expect(DVR_LOOKBACK_SEC).toBe(300)
    expect(computeDvrLeftEdge(500)).toBe(200)
    expect(computeDvrLeftEdge(500, 120)).toBe(380)
    expect(computeDvrLeftEdge(60)).toBe(0)
  })
})

describe('computeExpandedPreviewWindow', () => {
  it('live without buffer: left = purple = previewPos − configured duration, ignores recordedHint', () => {
    const r = computeExpandedPreviewWindow({
      liveDvr: true,
      previewPos: 500,
      recordedHint: 3600,
      previewDuration: 3600,
      fileDuration: 3600,
    })
    expect(r.start).toBe(200)
    expect(r.end).toBe(500)
    expect(r.purple).toBe(200)
    expect(r.liveEdge).toBe(500)
    expect(r.hasLiveDvr).toBe(true)
    expect(r.playheadPct).toBe(100)
    expect(r.fillLeftPct).toBe(0)
    expect(r.fillWidthPct).toBe(100)
  })

  it('live followLive pins playhead to the right even if previewPos lags', () => {
    const r = computeExpandedPreviewWindow({
      liveDvr: true,
      followLive: true,
      previewPos: 0,
      bufferedStart: 490,
      bufferedEnd: 620,
    })
    // 设置 300s 只在缓冲更深时才是边界；此处真实缓冲只有 130s ⇒ 左端 = buf.start。
    expect(r.start).toBe(490)
    expect(r.end).toBe(620)
    expect(r.playheadPct).toBe(100)
    expect(r.fillWidthPct).toBe(100)
  })

  it('live defaults to followLive (right edge) when followLive is omitted', () => {
    const r = computeExpandedPreviewWindow({
      liveDvr: true,
      previewPos: 12,
      bufferedStart: 490,
      bufferedEnd: 620,
    })
    expect(r.playheadPct).toBe(100)
  })

  it('live DVR scrub uses previewPos, not the right edge', () => {
    const r = computeExpandedPreviewWindow({
      liveDvr: true,
      followLive: false,
      previewPos: 610,
      bufferedStart: 490,
      bufferedEnd: 620,
      recordedHint: 3600,
    })
    expect(r.start).toBe(490)
    expect(r.end).toBe(620)
    expect(r.purple).toBe(490)
    expect(r.liveEdge).toBe(620)
    expect(r.playheadPct).toBeCloseTo((610 - 490) / 130 * 100, 5)
  })

  it('缓冲比设置浅时，可点范围回到真实缓冲起点（能点 == 能立即回放）', () => {
    // 2026-09-15 真机：设置 300s、真实缓冲只有 157.2s。旧实现把
    // [liveEdge − 300, liveEdge] 整段画成可点区域，点在缓冲左侧会被 mseSeek
    // 判成缓冲外并切到本地文件回看通道（预览区显示「正在准备回看…」）。
    const r = computeExpandedPreviewWindow({
      liveDvr: true,
      previewPos: 516,
      bufferedStart: 358.9,
      bufferedEnd: 516.1,
      replaySeconds: 300,
    })
    expect(r.start).toBeCloseTo(358.9, 1)
    expect(r.end).toBeCloseTo(516.1, 1)
    expect(r.purple).toBeCloseTo(358.9, 1)
    expect(r.end - r.start).toBeCloseTo(157.2, 1)
  })

  it('缓冲比设置深时，设置值作为上限：左端 = liveEdge − 设置时长', () => {
    const r = computeExpandedPreviewWindow({
      liveDvr: true,
      previewPos: 1000,
      bufferedStart: 400,
      bufferedEnd: 1000,
      replaySeconds: 300,
    })
    expect(r.start).toBe(700)
    expect(r.end).toBe(1000)
    expect(r.end - r.start).toBe(300)
  })

  it('live under 120s starts at 0 even if recording is long', () => {
    const r = computeExpandedPreviewWindow({
      liveDvr: true,
      previewPos: 40,
      recordedHint: 600,
    })
    expect(r.start).toBe(0)
    expect(r.end).toBe(40)
    expect(r.purple).toBe(0)
    expect(r.playheadPct).toBe(100)
  })

  it('short/empty buffer falls back to previewPos, not recordedHint', () => {
    const r = computeExpandedPreviewWindow({
      liveDvr: true,
      previewPos: 12,
      bufferedStart: 10,
      bufferedEnd: 10.4,
      recordedHint: 1800,
    })
    expect(r.start).toBe(0)
    expect(r.end).toBe(12)
    expect(r.liveEdge).toBe(12)
  })

  it('disabled replay exposes only the live edge', () => {
    const r = computeExpandedPreviewWindow({
      liveDvr: true,
      replaySeconds: 0,
      previewPos: 610,
      bufferedStart: 490,
      bufferedEnd: 620,
    })
    expect(r.start).toBe(620)
    expect(r.end).toBe(620)
    expect(r.purple).toBe(620)
    expect(r.hasLiveDvr).toBe(false)
    expect(r.playheadPct).toBe(100)
  })

  it('recording_review uses file duration from 0, no 120s DVR window', () => {
    const r = computeExpandedPreviewWindow({
      liveDvr: false,
      previewPos: 30,
      previewDuration: 10,
      fileDuration: 180,
      markIn: 5,
      markOut: 40,
      recordedHint: 9999,
    })
    expect(r.start).toBe(0)
    expect(r.end).toBe(180)
    expect(r.purple).toBe(0)
    expect(r.hasLiveDvr).toBe(false)
    expect(r.playheadPct).toBeCloseTo(30 / 180 * 100, 5)
  })
})

describe('可点范围 = 可立即回放范围（seek 落点收口）', () => {
  it('isWithinSeekRange 含边界容差：贴边一两秒不算缓冲外', () => {
    // 分片边界 + 浮点误差常让“刚好点在左沿/右沿”越界零点几秒；旧实现因此把
    // 普通点击推进重量级的本地文件回看通道。
    expect(isWithinSeekRange(308.1, 308.1, 465.2)).toBe(true)
    expect(isWithinSeekRange(306.5, 308.1, 465.2)).toBe(true)
    expect(isWithinSeekRange(466.4, 308.1, 465.2)).toBe(true)
    // 超出容差仍然按缓冲外处理（更早的内容走文件回看，语义不变）
    expect(isWithinSeekRange(305.5, 308.1, 465.2)).toBe(false)
    expect(isWithinSeekRange(468, 308.1, 465.2)).toBe(false)
    expect(isWithinSeekRange(Number.NaN, 308.1, 465.2)).toBe(false)
  })

  it('clampSeekToRange 把落点收进缓冲内侧（左右各留安全边距）', () => {
    expect(clampSeekToRange(283.1, 308.1, 465.2)).toBeCloseTo(308.4, 5)
    expect(clampSeekToRange(465.2, 308.1, 465.2)).toBeCloseTo(464.9, 5)
    expect(clampSeekToRange(400, 308.1, 465.2)).toBe(400)
    // 退化区间（缓冲短于两个安全边距）：返回下沿，不产生 NaN/负值
    const tiny = clampSeekToRange(10, 10, 10.2)
    expect(Number.isFinite(tiny)).toBe(true)
    expect(tiny).toBeGreaterThanOrEqual(10)
  })
})
