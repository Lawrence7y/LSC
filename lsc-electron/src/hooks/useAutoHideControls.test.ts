import { describe, expect, it, vi, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useAutoHideControls } from './useAutoHideControls'

/**
 * 放大预览底部控制条（时间线 + 按键一体化）的显隐状态机：
 * 默认隐藏 → 鼠标经过/移动滑出 → 静止 idleMs 自动收起。
 * 拖动时间线 / 画质下拉打开（pinned）期间必须钉住，否则操作中途控件会消失。
 */
describe('useAutoHideControls', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('默认隐藏；reveal 后可见，静止 idleMs 自动隐藏', () => {
    vi.useFakeTimers()
    const { result } = renderHook(() =>
      useAutoHideControls({ enabled: true, idleMs: 2500 }),
    )
    expect(result.current.visible).toBe(false)

    act(() => result.current.reveal())
    expect(result.current.visible).toBe(true)
    // 空闲计时未到：仍可见（放大后鼠标停在画面上不会立刻闪掉）
    act(() => { vi.advanceTimersByTime(2499) })
    expect(result.current.visible).toBe(true)

    act(() => { vi.advanceTimersByTime(1) })
    expect(result.current.visible).toBe(false)
  })

  it('持续移动（重复 reveal）会不断重置空闲计时', () => {
    vi.useFakeTimers()
    const { result } = renderHook(() =>
      useAutoHideControls({ enabled: true, idleMs: 2500 }),
    )
    act(() => result.current.reveal())
    act(() => { vi.advanceTimersByTime(2000) })
    act(() => result.current.reveal())
    act(() => { vi.advanceTimersByTime(2000) })
    expect(result.current.visible).toBe(true)
    act(() => { vi.advanceTimersByTime(500) })
    expect(result.current.visible).toBe(false)
  })

  it('hide 立即隐藏（指针离开宿主）', () => {
    vi.useFakeTimers()
    const { result } = renderHook(() =>
      useAutoHideControls({ enabled: true, idleMs: 2500 }),
    )
    act(() => result.current.reveal())
    act(() => result.current.hide())
    expect(result.current.visible).toBe(false)
    // 隐藏后残留的定时器不得把状态又翻回来
    act(() => { vi.advanceTimersByTime(5000) })
    expect(result.current.visible).toBe(false)
  })

  it('pinned（拖动中 / 下拉打开）期间空闲计时不得收起控件', () => {
    vi.useFakeTimers()
    const { result, rerender } = renderHook(
      ({ pinned }: { pinned: boolean }) =>
        useAutoHideControls({ enabled: true, idleMs: 2500, pinned }),
      { initialProps: { pinned: false } },
    )
    act(() => result.current.reveal())
    rerender({ pinned: true })
    act(() => { vi.advanceTimersByTime(10000) })
    expect(result.current.visible).toBe(true)

    // 松开后（不再 pinned）且无新交互：回到隐藏
    rerender({ pinned: false })
    expect(result.current.visible).toBe(false)
  })

  it('未放大（enabled=false）时 reveal 不生效，且退出放大即复位', () => {
    vi.useFakeTimers()
    const { result, rerender } = renderHook(
      ({ enabled }: { enabled: boolean }) =>
        useAutoHideControls({ enabled, idleMs: 2500 }),
      { initialProps: { enabled: true } },
    )
    act(() => result.current.reveal())
    expect(result.current.visible).toBe(true)

    rerender({ enabled: false })
    expect(result.current.visible).toBe(false)
    act(() => result.current.reveal())
    expect(result.current.visible).toBe(false)
  })

  it('卸载时清掉定时器（不得在卸载后 setState）', () => {
    vi.useFakeTimers()
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const { result, unmount } = renderHook(() =>
      useAutoHideControls({ enabled: true, idleMs: 2500 }),
    )
    act(() => result.current.reveal())
    unmount()
    act(() => { vi.advanceTimersByTime(5000) })
    expect(errorSpy).not.toHaveBeenCalled()
    errorSpy.mockRestore()
  })
})
