import { describe, expect, it } from 'vitest'
import type { ClipSegment } from '@/types'
import {
  applyConfirmedClipUpdate,
  resolveConfirmBounds,
  selectSyncTargetRooms,
} from './clipConfirmPolicy'

function clip(overrides: Partial<ClipSegment> = {}): ClipSegment {
  return {
    clip_id: 'c1',
    room_id: 'r1',
    start: 10,
    end: 20,
    label: 'x',
    source: 'manual',
    ...overrides,
  } as unknown as ClipSegment
}

describe('resolveConfirmBounds 的来源优先级', () => {
  it('非精修 ⇒ 只用切片自身边界（批量确认不得串用时间轴残留 mark）', () => {
    expect(resolveConfirmBounds({
      clipStart: 10, clipEnd: 20, refining: false,
      commonMarks: { start: 100, end: 200 },
      roomMarks: { start: 300, end: 400 },
      dragMark: { type: 'in', time: 500 },
    })).toEqual({ start: 10, end: 20 })
  })

  it('精修：公共轴 mark 优先于房间 mark', () => {
    expect(resolveConfirmBounds({
      clipStart: 10, clipEnd: 20, refining: true,
      commonMarks: { start: 100, end: 200 }, roomMarks: { start: 300, end: 400 },
    })).toEqual({ start: 100, end: 200 })
  })

  it('精修：公共轴不可用时才用房间 mark，都没有才回退切片边界', () => {
    expect(resolveConfirmBounds({
      clipStart: 10, clipEnd: 20, refining: true, roomMarks: { start: 300, end: 400 },
    })).toEqual({ start: 300, end: 400 })
    expect(resolveConfirmBounds({
      clipStart: 10, clipEnd: 20, refining: true, commonMarks: null, roomMarks: null,
    })).toEqual({ start: 10, end: 20 })
  })

  it('拖动游标覆盖在 mark 之上，且只覆盖它那一端', () => {
    const base = { clipStart: 10, clipEnd: 20, refining: true, commonMarks: { start: 100, end: 200 } }
    expect(resolveConfirmBounds({ ...base, dragMark: { type: 'in', time: 50 } }))
      .toEqual({ start: 50, end: 200 })
    expect(resolveConfirmBounds({ ...base, dragMark: { type: 'out', time: 250 } }))
      .toEqual({ start: 100, end: 250 })
  })

  it('出点不晚于入点 ⇒ null（相等也是无效）', () => {
    expect(resolveConfirmBounds({ clipStart: 20, clipEnd: 20, refining: false })).toBeNull()
    expect(resolveConfirmBounds({ clipStart: 30, clipEnd: 20, refining: false })).toBeNull()
    expect(resolveConfirmBounds({
      clipStart: 10, clipEnd: 20, refining: true, dragMark: { type: 'out', time: 5 },
    })).toBeNull()
  })
})

