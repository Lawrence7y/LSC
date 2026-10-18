import type { ClipSegment } from '@/types'

/**
 * 赛事切片可信出点类型（与后端 `room_handler._BROADCAST_VALID_END_BY` 同源）。
 * `next_combat` / `open_tail` 只说明后面还有内容，不能证明切点没落在回放/暂停里。
 */
const BROADCAST_VALID_END_BY = new Set(['next_prep', 'broadcast_exclusion'])

/**
 * 出点是否已由赛事视觉审计定稿。
 *
 * 与后端 `lsc/exporter/jianying_draft.py::_broadcast_gate_passed` 的
 * `end_is_authoritative` 同一判据：审计 passed + 出点 precise + 无需复核 +
 * 无时长异常 + 出点类型可信。出点定稿意味着切片不会伸进赛后回放/下一回合准备；
 * 此时入点即使仍是 coarse 的 OCR 战斗锚点（`broadcast_review_required` /
 * `boundary_review_required` 为 true）也只是「起得略早」，不该再拦人工确认。
 */
export function hasAuthoritativeBroadcastEnd(clip: ClipSegment): boolean {
  return clip.broadcast_audit === 'passed'
    && clip.end_quality === 'precise'
    && clip.end_review_required !== true
    && clip.duration_anomaly !== true
    && BROADCAST_VALID_END_BY.has(String(clip.end_by ?? ''))
}

/**
 * 切片可导出性的唯一判定入口（列表页按钮、批量导出、Ctrl+E 快捷键共用）。
 *
 * 历史：Workbench 快捷键路径曾内联一份判定并以 `!== 'refining'` 收尾，
 * 使前三个确认分支恒短路 —— audio_pending 切片可走 Ctrl+E 绕过导出门禁，
 * 而守卫测试只查文本被顺带绕过。现在禁止任何调用方内联 confirm_status 判定。
 */
export function canExportClip(clip: ClipSegment): boolean {
  if (clip.export_status === 'queued' || clip.export_status === 'exporting') return false
  if (clip.source_profile === 'broadcast') {
    // 后端会广播 typed 之外的 rejected 终态（被赛事审计拒绝的候选），故按字符串比较。
    const status = String(clip.confirm_status ?? '')
    const audit = String(clip.broadcast_audit ?? '').toLowerCase()
    // 拒绝/纯回放候选永不导出，人工确认也不复活（与后端门禁同款）。
    if (status === 'rejected' || audit.startsWith('rejected')) return false
    if (status === 'user_confirmed') return true
    // 视觉审计已定稿出点：直接可导，不再要求逐条人工确认。
    // 官方赛事的 pending/coarse 是会被后台审计继续改写的 provisional 版本，
    // 但出点定稿后改的只会是入点，导出文件最多「起得略早」，不会切掉内容。
    // refining 是「用户点了这条切片进入精修」的会话态（begin_refine_clip 广播），
    // 不代表边界不可信：出点同样已定稿，且导出用的就是该条已入列的边界
    // （预览弹窗显示的入出点即最终写入文件的范围）。
    if (
      (status === 'pending' || status === 'refining' || status === 'vision_confirmed')
      && hasAuthoritativeBroadcastEnd(clip)
    ) {
      return true
    }
    // 出点未定稿时仍失败关闭：必须完整审计 + 无任何复核标记。
    if (status !== 'vision_confirmed') return false
    if (audit !== 'passed') return false
    if (clip.broadcast_review_required === true) return false
    if (clip.boundary_review_required === true) return false
    return true
  }
  // 切片页取消待确认状态：已确认、OCR/视觉确认及已识别 pending 切片均可直接导出
  return !clip.confirm_status ||
    clip.confirm_status === 'user_confirmed' ||
    clip.confirm_status === 'ocr_confirmed' ||
    clip.confirm_status === 'vision_confirmed' ||
    clip.confirm_status === 'pending'
}

/** 批量或单条可导出判定：与单条同源（保留历史调用点名） */
export function canExportOrConfirmExport(clip: ClipSegment, _hasConfirmAndExport?: boolean): boolean {
  return canExportClip(clip)
}

/**
 * 列表里逐条展示的导出状态（用户不该等到导出才发现"少了 5 条"）。
 *
 * 取值与后端 `jianying_handlers._skip_reason_code` 的词汇表对齐：
 * REJECTED / PENDING_AUDIT(≈NEVER_AUDITED) / NEEDS_CONFIRM(≈END_NOT_FINAL|NO_EXCLUSION_EVIDENCE)
 * / BLOCKED / 以及本就可导的 EXPORTABLE。
 */
export type ClipExportStateCode =
  | 'EXPORTABLE'
  | 'PENDING_AUDIT'
  | 'NEEDS_CONFIRM'
  | 'REJECTED'
  | 'BLOCKED'

