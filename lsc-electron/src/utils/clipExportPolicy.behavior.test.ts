import { describe, expect, it } from 'vitest'
import type { ClipSegment } from '@/types'
import {
  canExportClip,
  canExportOrConfirmExport,
  clipExportState,
  clipSnapshotJobId,
  hasAuthoritativeBroadcastEnd,
  isApproximateClip,
  resolveManualRecordingRange,
  summarizeClipExport,
} from './clipExportPolicy'

function clip(overrides: Partial<ClipSegment> = {}): ClipSegment {
  return {
    start: 1,
    end: 5,
    label: 'x',
    room_id: 'r1',
    ...overrides,
  } as ClipSegment
}

describe('hasAuthoritativeBroadcastEnd：出点定稿判据', () => {
  const base = {
    source_profile: 'broadcast',
    broadcast_audit: 'passed',
    end_quality: 'precise' as const,
    end_review_required: false,
    duration_anomaly: false,
    end_by: 'next_prep',
  }

  it('审计 passed + 出点 precise + 无需复核 + 无时长异常 + 可信 end_by 才算定稿', () => {
    expect(hasAuthoritativeBroadcastEnd(clip(base))).toBe(true)
  })

  it('next_combat / open_tail 不能证明切点没落进回放，不算定稿', () => {
    expect(hasAuthoritativeBroadcastEnd(clip({ ...base, end_by: 'next_combat' }))).toBe(false)
    expect(hasAuthoritativeBroadcastEnd(clip({ ...base, end_by: 'open_tail' }))).toBe(false)
    expect(hasAuthoritativeBroadcastEnd(clip({ ...base, end_by: 'broadcast_exclusion' }))).toBe(true)
  })

  it('任一条件不满足即否定', () => {
    expect(hasAuthoritativeBroadcastEnd(clip({ ...base, broadcast_audit: 'failed' }))).toBe(false)
    expect(hasAuthoritativeBroadcastEnd(clip({ ...base, end_quality: 'coarse' }))).toBe(false)
    expect(hasAuthoritativeBroadcastEnd(clip({ ...base, end_review_required: true }))).toBe(false)
    expect(hasAuthoritativeBroadcastEnd(clip({ ...base, duration_anomaly: true }))).toBe(false)
  })
})

describe('canExportClip / canExportOrConfirmExport 同源', () => {
  it('queued / exporting 不可再导', () => {
    expect(canExportClip(clip({ export_status: 'queued' }))).toBe(false)
    expect(canExportClip(clip({ export_status: 'exporting' }))).toBe(false)
  })

  it('手动切片：无 confirm_status 或已确认 / pending 可导', () => {
    expect(canExportClip(clip())).toBe(true)
    expect(canExportClip(clip({ confirm_status: 'user_confirmed' }))).toBe(true)
    expect(canExportClip(clip({ confirm_status: 'ocr_confirmed' }))).toBe(true)
    expect(canExportClip(clip({ confirm_status: 'vision_confirmed' }))).toBe(true)
    expect(canExportClip(clip({ confirm_status: 'pending' }))).toBe(true)
  })

  it('赛事 rejected 终态永不复活', () => {
    const rejected = clip({
      source_profile: 'broadcast',
      confirm_status: 'rejected',
      broadcast_audit: 'passed',
    })
    expect(canExportClip(rejected)).toBe(false)
    expect(canExportClip(clip({
      source_profile: 'broadcast',
      broadcast_audit: 'rejected_replay',
    }))).toBe(false)
  })

  it('出点已定稿的 pending/refining/vision_confirmed 可直接导', () => {
    const authoritative = {
      source_profile: 'broadcast' as const,
      broadcast_audit: 'passed',
      end_quality: 'precise' as const,
      end_by: 'next_prep',
    }
    for (const confirm_status of ['pending', 'refining', 'vision_confirmed'] as const) {
      expect(canExportClip(clip({ ...authoritative, confirm_status }))).toBe(true)
    }
  })

  it('出点未定稿时：vision_confirmed+完整审计可导；带复核标记 / 审计未过则失败关闭', () => {
    const base = {
      source_profile: 'broadcast' as const,
      confirm_status: 'vision_confirmed' as const,
      broadcast_audit: 'passed',
      end_quality: 'coarse' as const,
      end_by: 'open_tail',
    }
    // vision_confirmed + 审计 passed + 无复核标记 ⇒ 可导（出点未定稿只影响「起得略早」）
    expect(canExportClip(clip(base))).toBe(true)
    // 带任一复核标记 ⇒ 失败关闭
    expect(canExportClip(clip({ ...base, broadcast_review_required: true }))).toBe(false)
    expect(canExportClip(clip({ ...base, boundary_review_required: true }))).toBe(false)
    // 审计未过 / 未跑 ⇒ 失败关闭
    expect(canExportClip(clip({ ...base, broadcast_audit: '' }))).toBe(false)
    expect(canExportClip(clip({ ...base, broadcast_audit: 'pending_lookahead' }))).toBe(false)
    // pending 且出点未定稿 ⇒ 失败关闭（须先人工确认）
    expect(canExportClip(clip({
      ...base,
      confirm_status: 'pending',
    }))).toBe(false)
  })

  it('canExportOrConfirmExport 与单条判定一致', () => {
    const c = clip({ export_status: 'queued' })
    expect(canExportOrConfirmExport(c)).toBe(canExportClip(c))
    expect(canExportOrConfirmExport(clip())).toBe(true)
  })
})

