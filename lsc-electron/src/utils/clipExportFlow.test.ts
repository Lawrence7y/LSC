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

describe('isSameClipEntry', () => {
  it('两边都有 clip_id ⇒ 只认 id，几何相同也不算同一条', () => {
    expect(isSameClipEntry(clip({ clip_id: 'a' }), clip({ clip_id: 'a' }))).toBe(true)
    expect(isSameClipEntry(clip({ clip_id: 'b' }), clip({ clip_id: 'a' }))).toBe(false)
  })

  it('无 clip_id 的老数据退回"同房间 + 同入出点"三元组', () => {
    const target = clip({ clip_id: undefined, room_id: 'r1', start: 10, end: 20 })
    expect(isSameClipEntry(clip({ clip_id: undefined }), target)).toBe(true)
    expect(isSameClipEntry(clip({ clip_id: undefined, room_id: 'r2' }), target)).toBe(false)
    expect(isSameClipEntry(clip({ clip_id: undefined, start: 11 }), target)).toBe(false)
    expect(isSameClipEntry(clip({ clip_id: undefined, end: 21 }), target)).toBe(false)
  })

  it('一方有 id、另一方没有 ⇒ 只能靠几何，不得因两边都取值就误判', () => {
    const target = clip({ clip_id: 'a' })
    expect(isSameClipEntry(clip({ clip_id: undefined }), target)).toBe(true)
    expect(isSameClipEntry(clip({ clip_id: undefined, start: 99, end: 120 }), target)).toBe(false)
  })
})

describe('buildExportRequest', () => {
  it('有快照 id ⇒ 走 export_clip_by_id，且不把区间/墙钟再传给后端（后端按快照定界）', () => {
    const r = buildExportRequest(clip({ clip_snapshot_id: 'snap-abcdefghijkl', is_ai_highlight: true }),
      { preset: 'p1', jobId: 'job-1' })
    expect(r.type).toBe('export_clip_by_id')
    expect(r.payload).toEqual({
      clip_id: 'snap-abcdefghijkl',
      label: '主播A_M01',
      preset_id: 'p1',
      source: 'ai_highlight',
    })
    expect(r.acceptedJobIdFallback).toBe('clip-snap-abc')
  })

  it('手动切片 ⇒ 走 export_clip，冻结墙钟/录制锚点/content_offset，并显式 use_room_marks:false', () => {
    const manual = clip({
      clip_snapshot_id: undefined,
      mark_in_wallclock: 1010,
      mark_out_wallclock: 1020,
      recording_start_mono: 900,
      recording_media_start_mono: 1000,
      content_offset: 1.5,
    })
    const r = buildExportRequest(manual, { preset: 'p1', jobId: 'job-9' })
    expect(r.type).toBe('export_clip')
    expect(r.payload).toMatchObject({
      room_id: 'r1', start: 10, end: 20, job_id: 'job-9', source: 'manual',
      mark_in_wallclock: 1010, mark_out_wallclock: 1020,
      recording_start_mono: 900, recording_media_start_mono: 1000,
      content_offset: 1.5, use_room_marks: false,
    })
    expect(r.acceptedJobIdFallback).toBe('job-9')
  })

  it('缺字段的快照切片不会漏 use_room_marks 到手动路径（两条分支互斥）', () => {
    const r = buildExportRequest(clip({ clip_snapshot_id: 'snap-1' }), { preset: '', jobId: 'j' })
    expect('use_room_marks' in r.payload).toBe(false)
    expect('start' in r.payload).toBe(false)
  })
})

describe('normalizeExportedRange', () => {
  it('两端都是有限数且出点晚于入点才收下', () => {
    expect(normalizeExportedRange(3, 8)).toEqual({ start: 3, end: 8 })
  })

  it('字符串/NaN/Infinity/倒置/缺失一律视为没给', () => {
    expect(normalizeExportedRange('3' as unknown as number, 8)).toBeNull()
    expect(normalizeExportedRange(Number.NaN, 8)).toBeNull()
    expect(normalizeExportedRange(3, Number.POSITIVE_INFINITY)).toBeNull()
    expect(normalizeExportedRange(8, 3)).toBeNull()
    expect(normalizeExportedRange(8, 8)).toBeNull()
    expect(normalizeExportedRange(undefined, undefined)).toBeNull()
  })
})

describe('markClipsQueued / markClipsFailed', () => {
  const a = clip({ clip_id: undefined, start: 10, end: 20 })
  const b = clip({ clip_id: undefined, start: 30, end: 40 })
  const list = [a, b]

  it('批量导出无 clip_id 的多条手动切片时，只能落在那一条上', () => {
    // 旧实现 `c.clip_id === clip.clip_id || 几何` 在这里会让 undefined === undefined 命中所有条目
    const next = markClipsQueued(list, a, { jobId: 'job-a', exportedRange: null })
    expect(next[0]).toMatchObject({ job_id: 'job-a', export_status: 'queued', exported: false })
    expect(next[1]).toBe(b)
  })

  it('后端给了实际区间 ⇒ 同时纠正录制轴秒数；没给 ⇒ 保持原值', () => {
    const withRange = markClipsQueued(list, a, { jobId: 'j', exportedRange: { start: 9.5, end: 19.5 } })
    expect(withRange[0]).toMatchObject({ recording_start_sec: 9.5, recording_end_sec: 19.5 })
    const without = markClipsQueued(list, a, { jobId: 'j', exportedRange: null })
    expect('recording_start_sec' in (without[0] as object)).toBe(false)
  })

  it('入队成功要清掉上一条的失败文案，否则列表继续显示旧错误', () => {
    const failedFirst = [clip({ clip_id: 'x', export_status: 'failed' as const, export_error: '旧错误' })]
    const next = markClipsQueued(failedFirst, failedFirst[0], { jobId: 'j2', exportedRange: null })
    expect(next[0]).toMatchObject({ export_status: 'queued', export_error: undefined })
  })

  it('失败也要落态：export_status=failed + 错误原文，且不动别的条目', () => {
    const next = markClipsFailed(list, b, '后端未接受导出任务')
    expect(next[1]).toMatchObject({ export_status: 'failed', export_error: '后端未接受导出任务', exported: false })
    expect(next[0]).toBe(a)
  })

  it('返回新数组，不就地改（Zustand 依赖引用变化才重渲染）', () => {
    expect(markClipsQueued(list, a, { jobId: 'j', exportedRange: null })).not.toBe(list)
  })
})
