import { describe, expect, it } from 'vitest'
import {
  canExportClip,
  canExportOrConfirmExport,
  clipExportState,
  clipSnapshotJobId,
  isApproximateClip,
  resolveManualRecordingRange,
  summarizeClipExport,
} from './clipExportPolicy'
import type { ClipSegment } from '@/types'

function clip(overrides: Partial<ClipSegment> = {}): ClipSegment {
  return {
    clip_id: 'c1',
    title: 't',
    start_sec: 0,
    end_sec: 1,
    duration_sec: 1,
    exported: false,
    ...overrides,
  } as unknown as ClipSegment
}

describe('clipExportPolicy.canExportClip', () => {
  it.each(['user_confirmed', 'ocr_confirmed', 'vision_confirmed', 'pending'])(
    '允许 confirm_status=%s 直接导出',
    (status) => {
      expect(canExportClip(clip({ confirm_status: status as ClipSegment['confirm_status'] }))).toBe(true)
    },
  )

  it('无 confirm_status 的手动切片可直接导出', () => {
    expect(canExportClip(clip({ confirm_status: undefined }))).toBe(true)
  })

  it.each(['audio_pending', 'refining'])('拒绝 confirm_status=%s', (status) => {
    expect(canExportClip(clip({ confirm_status: status as ClipSegment['confirm_status'] }))).toBe(false)
  })

  it.each(['queued', 'exporting'])('排队/导出中一律不可导出（含 pending）', (status) => {
    expect(
      canExportClip(clip({ confirm_status: 'pending', export_status: status as ClipSegment['export_status'] })),
    ).toBe(false)
  })

  it('canExportOrConfirmExport 与单条判定同源', () => {
    expect(canExportOrConfirmExport(clip({ confirm_status: 'audio_pending' }))).toBe(false)
    expect(canExportOrConfirmExport(clip({ confirm_status: 'pending' }))).toBe(true)
  })

  it('官方赛事 provisional 边界禁止在后台继续改写时直接导出', () => {
    expect(canExportClip(clip({
      source_profile: 'broadcast',
      confirm_status: 'pending',
      broadcast_audit: 'pending_lookahead',
    }))).toBe(false)
    expect(canExportClip(clip({
      source_profile: 'broadcast',
      confirm_status: 'vision_confirmed',
      broadcast_audit: 'passed',
      broadcast_review_required: true,
    }))).toBe(false)
  })

  it('视觉审计已定稿出点的赛事切片无需人工确认即可导出（真实运行样本）', () => {
    // 2026-09-11 18:04 持续分析实跑 sidecar：出点 precise（broadcast_exclusion），
    // 入点仍是 coarse 的 OCR 战斗锚点 → 聚合标记 broadcast_review_required=true。
    const endAuthoritative: Partial<ClipSegment> = {
      source_profile: 'broadcast',
      confirm_status: 'vision_confirmed',
      broadcast_audit: 'passed',
      broadcast_audit_reason: 'broadcast_replay_or_non_game',
      end_by: 'broadcast_exclusion',
      start_by: 'ocr_combat',
      start_quality: 'coarse',
      end_quality: 'precise',
      start_review_required: true,
      end_review_required: false,
    }
    expect(canExportClip(clip({
      ...endAuthoritative,
      boundary_quality: 'precise',
      boundary_review_required: false,
      broadcast_review_required: true,
    }))).toBe(true)
    // 入点 coarse 把聚合边界质量降级到 coarse 的样本同样可导：
    // 出点定稿后后台只可能再改入点，导出文件最多「起得略早」。
    expect(canExportClip(clip({
      ...endAuthoritative,
      boundary_quality: 'coarse',
      boundary_review_required: true,
      broadcast_review_required: true,
    }))).toBe(true)
    // 用户点开切片进入精修（begin_refine_clip 广播 refining）：出点已定稿时
    // 不该再拦——导出用的就是这条已入列的边界（预览弹窗显示的入出点）。
    expect(canExportClip(clip({
      ...endAuthoritative,
      confirm_status: 'refining',
      boundary_quality: 'precise',
      broadcast_review_required: true,
    }))).toBe(true)
    // 未定稿出点即使处于精修态也仍要人工确认
    expect(canExportClip(clip({
      ...endAuthoritative,
      confirm_status: 'refining',
      broadcast_audit: 'pending_lookahead',
      end_by: 'next_prep',
      end_quality: 'coarse',
    }))).toBe(false)
  })

  it('出点未定稿的赛事切片仍需人工确认', () => {
    const base: Partial<ClipSegment> = {
      source_profile: 'broadcast',
      broadcast_audit: 'passed',
      start_by: 'ocr_combat',
      start_quality: 'coarse',
    }
    // 未取得后视窗口：出点证据不足
    expect(canExportClip(clip({
      ...base,
      confirm_status: 'pending',
      broadcast_audit: 'pending_no_exclusion',
      broadcast_review_required: true,
      end_by: 'open_tail',
      end_quality: 'coarse',
    }))).toBe(false)
    // next_combat 不能证明切点没落在回放/暂停里
    expect(canExportClip(clip({
      ...base,
      confirm_status: 'vision_confirmed',
      broadcast_review_required: true,
      end_by: 'next_combat',
      end_quality: 'coarse',
    }))).toBe(false)
    // 出点精确但仍标注需复核（例如审计异常放行）不得绕过
    expect(canExportClip(clip({
      ...base,
      confirm_status: 'vision_confirmed',
      broadcast_review_required: true,
      end_by: 'broadcast_exclusion',
      end_quality: 'precise',
      end_review_required: true,
    }))).toBe(false)
    // 时长异常的切片即使出点精确也不自动放行
    expect(canExportClip(clip({
      ...base,
      confirm_status: 'vision_confirmed',
      broadcast_review_required: true,
      end_by: 'broadcast_exclusion',
      end_quality: 'precise',
      duration_anomaly: true,
    }))).toBe(false)
  })

  it('被审计拒绝的赛事切片人工确认也不复活', () => {
    expect(canExportClip(clip({
      source_profile: 'broadcast',
      confirm_status: 'user_confirmed',
      broadcast_audit: 'rejected_replay',
      end_by: 'broadcast_exclusion',
      end_quality: 'precise',
      end_review_required: false,
    }))).toBe(false)
  })

  it('官方赛事审计完整或经用户确认后可以导出', () => {
    expect(canExportClip(clip({
      source_profile: 'broadcast',
      confirm_status: 'vision_confirmed',
      broadcast_audit: 'passed',
      broadcast_review_required: false,
      boundary_review_required: false,
    }))).toBe(true)
    expect(canExportClip(clip({
      source_profile: 'broadcast',
      confirm_status: 'user_confirmed',
      broadcast_audit: 'pending_lookahead',
    }))).toBe(true)
  })
})

