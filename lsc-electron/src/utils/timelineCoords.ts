import type { TimelineContext, TimelineProgressSummary, ContinuousAnalysisStatus } from '@/types'

export type TimelineAlignStatus = 'ready' | 'local' | 'invalidated'

export function previewToCommon(ctx: TimelineContext, roomId: string, previewTime: number): number {
  const snap = ctx.room_snapshots[roomId]
  if (!snap) throw new Error(`room ${roomId} not in timeline`)
  return previewTime + snap.preview_to_common_delta
}

export function commonToPreview(ctx: TimelineContext, roomId: string, commonTime: number): number {
  const snap = ctx.room_snapshots[roomId]
  if (!snap) throw new Error(`room ${roomId} not in timeline`)
  return commonTime - snap.preview_to_common_delta
}

export function commonToRecording(ctx: TimelineContext, roomId: string, commonTime: number): number {
  const snap = ctx.room_snapshots[roomId]
  if (!snap) throw new Error(`room ${roomId} not in timeline`)
  return commonTime - snap.recording_to_common_delta
}

export function recordingToCommon(ctx: TimelineContext, roomId: string, recordingTime: number): number {
  const snap = ctx.room_snapshots[roomId]
  if (!snap) throw new Error(`room ${roomId} not in timeline`)
  return recordingTime + snap.recording_to_common_delta
}

export function getAlignStatus(
  ctx: TimelineContext | null,
  invalidated: boolean,
): TimelineAlignStatus {
  if (invalidated) return 'invalidated'
  if (ctx?.timeline_id) return 'ready'
  return 'local'
}

export function pickReferenceRoomId(
  ctx: TimelineContext | null,
  selectedRoomIds: Set<string>,
  fallbackRoomId?: string | null,
): string | null {
  if (ctx?.reference_room_id && selectedRoomIds.has(ctx.reference_room_id)) {
    return ctx.reference_room_id
  }
  for (const rid of selectedRoomIds) {
    if (ctx?.room_snapshots[rid]) return rid
  }
  return fallbackRoomId ?? null
}

/**
 * 长内容滑动窗：仅当 playhead 越出 [prevWs, prevWs+maxWindow] 时平移，
 * 禁止 playhead - 0.15*max 这种持续钉位（会导致拖拽时圆点相对位置不动）。
 */
export function panTimelineWindowStart(
  playhead: number,
  contentEnd: number,
  maxWindow: number,
  prevWs: number,
): number {
  if (contentEnd <= maxWindow) return 0
  const maxWs = Math.max(0, contentEnd - maxWindow)
  let ws = Math.max(0, Math.min(prevWs, maxWs))
  if (playhead < ws) {
    ws = Math.max(0, Math.min(playhead, maxWs))
  } else if (playhead > ws + maxWindow) {
    ws = Math.min(maxWs, Math.max(0, playhead - maxWindow))
  }
  return ws
}

/** recording_review：文件回看，无 DVR 紫标 */
export function isRecordingReviewMode(mode?: string | null): boolean {
  return mode === 'recording_review'
}

/** recording_review / degraded：禁用 followLive 与 dvrStart */
export function isNoDvrPreviewMode(mode?: string | null): boolean {
  return mode === 'recording_review' || mode === 'degraded'
}

/** 录制回看时间线右沿：仅 recording_review 模式使用 */
export function resolveRecordingReviewSpan(
  previewPos: number,
  recordedDurationHint: number,
  fileDuration: number | null | undefined,
  markIn?: number | null,
  markOut?: number | null,
): number {
  let span = Math.max(previewPos, recordedDurationHint, fileDuration ?? 0, 1)
  if (markIn != null && markIn > span) span = markIn
  if (markOut != null && markOut > span) span = markOut
  return span
}

export function computeRecordedDurationHint(
  room: { is_recording?: boolean; record_started_at?: string | null } | null | undefined,
  continuousRecorded?: number,
  nowMs: number = Date.now(),
): number {
  let hint = continuousRecorded ?? 0
  if (room?.is_recording && room.record_started_at) {
    hint = Math.max(hint, (nowMs - new Date(room.record_started_at).getTime()) / 1000)
  }
  return hint
}

/**
 * 组装三轴进度展示摘要（仅用于 UI 展示，不改变三轴换算规则）。
 * 复用 computeRecordedDurationHint + continuousStatus.analyzed_duration。
 */
export function summarizeTimelineProgress(opts: {
  previewPosition: number
  room: { is_recording?: boolean; record_started_at?: string | null } | null | undefined
  continuousRecorded?: number
  continuousStatus: ContinuousAnalysisStatus | null
  axis: TimelineProgressSummary['axis']
  nowMs?: number
}): TimelineProgressSummary {
  const recordedDuration = computeRecordedDurationHint(opts.room, opts.continuousRecorded, opts.nowMs)
  const analysisScannedDuration = opts.continuousStatus?.analyzed_duration ?? 0
  const previewDelay = Math.max(0, recordedDuration - opts.previewPosition)
  return {
    previewPosition: opts.previewPosition,
    recordedDuration,
    analysisScannedDuration,
    previewDelay,
    axis: opts.axis,
  }
}

/**
 * 时间线内容右沿：播放头轴进度 + 切片末端。
 * 无预览 / recording_review / 非 followLive 时可用录制时长撑开，避免长录制卡在冻结预览轴。
 * 有预览且 followLive 时禁止用 recorded_duration（§8.7，防止播放头被钳到 0%）。
 */
export function resolveLiveContentSpan(opts: {
  axisProgress: number
  clipEnds?: Iterable<number>
  recordedHint?: number
  previewEnabled?: boolean
  recordingReview?: boolean
  followLive?: boolean
}): number {
  let span = Math.max(0, Number(opts.axisProgress) || 0)
  if (opts.clipEnds) {
    for (const end of opts.clipEnds) {
      const v = Number(end)
      if (Number.isFinite(v) && v > span) span = v
    }
  }
  const allowRecorded =
    Boolean(opts.recordingReview) ||
    !opts.previewEnabled ||
    opts.followLive === false
  const hint = Number(opts.recordedHint)
  if (allowRecorded && Number.isFinite(hint) && hint > span) {
    span = hint
  }
  return Math.max(span, 1)
}
