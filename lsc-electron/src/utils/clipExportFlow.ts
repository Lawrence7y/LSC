import type { ClipSegment } from '@/types'
import { clipSnapshotJobId } from './clipExportPolicy'

/**
 * 批量导出的"发请求 + 乐观更新"口径（2026-09-19 从 `Workbench.handleExportMany` 搬出）。
 *
 * 搬的理由：这一段里有两处必须永远一致的判据，历史上是**手抄两份**
 * （成功分支与失败分支各写一遍"这条切片是哪一条"），
 * 一旦漂移，失败态就可能写不进正确的列表项 —— 用户看到的就是"导出失败但列表还在导出中"。
 * 现在两份共用同一个 `isSameClipEntry`，且 payload 形状可以逐字段断言。
 */

/** 列表项身份：优先 clip_id；老数据没有 clip_id 时退回"同房间 + 同入出点"三元组。 */
export function isSameClipEntry(candidate: ClipSegment, target: ClipSegment): boolean {
  if (candidate.clip_id && target.clip_id) return candidate.clip_id === target.clip_id
  return (
    candidate.start === target.start
    && candidate.end === target.end
    && candidate.room_id === target.room_id
  )
}

export interface ExportRequestSpec {
  type: 'export_clip_by_id' | 'export_clip'
  payload: Record<string, unknown>
  /** 后端没回 job_id 时用来认领任务的 id（快照切片用快照 id，与后端同源）。 */
  acceptedJobIdFallback: string
}

/**
 * 有 `clip_snapshot_id` 的切片走 `export_clip_by_id`（后端按快照定界，前端不再传区间）；
 * 其余走 `export_clip` 并**冻结入列时的墙钟/录制锚点/`content_offset`**，
 * 且必须 `use_room_marks: false` —— 否则后端会拿房间"当前"mark 覆盖这条切片的区间。
 */
export function buildExportRequest(
  clip: ClipSegment,
  opts: { preset: string; jobId: string },
): ExportRequestSpec {
  const source = clip.is_ai_highlight ? 'ai_highlight' : 'manual'
  if (clip.clip_snapshot_id) {
    return {
      type: 'export_clip_by_id',
      payload: {
        clip_id: clip.clip_snapshot_id,
        label: clip.label,
        preset_id: opts.preset,
        source,
      },
      acceptedJobIdFallback: clipSnapshotJobId(clip.clip_snapshot_id),
    }
  }
  return {
    type: 'export_clip',
    payload: {
      room_id: clip.room_id,
      start: clip.start,
      end: clip.end,
      label: clip.label,
      preset_id: opts.preset,
      job_id: opts.jobId,
      source,
      mark_in_wallclock: clip.mark_in_wallclock,
      mark_out_wallclock: clip.mark_out_wallclock,
      recording_start_mono: clip.recording_start_mono,
      recording_media_start_mono: clip.recording_media_start_mono,
      content_offset: clip.content_offset,
      use_room_marks: false,
    },
    acceptedJobIdFallback: opts.jobId,
  }
}

/** 后端回的实际导出区间：两端都得是有限数且出点晚于入点，否则视为没给。 */
export function normalizeExportedRange(
  start: unknown,
  end: unknown,
): { start: number; end: number } | null {
  const from = Number(start)
  const to = Number(end)
  if (typeof start !== 'number' || typeof end !== 'number') return null
  if (!Number.isFinite(from) || !Number.isFinite(to) || to <= from) return null
  return { start: from, end: to }
}

/** 入队成功：写 job_id 与 queued 态；后端给了实际区间就同时纠正录制轴秒数。 */
export function markClipsQueued(
  clips: readonly ClipSegment[],
  target: ClipSegment,
  info: { jobId: string; exportedRange: { start: number; end: number } | null },
): ClipSegment[] {
  return clips.map(c => isSameClipEntry(c, target)
    ? {
      ...c,
      ...(info.exportedRange
        ? {
          recording_start_sec: info.exportedRange.start,
          recording_end_sec: info.exportedRange.end,
        }
        : {}),
      job_id: info.jobId,
      exported: false,
      export_status: 'queued' as const,
      export_error: undefined,
    }
    : c)
}

/** 入队失败：同样要落态，否则列表会一直停在"导出中"。 */
export function markClipsFailed(
  clips: readonly ClipSegment[],
  target: ClipSegment,
  errorText: string,
): ClipSegment[] {
  return clips.map(c =>
    isSameClipEntry(c, target)
      ? { ...c, exported: false, export_status: 'failed' as const, export_error: errorText }
      : c,
  )
}
