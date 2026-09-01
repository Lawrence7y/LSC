import { describe, expect, it } from 'vitest'
import {
  computeDvrLeftEdge,
  computeExpandedPreviewWindow,
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
  it('returns max(0, liveEdge - 120)', () => {
    expect(DVR_LOOKBACK_SEC).toBe(120)
    expect(computeDvrLeftEdge(500)).toBe(380)
    expect(computeDvrLeftEdge(60)).toBe(0)
  })
})

describe('computeExpandedPreviewWindow', () => {
  it('live without buffer: left = purple = previewPos − 120, ignores recordedHint', () => {
    const r = computeExpandedPreviewWindow({
      liveDvr: true,
      previewPos: 500,
      recordedHint: 3600,
      previewDuration: 3600,
      fileDuration: 3600,
    })
    expect(r.start).toBe(380)
    expect(r.end).toBe(500)
    expect(r.purple).toBe(380)
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
    expect(r.start).toBe(500)
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
    expect(r.start).toBe(500)
    expect(r.end).toBe(620)
    expect(r.purple).toBe(500)
    expect(r.liveEdge).toBe(620)
    expect(r.playheadPct).toBeCloseTo((610 - 500) / 120 * 100, 5)
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
