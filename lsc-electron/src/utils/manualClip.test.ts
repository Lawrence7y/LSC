import { describe, expect, it } from 'vitest'
import type { ClipSegment } from '@/types'
import {
  buildManualClip,
  countManualClips,
  distributeManualIndexes,
  type ManualClipSource,
} from './manualClip'

function clip(overrides: Partial<ClipSegment> = {}): ClipSegment {
  return { clip_id: 'c1', room_id: 'r1', source: 'manual', ...overrides } as ClipSegment
}

/** 三样齐备（双墙钟 + 录制单调起点）才算 exact。 */
const fullRoom: ManualClipSource = {
  room_id: 'r1',
  streamer_name: '主播A',
  mark_in: 12,
  mark_out: 45,
  mark_in_wallclock: 1012,
  mark_out_wallclock: 1045,
  recording_media_start_mono: 1000,
  recording_start_mono: 9000,
  content_offset: 2,
}

describe('countManualClips', () => {
  it('只数同房间的手动切片：AI 回合与别房都不算', () => {
    const clips = [
      clip({ room_id: 'r1', source: 'manual' }),
      clip({ room_id: 'r1', source: 'ai_highlight' }),
      clip({ room_id: 'r2', source: 'manual' }),
      clip({ room_id: 'r1', source: 'manual' }),
    ]
    expect(countManualClips(clips, 'r1')).toBe(2)
    expect(countManualClips(clips, 'r2')).toBe(1)
    expect(countManualClips(clips, 'nope')).toBe(0)
  })

  it('空列表为 0（第一片编号因此是 M01）', () => {
    expect(countManualClips([], 'r1')).toBe(0)
  })
})

describe('distributeManualIndexes', () => {
  it('同房间连续追加时逐条 +1，不跳号', () => {
    expect(distributeManualIndexes(
      [{ room_id: 'r1' }, { room_id: 'r1' }, { room_id: 'r1' }],
      () => 2,
    )).toEqual([3, 4, 5])
  })

  it('多房间同时添加：各房序号互不干扰（曾经用全局下标会让第二间跳号）', () => {
    const indexes = distributeManualIndexes(
      [{ room_id: 'rA' }, { room_id: 'rB' }, { room_id: 'rA' }],
      rid => (rid === 'rA' ? 1 : 0),
    )
    expect(indexes).toEqual([2, 1, 3])
  })

  it('空批次返回空数组', () => {
    expect(distributeManualIndexes([], () => 0)).toEqual([])
  })
})

describe('buildManualClip', () => {
  it('快照齐全 ⇒ exact，并原样冻结墙钟与录制锚点（导出不能再读当前 mark）', () => {
    const c = buildManualClip(fullRoom, 3)
    expect(c).not.toBeNull()
    expect(c).toMatchObject({
      start: 12,
      end: 45,
      label: '主播A_M03',
      room_id: 'r1',
      source: 'manual',
      mark_in_wallclock: 1012,
      mark_out_wallclock: 1045,
      recording_media_start_mono: 1000,
      recording_start_mono: 9000,
      content_offset: 2,
      mark_precision: 'exact',
    })
    // 录制轴秒数：媒体起点优先，且扣了 content_offset
    expect(c?.recording_start_sec).toBeCloseTo(10)
    expect(c?.recording_end_sec).toBeCloseTo(43)
  })

  it('缺任一墙钟 ⇒ approximate，录制轴秒数一并置空', () => {
    const c = buildManualClip({ ...fullRoom, mark_out_wallclock: null }, 1)
    expect(c?.mark_precision).toBe('approximate')
    expect(c?.recording_start_sec).toBeNull()
    expect(c?.recording_end_sec).toBeNull()
  })

  it('只有 recording_start_mono（没有媒体起点）也算有锚点，媒体起点优先用于换算', () => {
    const onlyLegacy = buildManualClip({ ...fullRoom, recording_media_start_mono: null }, 1)
    expect(onlyLegacy?.mark_precision).toBe('exact')
    // 精度只看"字段在不在"，而录制轴秒数还要求区间有效：
    // 回退到 recording_start_mono=9000 时 mark 早于该起点，两端都夹到 0 ⇒ 空区间 ⇒ 秒数留空。
    // 这条不对称记在报告 §11.26-C，本批不改语义（改了会动导出取哪条轴）。
    expect(onlyLegacy?.recording_start_sec).toBeNull()
    expect(onlyLegacy?.recording_end_sec).toBeNull()

    const both = buildManualClip(fullRoom, 1)
    expect(both?.recording_start_sec).toBeCloseTo(10)
  })

  it('mark 缺失/非有限值 ⇒ null（调用方提示"请先设置入点和出点"）', () => {
    expect(buildManualClip({ ...fullRoom, mark_in: null }, 1)).toBeNull()
    expect(buildManualClip({ ...fullRoom, mark_out: undefined }, 1)).toBeNull()
    expect(buildManualClip({ ...fullRoom, mark_in: Number.NaN }, 1)).toBeNull()
    expect(buildManualClip({ room_id: 'r1' }, 1)).toBeNull()
  })

  it('入出点顺序不在此校验（导出侧另有区间校验），值原样透传', () => {
    const c = buildManualClip({ ...fullRoom, mark_in: 50, mark_out: 45 }, 1)
    expect(c?.start).toBe(50)
    expect(c?.end).toBe(45)
  })

  it('content_offset 缺失按 0；主播名缺失时用 room_id 兜底命名', () => {
    const c = buildManualClip({ ...fullRoom, content_offset: undefined, streamer_name: '' }, 7)
    expect(c?.content_offset).toBe(0)
    expect(c?.label).toBe('r1_M07')
  })
})