describe('selectSyncTargetRooms 的作用域', () => {
  const rooms = [
    { room_id: 'r1', is_connected: true, align_group_id: 'gA' },
    { room_id: 'r2', is_connected: true, align_group_id: 'gA' },
    { room_id: 'r3', is_connected: true, align_group_id: 'gB' },
    { room_id: 'r4', is_connected: false, align_group_id: 'gA' },
  ]

  it('本房有分组时，冻结目标集里跨组/未连接/不存在的房间都被剔掉', () => {
    expect(selectSyncTargetRooms({
      clipRoomId: 'r1', alignGroupId: 'gA', analysisTargetRoomIds: ['r2', 'r3', 'r4', 'ghost'], rooms,
    })).toEqual(['r2'])
  })

  it('本房无分组时，冻结目标集不受分组限制（只要求已连接且非本房）', () => {
    expect(selectSyncTargetRooms({
      clipRoomId: 'r1', analysisTargetRoomIds: ['r2', 'r3', 'r4', 'ghost'], rooms,
    })).toEqual(['r2', 'r3'])
  })

  it('目标集全不可用时回落到同对齐组（而不是回落到空、也不是回落到全量已连接房）', () => {
    expect(selectSyncTargetRooms({
      clipRoomId: 'r1', alignGroupId: 'gA', analysisTargetRoomIds: ['r4'], rooms,
    })).toEqual(['r2'])
    // 冻结目标属于别的组 ⇒ 视同"本次没有可用目标"，仍在同组内同步
    expect(selectSyncTargetRooms({
      clipRoomId: 'r1', alignGroupId: 'gA', analysisTargetRoomIds: ['r3'], rooms,
    })).toEqual(['r2'])
  })

  it('没有分析目标但有分组 ⇒ 只取同组已连接房间', () => {
    expect(selectSyncTargetRooms({ clipRoomId: 'r1', alignGroupId: 'gA', analysisTargetRoomIds: [], rooms }))
      .toEqual(['r2'])
  })

  it('既无分析目标又无分组 ⇒ 空；绝不退回"所有已连接房间"', () => {
    expect(selectSyncTargetRooms({ clipRoomId: 'r1', analysisTargetRoomIds: [], rooms }))
      .toEqual([])
  })

  it('同步范围不得越组：本房在 gB 且组内无其它已连接房间 ⇒ 空', () => {
    expect(selectSyncTargetRooms({
      clipRoomId: 'r3', alignGroupId: 'gB', analysisTargetRoomIds: [], rooms,
    })).toEqual([])
  })
})

describe('applyConfirmedClipUpdate 的乐观更新', () => {
  it('只改同房间里代表同一条切片的条目：同 round_key 的别房不动', () => {
    const target = clip({ clip_id: 'cA', room_id: 'r1', round_key: 'k1', start: 10, end: 20 })
    const otherRoom = clip({ clip_id: 'cB', room_id: 'r2', round_key: 'k1', start: 10, end: 20 })
    const next = applyConfirmedClipUpdate([target, otherRoom], target, { start: 12, end: 18 })
    expect(next[0]).toMatchObject({ start: 12, end: 18, confirm_status: 'user_confirmed' })
    expect(next[1]).toBe(otherRoom)
  })

  it('key 命中本条的 round_key 或 clip_id 都算同一条', () => {
    const target = clip({ clip_id: 'cA', round_key: 'k1', room_id: 'r1' })
    const byClipId = clip({ clip_id: 'k1', room_id: 'r1', start: 1, end: 2 })
    const next = applyConfirmedClipUpdate([byClipId], target, { start: 5, end: 6 })
    expect(next[0]).toMatchObject({ start: 5, end: 6 })
  })

  it('两边都没有 clip_id 时靠几何匹配，不得因 undefined===undefined 全命中', () => {
    const a = clip({ clip_id: undefined, room_id: 'r1', start: 10, end: 20, label: 'A' })
    const b = clip({ clip_id: undefined, room_id: 'r1', start: 30, end: 40, label: 'B' })
    const next = applyConfirmedClipUpdate([a, b], a, { start: 11, end: 19 })
    expect(next[0].label).toBe('A')
    expect(next[1]).toBe(b)
  })

  it('AI 高光同时纠正录制轴秒数；手动切片不写录制轴字段', () => {
    const ai = clip({ clip_id: 'cA', room_id: 'r1', is_ai_highlight: true })
    expect(applyConfirmedClipUpdate([ai], ai, { start: 3, end: 9 })[0])
      .toMatchObject({ recording_start_sec: 3, recording_end_sec: 9 })
    const manual = clip({ clip_id: 'cB', room_id: 'r1' })
    const updated = applyConfirmedClipUpdate([manual], manual, { start: 3, end: 9 })[0]
    expect(updated.confirm_status).toBe('user_confirmed')
    expect(updated.recording_start_sec ?? null).toBe(null)
  })

  it('返回新数组，不就地改', () => {
    const target = clip()
    const list = [target]
    expect(applyConfirmedClipUpdate(list, target, { start: 1, end: 2 })).not.toBe(list)
  })
})
