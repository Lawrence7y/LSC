import { describe, expect, it } from 'vitest'
import type { ClipSegment } from '@/types'
import {
  buildExportRequest,
  isSameClipEntry,
  markClipsFailed,
  markClipsQueued,
  normalizeExportedRange,
} from './clipExportFlow'

function clip(overrides: Partial<ClipSegment> = {}): ClipSegment {
  return {
    clip_id: 'c1',
    room_id: 'r1',
    start: 10,
    end: 20,
    label: '主播A_M01',
    source: 'manual',
    ...overrides,
  } as unknown as ClipSegment
}

describe('buildExportRequest：导出 payload 冻结入列时边界', () => {
  it('有快照 id ⇒ export_clip_by_id，不再传区间（后端按快照定界）', () => {
    const r = buildExportRequest(
      clip({ clip_snapshot_id: 'snap-abcdefghijkl', is_ai_highlight: true }),
      { preset: 'p1', jobId: 'job-1' },
    )
    expect(r.type).toBe('export_clip_by_id')
    expect(r.payload).toEqual({
      clip_id: 'snap-abcdefghijkl',
      label: '主播A_M01',
      preset_id: 'p1',
      source: 'ai_highlight',
    })
    expect(r.acceptedJobIdFallback).toBe('clip-snap-abc')
  })

  it('手动切片冻结墙钟 / 录制锚点 / content_offset，且 use_room_marks:false', () => {
    const r = buildExportRequest(clip({
      clip_snapshot_id: undefined,
      mark_in_wallclock: 1010,
      mark_out_wallclock: 1020,
      recording_start_mono: 900,
      recording_media_start_mono: 1000,
      content_offset: 1.5,
    }), { preset: 'p1', jobId: 'job-9' })
    expect(r.type).toBe('export_clip')
    expect(r.payload).toMatchObject({
      room_id: 'r1',
      start: 10,
      end: 20,
      job_id: 'job-9',
      source: 'manual',
      mark_in_wallclock: 1010,
      mark_out_wallclock: 1020,
      recording_start_mono: 900,
      recording_media_start_mono: 1000,
      content_offset: 1.5,
      use_room_marks: false,
    })
    expect(r.acceptedJobIdFallback).toBe('job-9')
  })

  it('两条分支互斥：快照路径不得漏出 use_room_marks', () => {
    const r = buildExportRequest(clip({ clip_snapshot_id: 'snap-1' }), { preset: '', jobId: 'j' })
    expect('use_room_marks' in r.payload).toBe(false)
  })
})

describe('isSameClipEntry：列表项身份', () => {
  it('两边都有 clip_id 时只认 id', () => {
    expect(isSameClipEntry(clip({ clip_id: 'a' }), clip({ clip_id: 'a' }))).toBe(true)
    expect(isSameClipEntry(clip({ clip_id: 'b' }), clip({ clip_id: 'a' }))).toBe(false)
  })

  it('无 clip_id 退回 同房间+同入出点；不得 undefined===undefined 全命中', () => {
    const target = clip({ clip_id: undefined, room_id: 'r1', start: 10, end: 20 })
    expect(isSameClipEntry(clip({ clip_id: undefined }), target)).toBe(true)
    expect(isSameClipEntry(clip({ clip_id: undefined, start: 11 }), target)).toBe(false)
  })
})

describe('markClipsQueued / markClipsFailed', () => {
  it('入队成功写 job_id 与 queued；后端给了实际区间就纠正录制轴秒数', () => {
    const next = markClipsQueued([clip()], clip(), {
      jobId: 'j1',
      exportedRange: { start: 8, end: 22 },
    })
    expect(next[0]).toMatchObject({
      job_id: 'j1',
      export_status: 'queued',
      exported: false,
      recording_start_sec: 8,
      recording_end_sec: 22,
      export_error: undefined,
    })
  })

  it('无实际区间时不改录制轴字段', () => {
    const next = markClipsQueued([clip({ recording_start_sec: 1 })], clip(), {
      jobId: 'j1',
      exportedRange: null,
    })
    expect(next[0].recording_start_sec).toBe(1)
  })

  it('入队失败必须落 failed + export_error，否则列表永远停在导出中', () => {
    const next = markClipsFailed([clip({ export_status: 'exporting' })], clip(), 'boom')
    expect(next[0]).toMatchObject({
      export_status: 'failed',
      export_error: 'boom',
      exported: false,
    })
  })
})

describe('normalizeExportedRange', () => {
  it('两端有限且出点晚于入点才有效', () => {
    expect(normalizeExportedRange(1, 5)).toEqual({ start: 1, end: 5 })
    expect(normalizeExportedRange(5, 1)).toBeNull()
    expect(normalizeExportedRange(1, 1)).toBeNull()
    expect(normalizeExportedRange('1', 5)).toBeNull()
    expect(normalizeExportedRange(Number.NaN, 5)).toBeNull()
    expect(normalizeExportedRange(0, Number.POSITIVE_INFINITY)).toBeNull()
  })
})
