/**
 * 时间线视图纯计算（可在 Worker 中运行）。
 * 从 Workbench useMemo 抽出，避免巨型组件内联重逻辑。
 *
 * 分层：buildClipBlocks（稳定）→ content/window（高频）→ assemble view。
 */
import type { ClipSegment, TimelineContext, TimelineHighlightBand } from '@/types'
import type { TimelineViewModel } from '@/pages/Workbench/components/ControlBar'
import {
  computeRecordedDurationHint,
  isRecordingReviewMode,
  previewToCommon,
  resolveLiveContentSpan,
  resolveRecordingReviewSpan,
} from '@/utils/timelineCoords'
import { computeTimelineWindow, TIMELINE_MAX_WINDOW } from '@/utils/timelineWindow'

export { TIMELINE_MAX_WINDOW }

export type TimelineClipBlock = { start: number; end: number }

export type TimelineViewInput = {
  commonMode: boolean
  timelineContext: TimelineContext | null
  referenceRoomId: string | null
  rooms: Array<{
    room_id: string
    preview_enabled?: boolean
    preview_mode?: string
    mark_in?: number | null
    mark_out?: number | null
    record_started_at?: string | null
    is_recording?: boolean
  }>
  previewPositions: Record<string, number>
  commonMarkIn: number | null
  commonMarkOut: number | null
  clips: ClipSegment[]
  timelineHighlights: TimelineHighlightBand[]
  refiningClipId: string | null
  waveformPeaks: number[]
  timelineFollowLive: boolean
  timelineScrubbing: boolean
  frozenWindowStart: number | null
  recordedDurationHint?: number
  /** 媒体 duration 探测（可选） */
  mediaDuration?: number
  prevContentEnd: number
  prevWindowStart: number
  contentEdgeRoomId: string | null
  /** 预计算的 clip 块（跳过 O(n) 坐标转换） */
  clipBlocks?: TimelineClipBlock[]
  /** 缩放倍率；1x Live 时 windowStart 固定 0 */
  zoomLevel?: number
}

export type TimelineViewResult = {
  view: TimelineViewModel | null
  contentEnd: number
  windowStart: number
  contentEdgeRoomId: string | null
}

/** 第一层：仅 clips / timelineContext 变化时重算 */
export function buildClipBlocks(
  clips: ClipSegment[],
  timelineContext: TimelineContext,
): TimelineClipBlock[] {
  return clips
    .filter(c => !c.is_ai_highlight && c.end > c.start)
    .map(c => {
      let start = c.common_start ?? c.start
      let end = c.common_end ?? c.end
      if (c.room_id && c.common_start == null) {
        try {
          start = previewToCommon(timelineContext, c.room_id, c.start)
          end = previewToCommon(timelineContext, c.room_id, c.end)
        } catch {
          return null
        }
      }
      return { start, end }
    })
    .filter((c): c is TimelineClipBlock => c != null)
}

export function computeTimelineViewModel(input: TimelineViewInput): TimelineViewResult {
  const {
    commonMode,
    timelineContext,
    referenceRoomId,
    rooms,
    previewPositions,
    commonMarkIn,
    commonMarkOut,
    clips,
    timelineHighlights,
    refiningClipId,
    waveformPeaks,
    timelineFollowLive,
    timelineScrubbing,
    frozenWindowStart,
    recordedDurationHint,
    mediaDuration,
    prevContentEnd,
    prevWindowStart,
    contentEdgeRoomId,
  } = input

  if (!commonMode || !timelineContext || !referenceRoomId) {
    return {
      view: null,
      contentEnd: prevContentEnd,
      windowStart: prevWindowStart,
      contentEdgeRoomId,
    }
  }

  let nextContentEnd = prevContentEnd
  let nextEdgeRoom = contentEdgeRoomId
  if (nextEdgeRoom !== referenceRoomId) {
    nextEdgeRoom = referenceRoomId
    nextContentEnd = 1
  }

  const refRoom = rooms.find(r => r.room_id === referenceRoomId)
  const previewT = previewPositions[referenceRoomId] ?? 0
  const curCommon = previewToCommon(timelineContext, referenceRoomId, previewT)
  const isRecordingReview = isRecordingReviewMode(refRoom?.preview_mode)
  let axisProgress = Math.max(commonMarkOut ?? 0, commonMarkIn ?? 0, curCommon)
  if (isRecordingReview) {
    const recordedHint = computeRecordedDurationHint(refRoom, recordedDurationHint)
    const reviewSpan = resolveRecordingReviewSpan(
      previewT,
      recordedHint,
      mediaDuration,
      commonMarkIn,
      commonMarkOut,
    )
    axisProgress = Math.max(axisProgress, reviewSpan)
  }
  const refineClip = refiningClipId
    ? clips.find(c => c.round_key === refiningClipId || c.clip_id === refiningClipId)
    : null
  let refineStart = commonMarkIn
  let refineEnd = commonMarkOut
  if ((refineStart == null || refineEnd == null) && refineClip) {
    refineStart = refineClip.common_start ?? refineClip.start
    refineEnd = refineClip.common_end ?? refineClip.end
  }
  if (refineEnd != null && refineEnd > axisProgress) axisProgress = refineEnd
  if (refineStart != null && refineStart > axisProgress) axisProgress = refineStart

  const clipBlocks = input.clipBlocks ?? buildClipBlocks(clips, timelineContext)
  const clipEnds: number[] = []
  for (const c of clips) {
    clipEnds.push(c.common_end ?? c.end)
  }
  const recordedHint = computeRecordedDurationHint(refRoom, recordedDurationHint)
  const elapsed = resolveLiveContentSpan({
    axisProgress,
    clipEnds,
    recordedHint,
    previewEnabled: Boolean(refRoom?.preview_enabled),
    recordingReview: isRecordingReview,
    followLive: timelineFollowLive,
  })
  const rawEnd = Math.max(elapsed, curCommon, 0)
  const contentEnd = Math.max(nextContentEnd, rawEnd, 1)

  const zoomLevel = input.zoomLevel ?? 1
  const refining =
    refineStart != null && refineEnd != null && refineEnd > refineStart
      ? { start: refineStart, end: refineEnd }
      : null
  const win = computeTimelineWindow({
    contentEnd,
    zoomLevel,
    followLive: timelineFollowLive,
    scrubbing: timelineScrubbing,
    frozenWindowStart,
    playhead: Math.max(0, curCommon),
    prevWindowStart,
    refining,
  })
  const ws = win.windowStart
  const dur = win.duration

  const markIn = commonMarkIn ?? (refRoom?.mark_in != null
    ? previewToCommon(timelineContext, referenceRoomId, refRoom.mark_in)
    : null)
  const markOut = commonMarkOut ?? (refRoom?.mark_out != null
    ? previewToCommon(timelineContext, referenceRoomId, refRoom.mark_out)
    : null)

  const liveCur = (timelineFollowLive && !timelineScrubbing)
    ? contentEnd
    : Math.max(0, curCommon)

  return {
    view: {
      duration: dur,
      currentTime: liveCur,
      windowStart: ws,
      markIn,
      markOut,
      clips: clipBlocks,
      highlights: timelineHighlights,
      waveformPeaks,
      contentEnd,
    },
    contentEnd,
    windowStart: ws,
    contentEdgeRoomId: nextEdgeRoom,
  }
}