export function clipExportState(clip: ClipSegment): ClipExportStateCode {
  const status = String(clip.confirm_status ?? '')
  const audit = String(clip.broadcast_audit ?? '').toLowerCase()
  // 拒绝是终态：人工确认也不复活（与后端门禁同款）
  if (status === 'rejected' || audit.startsWith('rejected')) return 'REJECTED'
  // 时长异常/近似定位：确认也导不出，必须换一条候选（先于可导出判定）
  if (clip.duration_anomaly === true || clip.mark_precision === 'approximate') return 'BLOCKED'
  if (canExportClip(clip)) return 'EXPORTABLE'
  if (clip.source_profile === 'broadcast') {
    if (!audit || audit === 'pending_lookahead') return 'PENDING_AUDIT'
  }
  return 'NEEDS_CONFIRM'
}

/** 状态 → i18n 文案 key（列表 chip 与 tooltip 共用） */
export const CLIP_EXPORT_STATE_LABEL: Record<ClipExportStateCode, string> = {
  EXPORTABLE: '可导出',
  PENDING_AUDIT: '待审计',
  NEEDS_CONFIRM: '需确认',
  REJECTED: '已排除',
  BLOCKED: '不可导出',
}

export const CLIP_EXPORT_STATE_HINT: Record<ClipExportStateCode, string> = {
  EXPORTABLE: '出点已由赛事审计定稿，可直接导出',
  PENDING_AUDIT: '后台视觉审计还没跑到这条；审完会自动变成可导出，不必手动确认',
  NEEDS_CONFIRM: '出点未定稿（无回放/暂停排除证据或仍是下一回合准备）。核对边界后点「确认」即可导出',
  REJECTED: '赛事审计判定不是有效回合，已被排除（人工确认也不会复活）',
  BLOCKED: '时长异常或坐标无效，无法导出',
}

export function isApproximateClip(c: ClipSegment): boolean {
  if (c.clip_snapshot_id || c.mark_precision === 'exact') return false
  if (c.is_ai_highlight && c.mark_precision !== 'approximate') return false
  return (
    c.mark_precision === 'approximate' ||
    (c.mark_precision !== 'exact' &&
      (c.mark_in_wallclock == null || c.mark_out_wallclock == null))
  )
}

export function resolveManualRecordingRange(room: {
  mark_in_wallclock?: number | null
  mark_out_wallclock?: number | null
  recording_media_start_mono?: number | null
  recording_start_mono?: number | null
  content_offset?: number
}): { start: number; end: number } | null {
  const markIn = room.mark_in_wallclock
  const markOut = room.mark_out_wallclock
  const recordingStart = room.recording_media_start_mono ?? room.recording_start_mono
  if (
    typeof markIn !== 'number' || !Number.isFinite(markIn)
    || typeof markOut !== 'number' || !Number.isFinite(markOut)
    || typeof recordingStart !== 'number' || !Number.isFinite(recordingStart)
  ) {
    return null
  }
  const offset = typeof room.content_offset === 'number' && Number.isFinite(room.content_offset)
    ? room.content_offset
    : 0
  const start = Math.max(0, markIn - recordingStart - offset)
  const end = Math.max(0, markOut - recordingStart - offset)
  return end > start ? { start, end } : null
}

export function clipSnapshotJobId(clipId: string): string {
  return `clip-${clipId.slice(0, 8)}`
}

/** 底部状态条用的导出摘要（原来在 Workbench 的 useMemo 里内联，2026-09-19 搬来）。 */
export interface ClipExportSummary {
  /** 切片列表中待确认的条数（不是导出入队） */
  pendingConfirm: number
  /** 已进入导出队列 */
  queued: number
  exporting: number
  completed: number
  failed: number
  /** 切片列表条数（入列） */
  listed: number
}

/**
 * 聚合切片列表成导出摘要。判据里最容易写错的一条：**`export_status === 'pending'`
 * 是"待确认"，不能计入 `queued`（导出入队）** —— 一旦计入，用户会在一个都没导出的
 * 时候看到"已在队列"。`exported` 是老字段（无 `export_status` 时才算完成）。
 */
export function summarizeClipExport(clips: readonly ClipSegment[]): ClipExportSummary {
  const summary: ClipExportSummary = {
    pendingConfirm: 0,
    queued: 0,
    exporting: 0,
    completed: 0,
    failed: 0,
    listed: clips.length,
  }
  for (const clip of clips) {
    const needsTune = clip.confirm_status === 'pending' || clip.confirm_status === 'refining'
    if (needsTune || clip.export_status === 'pending') {
      summary.pendingConfirm += 1
    }
    const status = clip.export_status ?? (clip.exported ? 'completed' : undefined)
    // pending = 待确认，不算导出入队
    if (status === 'queued' || status === 'exporting' || status === 'completed' || status === 'failed') {
      summary[status] += 1
    }
  }
  return summary
}
