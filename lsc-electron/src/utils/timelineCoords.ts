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
