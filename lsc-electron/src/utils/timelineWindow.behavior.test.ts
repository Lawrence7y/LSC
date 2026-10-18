import { describe, expect, it } from 'vitest'
import {
  clampSeekToRange,
  computeDvrLeftEdge,
  computeExpandedPreviewWindow,
  computeTimelineWindow,
  DVR_BUFFER_EDGE_TOLERANCE_SEC,
  DVR_SEEK_EDGE_MARGIN_SEC,
  isWithinSeekRange,
  TIMELINE_MAX_WINDOW,
} from './timelineWindow'
import { normalizeReplayBufferSeconds, DEFAULT_TIMELINE_REPLAY_SECONDS } from './replaySettings'

describe('seek 落点收口：能点的范围 = 真能回放的范围', () => {
  it('isWithinSeekRange 含边界容差：贴边一两秒不算缓冲外', () => {
    expect(isWithinSeekRange(30, 30, 100)).toBe(true)
    expect(isWithinSeekRange(100, 30, 100)).toBe(true)
    expect(isWithinSeekRange(30 - DVR_BUFFER_EDGE_TOLERANCE_SEC + 0.1, 30, 100)).toBe(true)
    expect(isWithinSeekRange(30 - DVR_BUFFER_EDGE_TOLERANCE_SEC - 0.5, 30, 100)).toBe(false)
    expect(isWithinSeekRange(100 + DVR_BUFFER_EDGE_TOLERANCE_SEC + 0.5, 30, 100)).toBe(false)
    expect(isWithinSeekRange(Number.NaN, 30, 100)).toBe(false)
  })

  it('clampSeekToRange 把落点收进缓冲内侧（左右各留安全边距）', () => {
    expect(clampSeekToRange(50, 30, 100)).toBe(50)
    expect(clampSeekToRange(0, 30, 100)).toBe(30 + DVR_SEEK_EDGE_MARGIN_SEC)
    expect(clampSeekToRange(200, 30, 100)).toBe(100 - DVR_SEEK_EDGE_MARGIN_SEC)
    // 极窄缓冲：hi 钳到 lo，不产生倒置区间
    expect(clampSeekToRange(50, 30, 30.1)).toBe(30 + DVR_SEEK_EDGE_MARGIN_SEC)
  })
})

describe('computeDvrLeftEdge：紫线 = liveEdge − lookback，左界不为负', () => {
  it('正常 liveEdge 减 lookback', () => {
    expect(computeDvrLeftEdge(400, 120)).toBe(280)
  })

  it('liveEdge 不足 lookback 时归零（光标停最左边）', () => {
    expect(computeDvrLeftEdge(50, 120)).toBe(0)
    expect(computeDvrLeftEdge(0, 120)).toBe(0)
    expect(computeDvrLeftEdge(-5, 120)).toBe(0)
  })

  it('非法 replaySeconds 回落到默认档', () => {
    expect(computeDvrLeftEdge(400, Number.NaN)).toBe(400 - DEFAULT_TIMELINE_REPLAY_SECONDS)
  })
})

