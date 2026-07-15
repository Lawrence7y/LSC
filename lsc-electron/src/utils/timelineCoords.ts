import type { TimelineContext } from '@/types'

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
