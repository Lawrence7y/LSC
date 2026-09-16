/**
 * 时间线可视窗口纯计算（主轨 1x / zoom 局部窗 + DVR 左边界）。
 * 与 CLAUDE.md §8.7 一致：只用 preview/common 轴秒，禁止录制墙钟推窗。
 */
import { panTimelineWindowStart } from '@/utils/timelineCoords'
import {
  DEFAULT_TIMELINE_REPLAY_SECONDS,
  normalizeReplayBufferSeconds,
} from '@/utils/replaySettings'

/** 长内容默认滑窗上限（秒）；1x Live 时不再使用，整段从 0 压缩。 */
export const TIMELINE_MAX_WINDOW = 600

/** 紫线 / 预览条左端：liveEdge − 此时长（秒）。 */
export const DVR_LOOKBACK_SEC = DEFAULT_TIMELINE_REPLAY_SECONDS // DVR_LOOKBACK_SEC = 120 (fallback constant)

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

/**
 * 缓冲边界容差（秒）。
 *
 * seek 目标落在可回放范围之外、但相差不超过该容差时按“范围之内”处理：分片边界与
 * 浮点误差经常让“刚好点在左沿/右沿”算出零点几到一两秒的越界，旧实现会因此把一次
 * 普通点击推进重量级的本地文件回看通道（表现为预览区一直显示「正在准备回看…」）。
 */
export const DVR_BUFFER_EDGE_TOLERANCE_SEC = 2

/** 落点与可回放范围边界之间保留的安全边距（秒）：贴着边界 seek 容易被判出缓冲。 */
export const DVR_SEEK_EDGE_MARGIN_SEC = 0.3

/** 目标是否落在可立即回放的缓冲范围内（含边界容差）。 */
export function isWithinSeekRange(
  target: number,
  rangeStart: number,
  rangeEnd: number,
  toleranceSec: number = DVR_BUFFER_EDGE_TOLERANCE_SEC,
): boolean {
  if (!Number.isFinite(target) || !Number.isFinite(rangeStart) || !Number.isFinite(rangeEnd)) {
    return false
  }
  return target >= rangeStart - toleranceSec && target <= rangeEnd + toleranceSec
}

/**
 * 把 seek 落点收进 [rangeStart + margin, rangeEnd − margin]。
 *
 * 「能点的范围」必须等于「真能回放的范围」：调用方拿到任何落点都应先过这一层，
 * 否则左沿/右沿那点误差就会触发文件回看重通道或让播放头落在缓冲外。
 */
export function clampSeekToRange(target: number, rangeStart: number, rangeEnd: number): number {
  const lo = rangeStart + DVR_SEEK_EDGE_MARGIN_SEC
  const hi = Math.max(lo, rangeEnd - DVR_SEEK_EDGE_MARGIN_SEC)
  return Math.min(hi, Math.max(lo, target))
}

/** 预览/common 轴上的 DVR 左边界（紫线）。 */
export function computeDvrLeftEdge(
  liveEdgeSec: number,
  replaySeconds: number = DVR_LOOKBACK_SEC,
): number {
  if (!Number.isFinite(liveEdgeSec) || liveEdgeSec <= 0) return 0
  const lookback = normalizeReplayBufferSeconds(replaySeconds)
  return Math.max(0, liveEdgeSec - lookback)
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
  /** 用户设置的直播 DVR 时长；0 表示关闭历史回放。 */
  replaySeconds?: number
  /** Live 默认 true：播放头钉在右沿。DVR 回看传 false。 */
  followLive?: boolean
}

export type ExpandedPreviewWindow = {
  /** 可立即回放范围左端：真实 MSE 连续缓冲起点（用户设置只作为上限）。 */
  start: number
  /** 可立即回放范围右端：直播沿（真实缓冲末端；无缓冲时退化为播放位置）。 */
  end: number
  /** 左界标记位置（= start），供预览条画“从此处起可回放”的标记。 */
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
 * 放大预览条窗口（**能点的范围 = 真能回放的范围**）：
 *   · 左端 start = max(真实连续缓冲起点 buf.start, liveEdge − 用户设置时长)，
 *     用户设置只作为上限，绝不把未缓冲的历史画成可点区域；
 *   · 右端 end = liveEdge（真实缓冲末端），无缓冲时退化为播放位置。
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
  const replaySeconds = normalizeReplayBufferSeconds(input.replaySeconds)
  // 关闭 DVR 时仍保留当前直播沿作为唯一可回放位置，避免 UI 暗示存在历史缓存。
  if (replaySeconds === 0) {
    return {
      start: liveEdge,
      end: liveEdge,
      purple: liveEdge,
      liveEdge,
      hasLiveDvr: false,
      playheadPct: 100,
      fillLeftPct: 0,
      fillWidthPct: 100,
    }
  }
  // 用户设置只是**上限**：缓冲比设置浅（预览刚起、配额缩容、播放头落后被 trim）
  // 时，左端必须回到真实连续缓冲起点 buf.start —— 能点/能拖的范围就是能立即回放
  // 的范围。旧实现把「设置窗口」整段画成可点区域，点在缓冲左侧会触发重量级的
  // 本地文件回看通道（预览区显示「正在准备回看…」），实测一次点击白等数秒。
  const settingStart = computeDvrLeftEdge(liveEdge, replaySeconds)
  const start = hasBuffer ? Math.max(bufStart as number, settingStart) : settingStart
  const purple = start
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