describe('clipExportPolicy.clipExportState（列表逐条标注）', () => {
  it('已定稿的赛事切片 → EXPORTABLE', () => {
    expect(clipExportState(clip({
      source_profile: 'broadcast',
      confirm_status: 'pending',
      broadcast_audit: 'passed',
      end_by: 'broadcast_exclusion',
      end_quality: 'precise',
      end_review_required: false,
    }))).toBe('EXPORTABLE')
  })

  it('审计未跑到的（pending_lookahead）→ PENDING_AUDIT，提示会自动变可导出', () => {
    expect(clipExportState(clip({
      source_profile: 'broadcast',
      confirm_status: 'pending',
      broadcast_audit: 'pending_lookahead',
    }))).toBe('PENDING_AUDIT')
  })

  it('审计跑完但出点未定稿 / 无排除证据 → NEEDS_CONFIRM', () => {
    // 真实形状（09:01 现场 047/063）：审计未跑完 + 需复核
    expect(clipExportState(clip({
      source_profile: 'broadcast',
      confirm_status: 'pending',
      broadcast_audit: 'passed',
      end_by: 'next_prep',
      end_quality: 'coarse',
      end_review_required: true,
      broadcast_review_required: true,
    }))).toBe('NEEDS_CONFIRM')
    expect(clipExportState(clip({
      source_profile: 'broadcast',
      confirm_status: 'pending',
      broadcast_audit: 'pending_no_exclusion',
      broadcast_review_required: true,
    }))).toBe('NEEDS_CONFIRM')
    // 出点仍是 next_combat（不能证明切点不在回放里）
    expect(clipExportState(clip({
      source_profile: 'broadcast',
      confirm_status: 'vision_confirmed',
      broadcast_audit: 'passed',
      end_by: 'next_combat',
      end_quality: 'coarse',
      broadcast_review_required: true,
    }))).toBe('NEEDS_CONFIRM')
  })

  it('被拒终态 → REJECTED（确认也不复活）', () => {
    expect(clipExportState(clip({
      source_profile: 'broadcast',
      confirm_status: 'pending',
      broadcast_audit: 'rejected_no_stable_combat_start',
    }))).toBe('REJECTED')
    expect(clipExportState(clip({
      source_profile: 'broadcast',
      confirm_status: 'rejected',
      broadcast_audit: 'passed',
    }))).toBe('REJECTED')
  })

  it('时长异常 / 近似定位 → BLOCKED（确认也导不出）', () => {
    expect(clipExportState(clip({
      source_profile: 'broadcast',
      confirm_status: 'vision_confirmed',
      broadcast_audit: 'passed',
      duration_anomaly: true,
    }))).toBe('BLOCKED')
  })
})

