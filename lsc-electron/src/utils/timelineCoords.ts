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
