import { describe, expect, it } from 'vitest'
import {
  computeDvrLeftEdge,
  computeTimelineWindow,
  DVR_LOOKBACK_SEC,
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

  it('refine window overrides zoom/live', () => {
    const r = computeTimelineWindow({
      contentEnd: 2400,
      zoomLevel: 1,
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
})

describe('computeDvrLeftEdge', () => {
  it('returns max(0, liveEdge - 120)', () => {
    expect(DVR_LOOKBACK_SEC).toBe(120)
    expect(computeDvrLeftEdge(500)).toBe(380)
    expect(computeDvrLeftEdge(60)).toBe(0)
  })
})
