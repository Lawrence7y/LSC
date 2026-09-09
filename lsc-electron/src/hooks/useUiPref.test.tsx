import { describe, expect, it, beforeEach, vi } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useUiPref, readUiPref } from './useUiPref'
import { useMarkUndo, type MarkSnapshot } from './useMarkUndo'
import { useUndoStack } from './useUndoStack'

beforeEach(() => {
  try {
    localStorage.clear()
    localStorage.setItem('lsc.locale', 'zh-CN')
  } catch { /* ignore */ }
})

describe('useUiPref', () => {
  it('无存储值时用 fallback，并在写入后可被下次读取复用', () => {
    const a = renderHook(() => useUiPref('zoom', 1))
    expect(a.result.current[0]).toBe(1)

    act(() => a.result.current[1](3))
    expect(a.result.current[0]).toBe(3)
    expect(localStorage.getItem('lsc.ui.zoom')).toBe('3')

    // 新的 hook 实例（等价于重启应用）应从 localStorage 恢复
    const b = renderHook(() => useUiPref('zoom', 1))
    expect(b.result.current[0]).toBe(3)
  })

  it('支持函数式更新（折叠面板这类 toggle 写法）', () => {
    const { result } = renderHook(() => useUiPref('collapsed', false))
    act(() => result.current[1](prev => !prev))
    expect(result.current[0]).toBe(true)
    act(() => result.current[1](prev => !prev))
    expect(result.current[0]).toBe(false)
  })

  it('已有存储值时不再调用 fallback（惰性求值避免每渲染读盘）', () => {
    // RTL 默认包 StrictMode，useState 初始化函数会被双调，
    // 因此只能断言「完全不调」，不能断言恰好调 1 次。
    localStorage.setItem('lsc.ui.zoom', '2.5')
    const fallback = vi.fn(() => 1)
    const { result, rerender } = renderHook(() => useUiPref('zoom', fallback))
    expect(result.current[0]).toBe(2.5)
    expect(fallback).not.toHaveBeenCalled()

    rerender()
    expect(fallback).not.toHaveBeenCalled()
  })

  it('无存储值时惰性 fallback 生效，重渲染不重复读盘', () => {
    const fallback = vi.fn(() => 1)
    const { result, rerender } = renderHook(() => useUiPref('fresh', fallback))
    expect(result.current[0]).toBe(1)
    const callsAfterMount = fallback.mock.calls.length
    expect(callsAfterMount).toBeGreaterThan(0)

    rerender()
    rerender()
    expect(fallback.mock.calls.length).toBe(callsAfterMount)
  })

  it('存储值损坏时回落默认值而不是抛错', () => {
    localStorage.setItem('lsc.ui.broken', '{not json')
    const { result } = renderHook(() => useUiPref('broken', 7))
    expect(result.current[0]).toBe(7)
  })

  it('readUiPref 与 useUiPref 共用命名空间', () => {
    const { result } = renderHook(() => useUiPref('rate', 1.5))
    act(() => result.current[1](2))
    expect(readUiPref('rate', 1)).toBe(2)
  })
})

function snapshotOf(
  commonIn: number | null,
  commonOut: number | null,
  rooms: MarkSnapshot['rooms'],
): MarkSnapshot {
  return { commonIn, commonOut, rooms }
}

describe('useMarkUndo', () => {
  it('按改动前状态记录，撤销时原样回滚', () => {
    let current = snapshotOf(10, 20, [{ roomId: 'r1', markIn: 1, markOut: 2 }])
    const applied: MarkSnapshot[] = []
    const { result } = renderHook(() => useMarkUndo({
      getSnapshot: () => current,
      applySnapshot: s => { applied.push(s); current = s },
    }))

    result.current.record('标记入点')
    current = snapshotOf(15, 20, [{ roomId: 'r1', markIn: 5, markOut: 2 }])

    expect(result.current.undoLast()).toBe(true)
    expect(applied).toHaveLength(1)
    expect(applied[0].commonIn).toBe(10)
    expect(applied[0].rooms[0].markIn).toBe(1)
  })

  it('同一 coalesceKey 的连续记录只保留第一步（一次拖拽算一步）', () => {
    let current = snapshotOf(10, 20, [])
    const { result } = renderHook(() => useMarkUndo({
      getSnapshot: () => current,
      applySnapshot: s => { current = s },
    }))

    result.current.record('拖动入点', 'drag:in')
    result.current.record('拖动入点', 'drag:in')
    result.current.record('拖动入点', 'drag:in')
    current = snapshotOf(99, 20, [])

    expect(result.current.undoLast()).toBe(true)
    expect(current.commonIn).toBe(10)
    // 三步合并成一步后再撤销一次即空栈
    expect(result.current.undoLast()).toBe(false)
  })

  it('endCoalesce 之后的同 key 记录算新的一步', () => {
    let current = snapshotOf(10, 20, [])
    const { result } = renderHook(() => useMarkUndo({
      getSnapshot: () => current,
      applySnapshot: s => { current = s },
    }))

    result.current.record('拖动入点', 'drag:in')
    result.current.endCoalesce()
    current = snapshotOf(30, 20, [])
    result.current.record('拖动入点', 'drag:in')
    result.current.endCoalesce()
    current = snapshotOf(50, 20, [])

    result.current.undoLast()
    expect(current.commonIn).toBe(30)
    result.current.undoLast()
    expect(current.commonIn).toBe(10)
  })

  it('栈空时 undoLast 返回 false（调用方据此决定要不要提示）', () => {
    const { result } = renderHook(() => useMarkUndo({
      getSnapshot: () => snapshotOf(null, null, []),
      applySnapshot: () => {},
    }))
    expect(result.current.undoLast()).toBe(false)
  })
})

describe('useUndoStack.undoLast', () => {
  it('从最近一条开始撤销并出栈', () => {
    const order: string[] = []
    const { result } = renderHook(() => useUndoStack(5))
    result.current.push('a', () => order.push('a'))
    result.current.push('b', () => order.push('b'))
    expect(result.current.canUndo()).toBe(true)

    expect(result.current.undoLast()).toBe(true)
    expect(order).toEqual(['b'])
    expect(result.current.undoLast()).toBe(true)
    expect(order).toEqual(['b', 'a'])
    expect(result.current.canUndo()).toBe(false)
  })
})
