/**
 * 时间线可视窗口纯计算（主轨 1x / zoom 局部窗 + DVR 左边界）。
 * 与 CLAUDE.md §8.7 一致：只用 preview/common 轴秒，禁止录制墙钟推窗。
 */
import { panTimelineWindowStart } from '@/utils/timelineCoords'

/** 长内容默认滑窗上限（秒）；1x Live 时不再使用，整段从 0 压缩。 */
export const TIMELINE_MAX_WINDOW = 600

/** 紫线 / 预览条左端：liveEdge − 此时长（秒）。 */
export const DVR_LOOKBACK_SEC = 120

export type RefineRange = { start: number; end: number }

export type TimelineWindowInput = {
  contentEnd: number
  zoomLevel: number
  followLive: boolean
  scrubbing: boolean
  frozenWindowStart: number | null
  playhead: number
  prevWindowStart: number
  refining: RefineRange | null
}

export type TimelineWindowResult = {
  windowStart: number
  duration: number
  visibleSpan: number
}

/** 预览/common 轴上的 DVR 左边界（紫线）。 */
export function computeDvrLeftEdge(liveEdgeSec: number): number {
  if (!Number.isFinite(liveEdgeSec) || liveEdgeSec <= 0) return 0
  return Math.max(0, liveEdgeSec - DVR_LOOKBACK_SEC)
}

/**
 * 1x + followLive + !scrub + !refine → windowStart=0，整段压进视口。
 * zoom>1 → visibleSpan=contentEnd/zoom；Live 时窗贴右缘；scrub 时用 pan/frozen。
 */
export function computeTimelineWindow(input: TimelineWindowInput): TimelineWindowResult {
  const contentEnd = Math.max(1, input.contentEnd)
  const zoom = Math.max(1, input.zoomLevel || 1)
  const refining = input.refining

  if (refining && refining.end > refining.start) {
    const mid = (refining.start + refining.end) / 2
    const half = Math.min(TIMELINE_MAX_WINDOW, Math.max(30, (refining.end - refining.start) * 4)) / 2
    const ws = Math.max(0, mid - half)
    const dur = Math.max(contentEnd, ws + half * 2, 1)
    return { windowStart: ws, duration: dur, visibleSpan: dur - ws }
  }

  if (zoom <= 1 && input.followLive && !input.scrubbing) {
    return { windowStart: 0, duration: contentEnd, visibleSpan: contentEnd }
  }

  if (zoom <= 1 && contentEnd <= TIMELINE_MAX_WINDOW) {
    return { windowStart: 0, duration: contentEnd, visibleSpan: contentEnd }
  }

  const visibleSpan =
    zoom > 1
      ? Math.max(30, Math.min(contentEnd, contentEnd / zoom))
      : Math.min(contentEnd, TIMELINE_MAX_WINDOW)

  let ws = 0
  if (input.followLive && !input.scrubbing) {
    ws = Math.max(0, contentEnd - visibleSpan)
  } else if (input.scrubbing && input.frozenWindowStart != null) {
    ws = Math.max(0, Math.min(input.frozenWindowStart, Math.max(0, contentEnd - visibleSpan)))
  } else {
    ws = panTimelineWindowStart(
      Math.max(0, input.playhead),
      contentEnd,
      visibleSpan,
      input.frozenWindowStart ?? input.prevWindowStart,
    )
  }

  return { windowStart: ws, duration: contentEnd, visibleSpan }
}
