import { describe, expect, it } from 'vitest'
import { computeVisibleRangeIds, toggleInSet, unionWith } from './roomSelection'

/**
 * 范围选择必须以「可见顺序」为准。
 * 回归目标：旧实现拿 store 下标算区间，一旦网格按状态/平台/名称排序，
 * Shift 框选就会选到视觉上不连续的房间。
 */
describe('computeVisibleRangeIds', () => {
  const storeOrder = ['a', 'b', 'c', 'd', 'e']
  // 按状态排序后，用户看到的顺序与 store 顺序完全不同
  const sortedOrder = ['c', 'a', 'e', 'b', 'd']

  it('正向闭区间按可见顺序取值', () => {
    expect(computeVisibleRangeIds(sortedOrder, 'a', 'd')).toEqual(['a', 'e', 'b', 'd'])
  })

  it('反向拖选（从右往左）结果与正向一致', () => {
    expect(computeVisibleRangeIds(sortedOrder, 'd', 'a')).toEqual(['a', 'e', 'b', 'd'])
  })

  it('锚点与目标同为一个房间时只返回它自己', () => {
    expect(computeVisibleRangeIds(sortedOrder, 'b', 'b')).toEqual(['b'])
  })

  it('排序后不再按 store 下标误选', () => {
    // store 下标语义下 a..d 会得到 [a,b,c,d]；可见顺序语义应得到排序后的中间段
    const wrong = computeVisibleRangeIds(storeOrder, 'a', 'd')
    const right = computeVisibleRangeIds(sortedOrder, 'a', 'd')
    expect(wrong).toEqual(['a', 'b', 'c', 'd'])
    expect(right).toEqual(['a', 'e', 'b', 'd'])
  })

  it('无锚点 / 锚点或目标已被移除时返回空数组', () => {
    expect(computeVisibleRangeIds(sortedOrder, null, 'a')).toEqual([])
    expect(computeVisibleRangeIds(sortedOrder, 'zz', 'a')).toEqual([])
    expect(computeVisibleRangeIds(sortedOrder, 'a', 'zz')).toEqual([])
  })
})

describe('多选集合操作', () => {
  it('toggleInSet 不修改原集合', () => {
    const prev = new Set(['a', 'b'])
    const added = toggleInSet(prev, 'c')
    const removed = toggleInSet(prev, 'a')
    expect([...prev]).toEqual(['a', 'b'])
    expect([...added].sort()).toEqual(['a', 'b', 'c'])
    expect([...removed]).toEqual(['b'])
  })

  it('unionWith 只做加法，不会静默丢掉已有选择', () => {
    const prev = new Set(['x'])
    const next = unionWith(prev, ['a', 'b', 'x'])
    expect([...next].sort()).toEqual(['a', 'b', 'x'])
  })
})
