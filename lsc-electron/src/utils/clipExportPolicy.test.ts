import { describe, expect, it } from 'vitest'
import { canExportClip, canExportOrConfirmExport } from './clipExportPolicy'
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
})
