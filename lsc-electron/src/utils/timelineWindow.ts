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

export type ExpandedPreviewWindowInput = {
  liveDvr: boolean
  previewPos: number
  bufferedStart?: number
  bufferedEnd?: number
  previewDuration?: number
  fileDuration?: number
  markIn?: number | null
  markOut?: number | null
  /** Live 必须忽略；回看也只用 file/preview 秒，不用录制墙钟。 */
  recordedHint?: number
  /** Live 默认 true：播放头钉在右沿。DVR 回看传 false。 */
  followLive?: boolean
}

export type ExpandedPreviewWindow = {
  start: number
  end: number
  purple: number
  liveEdge: number
  hasLiveDvr: boolean
  playheadPct: number
  fillLeftPct: number
  fillWidthPct: number
}

function finiteNonNeg(n: number | null | undefined): number {
  return typeof n === 'number' && Number.isFinite(n) && n > 0 ? n : 0
}

/**
 * 放大预览条窗口：Live 左端 = 紫线 = liveEdge − 120s。
 * liveEdge 优先 buffered.end，无效时用 previewPos；禁止录制墙钟。
 */
export function computeExpandedPreviewWindow(input: ExpandedPreviewWindowInput): ExpandedPreviewWindow {
  const pos = typeof input.previewPos === 'number' && Number.isFinite(input.previewPos)
    ? Math.max(0, input.previewPos)
    : 0

  if (!input.liveDvr) {
    const end = Math.max(
      pos,
      finiteNonNeg(input.previewDuration),
      finiteNonNeg(input.fileDuration),
      finiteNonNeg(input.markIn),
      finiteNonNeg(input.markOut),
      1,
    )
    const playheadPct = Math.max(0, Math.min(100, (pos / end) * 100))
    return {
      start: 0,
      end,
      purple: 0,
      liveEdge: end,
      hasLiveDvr: false,
      playheadPct,
      fillLeftPct: 0,
      fillWidthPct: playheadPct,
    }
  }

  const bufStart = input.bufferedStart
  const bufEnd = input.bufferedEnd
  const hasBuffer =
    typeof bufStart === 'number' && Number.isFinite(bufStart)
    && typeof bufEnd === 'number' && Number.isFinite(bufEnd)
    && bufEnd - bufStart > 1
  const liveEdge = hasBuffer ? Math.max(0, bufEnd as number) : pos
  const purple = computeDvrLeftEdge(liveEdge)
  const start = purple
  const end = Math.max(liveEdge, start)
  const span = Math.max(end - start, 1e-6)
  const followLive = input.followLive !== false
  const playheadPct = followLive
    ? 100
    : Math.max(0, Math.min(100, ((pos - start) / span) * 100))
  return {
    start,
    end,
    purple,
    liveEdge,
    hasLiveDvr: true,
    playheadPct,
    fillLeftPct: 0,
    fillWidthPct: playheadPct,
  }
}

/**
 * 1x + followLive + !scrub + !refine → windowStart=0，整段压进视口。
 * zoom>1 → visibleSpan=contentEnd/zoom；Live 时窗贴右缘；scrub 时用 pan/frozen。
 */
export function computeTimelineWindow(input: TimelineWindowInput): TimelineWindowResult {
  const contentEnd = Math.max(1, input.contentEnd)
  const zoom = Math.max(1, input.zoomLevel || 1)
  const refining = input.refining

  // 1x（未放大）始终从 00:00 开始，整段压进视口，不做局部滑动窗。
  // 精修/拖拽/跟播都不改变 1x 的左端点，保证“最左边恒定零点”。
  if (zoom <= 1) {
    return { windowStart: 0, duration: contentEnd, visibleSpan: contentEnd }
  }

  if (refining && refining.end > refining.start) {
    const mid = (refining.start + refining.end) / 2
    const half = Math.min(TIMELINE_MAX_WINDOW, Math.max(30, (refining.end - refining.start) * 4)) / 2
    const ws = Math.max(0, mid - half)
    const dur = Math.max(contentEnd, ws + half * 2, 1)
    return { windowStart: ws, duration: dur, visibleSpan: dur - ws }
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
