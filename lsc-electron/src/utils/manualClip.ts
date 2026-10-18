import type { ClipSegment } from '@/types'
import { formatManualClipLabel } from './clipNaming'
import { resolveManualRecordingRange } from './clipExportPolicy'

/**
 * 手动切片的构造口径（2026-09-19 从 `Workbench` 的 `handleAddClip` 闭包里搬出来）。
 *
 * 搬的理由有两层：
 * 1. 这段"入点/出点 → 切片对象"的判据此前只有文本守卫（切 `useCallback` 函数体找子串），
 *    而它决定导出用哪条时间轴，属于会切错内容的判据；
 * 2. 精度口径 `exact` 要求"双墙钟 + 录制单调起点"三者齐备，缺一即 `approximate`，
 *    而 `approximate` 的切片在批量导出时会被拦（见 `clipExportPolicy.isApproximateClip`）——
 *    两头必须是同一套判据，不能各写一份。
 */

/** 只取构造需要的字段：避免 util 依赖整个 store 的房间类型。 */
export interface ManualClipSource {
  room_id: string
  streamer_name?: string
  mark_in?: number | null
  mark_out?: number | null
  mark_in_wallclock?: number | null
  mark_out_wallclock?: number | null
  recording_start_mono?: number | null
  recording_media_start_mono?: number | null
  content_offset?: number
}

/** 该房已有的手动切片条数 —— 切片名序号按 room_id 维度计，不是全局 `clips.length`。 */
export function countManualClips(
  clips: readonly Pick<ClipSegment, 'room_id' | 'source'>[],
  roomId: string,
): number {
  return clips.filter(c => c.room_id === roomId && c.source === 'manual').length
}

/**
 * 给一批待添加的切片分配各自的序号（同房间连续追加时逐条 +1）。
 *
 * 批量添加（公共轴快照）会为多个房间各产一条，序号必须**按房间**累加：
 * 早期实现用全局下标 `i` 累加，两间房同时添加时第二间会拿到 base+2 而跳号。
 */
export function distributeManualIndexes(
  items: readonly { room_id: string }[],
  baseCountOf: (roomId: string) => number,
): number[] {
  const seen = new Map<string, number>()
  return items.map(item => {
    const already = seen.get(item.room_id) ?? 0
    seen.set(item.room_id, already + 1)
    return baseCountOf(item.room_id) + already + 1
  })
}

/**
 * 由房间的 mark 构造一条手动切片。
 * mark 入/出任一非有限值时返回 `null`（调用方负责提示"请先设置入点和出点"）。
 */
export function buildManualClip(
  room: ManualClipSource,
  manualIndex: number,
): ClipSegment | null {
  const markIn = room.mark_in
  const markOut = room.mark_out
  if (
    typeof markIn !== 'number' || !Number.isFinite(markIn)
    || typeof markOut !== 'number' || !Number.isFinite(markOut)
  ) {
    return null
  }
  const actualRange = resolveManualRecordingRange(room)
  const hasWallclockPair = room.mark_in_wallclock != null && room.mark_out_wallclock != null
  const hasRecordingAnchor =
    (room.recording_media_start_mono ?? room.recording_start_mono) != null
  return {
    start: markIn,
    end: markOut,
    label: formatManualClipLabel(room.streamer_name || room.room_id, manualIndex),
    room_id: room.room_id,
    source: 'manual',
    mark_in_wallclock: room.mark_in_wallclock ?? null,
    mark_out_wallclock: room.mark_out_wallclock ?? null,
    recording_start_mono: room.recording_start_mono ?? null,
    recording_media_start_mono: room.recording_media_start_mono ?? null,
    recording_start_sec: actualRange?.start ?? null,
    recording_end_sec: actualRange?.end ?? null,
    content_offset: room.content_offset ?? 0,
    mark_precision: hasWallclockPair && hasRecordingAnchor ? 'exact' : 'approximate',
  } as ClipSegment
}