// ────────────────── isApproximateClip（从 Workbench 搬来，§11.24）──────────────────
// 批量导出/确认导出前用它筛掉"定位不准"的切片并弹提示。
// 历史上它只有一条文子守卫（切函数体文本找 `is_ai_highlight`），
// 换成行为用例后才能钉住"AI 高光为什么不算近似"这条业务判据。
describe('isApproximateClip', () => {
  it('有快照 id 或 precision 明确为 exact ⇒ 一律不近似（快照代表已定界）', () => {
    expect(isApproximateClip(clip({ clip_snapshot_id: 'snap-1' }))).toBe(false)
    expect(isApproximateClip(clip({ mark_precision: 'exact' }))).toBe(false)
    // 快照/exact 优先于"墙钟字段缺失"
    expect(isApproximateClip(clip({
      mark_precision: 'exact', mark_in_wallclock: null, mark_out_wallclock: null,
    }))).toBe(false)
  })

  it('AI 高光未被标为 approximate 时不判近似（后端定界，不走预览拖拽误差）', () => {
    expect(isApproximateClip(clip({ is_ai_highlight: true }))).toBe(false)
    expect(isApproximateClip(clip({
      is_ai_highlight: true, mark_precision: 'approximate',
    }))).toBe(true)
  })

  it('precision 为 approximate 直接判近似', () => {
    expect(isApproximateClip(clip({
      mark_precision: 'approximate', mark_in_wallclock: 1, mark_out_wallclock: 2,
    }))).toBe(true)
  })

  it('precision 缺失时看墙钟字段：缺任一个即近似，齐了才不近似', () => {
    expect(isApproximateClip(clip({ mark_in_wallclock: 1010, mark_out_wallclock: null }))).toBe(true)
    expect(isApproximateClip(clip({ mark_in_wallclock: null, mark_out_wallclock: 1020 }))).toBe(true)
    expect(isApproximateClip(clip({ mark_in_wallclock: 1010, mark_out_wallclock: 1020 }))).toBe(false)
  })
})