describe('computeExpandedPreviewWindow：可点 == 可立即回放', () => {
  it('无缓冲：左端 = liveEdge − 设置时长，忽略 recordedHint', () => {
    const w = computeExpandedPreviewWindow({
      liveDvr: true,
      previewPos: 400,
      replaySeconds: 120,
    })
    expect(w.start).toBe(280)
    expect(w.end).toBe(400)
    expect(w.purple).toBe(280)
    expect(w.hasLiveDvr).toBe(true)
  })

  it('缓冲比设置浅：左端回到真实缓冲起点（点缓冲左侧不得进文件回看）', () => {
    const w = computeExpandedPreviewWindow({
      liveDvr: true,
      previewPos: 400,
      bufferedStart: 350,
      bufferedEnd: 400,
      replaySeconds: 300,
    })
    // settingStart = 100，但 buf.start=350 更靠右 → start=350
    expect(w.start).toBe(350)
    expect(w.purple).toBe(350)
  })

  it('缓冲比设置深：设置值作为上限', () => {
    const w = computeExpandedPreviewWindow({
      liveDvr: true,
      previewPos: 400,
      bufferedStart: 50,
      bufferedEnd: 400,
      replaySeconds: 120,
    })
    expect(w.start).toBe(280)
  })

  it('关闭 DVR 只暴露 live 沿', () => {
    const w = computeExpandedPreviewWindow({
      liveDvr: true,
      previewPos: 400,
      replaySeconds: 0,
    })
    expect(w.start).toBe(400)
    expect(w.end).toBe(400)
    expect(w.hasLiveDvr).toBe(false)
    expect(w.playheadPct).toBe(100)
  })

  it('followLive 钉右；scrub 用 previewPos 百分比', () => {
    const pinned = computeExpandedPreviewWindow({
      liveDvr: true,
      previewPos: 10,
      bufferedStart: 0,
      bufferedEnd: 100,
      replaySeconds: 50,
      followLive: true,
    })
    expect(pinned.playheadPct).toBe(100)

    const scrubbed = computeExpandedPreviewWindow({
      liveDvr: true,
      previewPos: 50,
      bufferedStart: 0,
      bufferedEnd: 100,
      replaySeconds: 50,
      followLive: false,
    })
    expect(scrubbed.playheadPct).toBeCloseTo(50, 5)
  })

  it('recording_review 从 0 到文件时长，无 120s DVR 窗', () => {
    const w = computeExpandedPreviewWindow({
      liveDvr: false,
      previewPos: 30,
      fileDuration: 120,
    })
    expect(w.start).toBe(0)
    expect(w.end).toBe(120)
    expect(w.hasLiveDvr).toBe(false)
    expect(w.playheadPct).toBeCloseTo(25, 5)
  })
})

describe('computeTimelineWindow：1x 左端恒为 0', () => {
  const base = {
    contentEnd: 1000,
    zoomLevel: 1,
    followLive: true,
    scrubbing: false,
    frozenWindowStart: null as number | null,
    playhead: 100,
    prevWindowStart: 0,
    refining: null as { start: number; end: number } | null,
  }

  it('1x followLive：windowStart=0，整段压进视口', () => {
    const r = computeTimelineWindow(base)
    expect(r.windowStart).toBe(0)
    expect(r.duration).toBe(1000)
    expect(r.visibleSpan).toBe(1000)
  })

  it('1x 在 scrub / 非 followLive / refining 下仍从 0 开始', () => {
    expect(computeTimelineWindow({ ...base, scrubbing: true, frozenWindowStart: 100 }).windowStart).toBe(0)
    expect(computeTimelineWindow({ ...base, followLive: false }).windowStart).toBe(0)
    expect(computeTimelineWindow({ ...base, refining: { start: 10, end: 20 } }).windowStart).toBe(0)
  })

  it('zoom>1 followLive：局部窗贴右缘', () => {
    const r = computeTimelineWindow({ ...base, zoomLevel: 4 })
    // visibleSpan = max(30, 1000/4) = 250
    expect(r.visibleSpan).toBe(250)
    expect(r.windowStart).toBe(750)
  })

  it('zoom>1 scrubbing：用冻结窗起点', () => {
    const r = computeTimelineWindow({
      ...base,
      zoomLevel: 4,
      scrubbing: true,
      frozenWindowStart: 100,
    })
    expect(r.windowStart).toBe(100)
  })

  it('refine 窗覆盖 zoom', () => {
    const r = computeTimelineWindow({
      ...base,
      zoomLevel: 8,
      refining: { start: 100, end: 120 },
    })
    // mid=110, half≈40 → ws≈70
    expect(r.windowStart).toBeCloseTo(70, 5)
  })

  it('TIMELINE_MAX_WINDOW 是 600（长内容滑窗上限）', () => {
    expect(TIMELINE_MAX_WINDOW).toBe(600)
  })
})

describe('normalizeReplayBufferSeconds', () => {
  it('非法值回落默认 300；0 表示关闭历史回放', () => {
    expect(normalizeReplayBufferSeconds(undefined)).toBe(DEFAULT_TIMELINE_REPLAY_SECONDS)
    expect(normalizeReplayBufferSeconds(-1)).toBe(DEFAULT_TIMELINE_REPLAY_SECONDS)
    expect(normalizeReplayBufferSeconds(1.5)).toBe(DEFAULT_TIMELINE_REPLAY_SECONDS)
    expect(normalizeReplayBufferSeconds(0)).toBe(0)
    expect(normalizeReplayBufferSeconds(120)).toBe(120)
  })
})