describe('clipExportState 列表状态码', () => {
  it('可导 / 待审计 / 需确认 / 已排除 / 不可导', () => {
    expect(clipExportState(clip())).toBe('EXPORTABLE')
    expect(clipExportState(clip({
      source_profile: 'broadcast',
      confirm_status: 'pending',
      broadcast_audit: 'pending_lookahead',
    }))).toBe('PENDING_AUDIT')
    // pending + 出点未定稿 + 审计已跑过 ⇒ 需人工确认
    expect(clipExportState(clip({
      source_profile: 'broadcast',
      confirm_status: 'pending',
      broadcast_audit: 'passed',
      end_quality: 'coarse',
      end_by: 'open_tail',
    }))).toBe('NEEDS_CONFIRM')
    expect(clipExportState(clip({ confirm_status: 'rejected' }))).toBe('REJECTED')
    expect(clipExportState(clip({ duration_anomaly: true }))).toBe('BLOCKED')
    expect(clipExportState(clip({ mark_precision: 'approximate' }))).toBe('BLOCKED')
  })

  it('BLOCKED 先于可导出判定（时长异常确认也导不出）', () => {
    expect(clipExportState(clip({
      duration_anomaly: true,
      confirm_status: 'user_confirmed',
    }))).toBe('BLOCKED')
  })
})

describe('isApproximateClip', () => {
  it('有快照 id 或 precision=exact ⇒ 不近似', () => {
    expect(isApproximateClip(clip({ clip_snapshot_id: 's' }))).toBe(false)
    expect(isApproximateClip(clip({ mark_precision: 'exact' }))).toBe(false)
  })

  it('AI 高光未标 approximate 不判近似（后端定界）', () => {
    expect(isApproximateClip(clip({ is_ai_highlight: true }))).toBe(false)
    expect(isApproximateClip(clip({ is_ai_highlight: true, mark_precision: 'approximate' }))).toBe(true)
  })

  it('precision=approximate 或缺墙钟字段 ⇒ 近似', () => {
    expect(isApproximateClip(clip({ mark_precision: 'approximate' }))).toBe(true)
    expect(isApproximateClip(clip({ mark_in_wallclock: 1, mark_out_wallclock: 2 }))).toBe(false)
    expect(isApproximateClip(clip({ mark_in_wallclock: 1 }))).toBe(true)
  })
})

describe('resolveManualRecordingRange：墙钟 → 录制轴', () => {
  it('扣 content_offset，优先媒体起点', () => {
    expect(resolveManualRecordingRange({
      mark_in_wallclock: 110,
      mark_out_wallclock: 120,
      recording_media_start_mono: 100,
      recording_start_mono: 90,
      content_offset: 5,
    })).toEqual({ start: 5, end: 15 })
  })

  it('缺媒体起点才回退录制起点', () => {
    expect(resolveManualRecordingRange({
      mark_in_wallclock: 110,
      mark_out_wallclock: 120,
      recording_start_mono: 100,
    })).toEqual({ start: 10, end: 20 })
  })

  it('非法输入 / 出点不晚于入点 ⇒ null；早于起点的入点夹到 0', () => {
    expect(resolveManualRecordingRange({})).toBeNull()
    expect(resolveManualRecordingRange({
      mark_in_wallclock: 10, mark_out_wallclock: 10,
      recording_start_mono: 0,
    })).toBeNull()
    expect(resolveManualRecordingRange({
      mark_in_wallclock: -5, mark_out_wallclock: 10,
      recording_start_mono: 0,
    })).toEqual({ start: 0, end: 10 })
  })
})

describe('summarizeClipExport：导出摘要口径', () => {
  it('空列表全 0，listed 为长度', () => {
    expect(summarizeClipExport([])).toEqual({
      pendingConfirm: 0, queued: 0, exporting: 0, completed: 0, failed: 0, listed: 0,
    })
  })

  it('export_status=pending 是待确认，不得计入 queued', () => {
    const s = summarizeClipExport([clip({ export_status: 'pending' })])
    expect(s.pendingConfirm).toBe(1)
    expect(s.queued).toBe(0)
  })

  it('confirm_status pending/refining 也算待确认', () => {
    const s = summarizeClipExport([
      clip({ confirm_status: 'pending' }),
      clip({ confirm_status: 'refining' }),
    ])
    expect(s.pendingConfirm).toBe(2)
    expect(s.queued).toBe(0)
  })

  it('四个导出态各自计数；老字段 exported 无 export_status 时也算完成', () => {
    const s = summarizeClipExport([
      clip({ export_status: 'queued' }),
      clip({ export_status: 'exporting' }),
      clip({ export_status: 'completed' }),
      clip({ export_status: 'failed' }),
      clip({ exported: true }),
    ])
    // completed = export_status:'completed' + 老字段 exported:true 共 2 条
    expect(s).toMatchObject({ queued: 1, exporting: 1, completed: 2, failed: 1, listed: 5 })
  })

  it('export_status 存在时以它为准，覆盖老 exported 字段', () => {
    const s = summarizeClipExport([clip({ exported: true, export_status: 'failed' })])
    expect(s.completed).toBe(0)
    expect(s.failed).toBe(1)
  })
})

describe('clipSnapshotJobId', () => {
  it('取快照 id 前 8 位做 job id', () => {
    expect(clipSnapshotJobId('abcdefghijkl')).toBe('clip-abcdefgh')
  })
})
