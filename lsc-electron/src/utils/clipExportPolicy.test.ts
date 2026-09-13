import { describe, expect, it } from 'vitest'
import {
  canExportClip,
  canExportOrConfirmExport,
  clipExportState,
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