describe('resolveManualRecordingRange', () => {
  const room = {
    mark_in_wallclock: 1010, mark_out_wallclock: 1020,
    recording_media_start_mono: 1000,
  }

  it('把墙钟 mark 折成录制轴秒数，并扣 content_offset', () => {
    expect(resolveManualRecordingRange(room)).toEqual({ start: 10, end: 20 })
    expect(resolveManualRecordingRange({ ...room, content_offset: 1.5 }))
      .toEqual({ start: 8.5, end: 18.5 })
  })

  it('优先用媒体起点，缺失才回退录制起点；两者都在时不混用', () => {
    expect(resolveManualRecordingRange({
      ...room, recording_media_start_mono: 1000, recording_start_mono: 900,
    })).toEqual({ start: 10, end: 20 })
    expect(resolveRecordingRangeFallbackShape()).toEqual({ start: 110, end: 120 })
  })

  it('非法/缺失输入一律 null，不返回 NaN 区间', () => {
    expect(resolveManualRecordingRange({ ...room, mark_in_wallclock: undefined })).toBeNull()
    expect(resolveManualRecordingRange({ ...room, mark_in_wallclock: Number.NaN })).toBeNull()
    expect(resolveManualRecordingRange({ ...room, mark_in_wallclock: '1010' as unknown as number })).toBeNull()
    expect(resolveManualRecordingRange({ ...room, mark_out_wallclock: '1020' as unknown as number })).toBeNull()
    expect(resolveManualRecordingRange({ ...room, recording_media_start_mono: null })).toBeNull()
    expect(resolveManualRecordingRange({
      ...room, recording_media_start_mono: null, recording_start_mono: null,
    })).toBeNull()
    // content_offset 非法只让自己归零，不影响整条换算
    expect(resolveManualRecordingRange({ ...room, content_offset: Number.NaN }))
      .toEqual({ start: 10, end: 20 })
  })

  it('出点不晚于入点 ⇒ null；早于录制起点的入点被夹到 0', () => {
    expect(resolveManualRecordingRange({ ...room, mark_out_wallclock: 1010 })).toBeNull()
    expect(resolveManualRecordingRange({ ...room, mark_in_wallclock: 990, mark_out_wallclock: 1020 }))
      .toEqual({ start: 0, end: 20 })
  })
})

function resolveRecordingRangeFallbackShape() {
  return resolveManualRecordingRange({
    mark_in_wallclock: 1010, mark_out_wallclock: 1020,
    recording_media_start_mono: null, recording_start_mono: 900,
  })
}

describe('clipSnapshotJobId', () => {
  it('取快照 id 前 8 位做 job id（与后端 clip_snapshot_id 对得上，便于日志关联）', () => {
    expect(clipSnapshotJobId('abcdefgh-1234')).toBe('clip-abcdefgh')
    expect(clipSnapshotJobId('short')).toBe('clip-short')
  })
})

// ────────────────── summarizeClipExport（从 Workbench 的 useMemo 搬来，§11.25）──────────────────
// 底部状态条的"待确认 / 已入队 / 导出中 / 完成 / 失败"计数。
// 历史上只有守卫切文本检查 `summary.queued` 之类存在，最关键的
// "pending 是待确认、不是导出入队"只能靠人读代码。
describe('summarizeClipExport', () => {
  it('空列表：全 0，listed 也为 0', () => {
    expect(summarizeClipExport([])).toEqual({
      pendingConfirm: 0, queued: 0, exporting: 0, completed: 0, failed: 0, listed: 0,
    })
  })

  it('待确认不计入导出队列（一个都没导出时不能显示"已在队列"）', () => {
    expect(summarizeClipExport([
      clip({ confirm_status: 'pending' }),
      clip({ confirm_status: 'refining' }),
      clip({ export_status: 'pending' }),
    ])).toMatchObject({ pendingConfirm: 3, queued: 0, completed: 0 })
  })

  it('四个导出态各自计数', () => {
    const s = summarizeClipExport([
      clip({ export_status: 'queued' }),
      clip({ export_status: 'queued' }),
      clip({ export_status: 'exporting' }),
      clip({ export_status: 'completed' }),
      clip({ export_status: 'failed' }),
    ])
    expect(s).toMatchObject({ queued: 2, exporting: 1, completed: 1, failed: 1, pendingConfirm: 0 })
  })

  it('老字段 exported（无 export_status）算完成；export_status 存在时以它为准', () => {
    expect(summarizeClipExport([clip({ exported: true })])).toMatchObject({ completed: 1 })
    expect(summarizeClipExport([clip({ exported: true, export_status: 'failed' })]))
      .toMatchObject({ completed: 0, failed: 1 })
  })

  it('待确认与队列进度不互斥：同一条两边各计一次', () => {
    expect(summarizeClipExport([clip({ confirm_status: 'pending', export_status: 'queued' })]))
      .toMatchObject({ pendingConfirm: 1, queued: 1 })
  })

  it('listed 恒为列表长度，包含已导出（新旧两种字段）与失败的条目', () => {
    const s = summarizeClipExport([
      clip({ export_status: 'completed' }),
      clip({ exported: true }),          // 老字段：无 export_status
      clip({ export_status: 'failed' }),
      clip({}),
    ])
    expect(s.listed).toBe(4)
    expect(s.completed).toBe(2)
  })
})
