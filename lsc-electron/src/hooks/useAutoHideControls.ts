import { useCallback, useEffect, useRef, useState } from 'react'

export type AutoHideControlsOptions = {
  /** 宿主是否处于需要自动隐藏的状态（如「区域已放大」）；false 时 reveal/hide 均复位为隐藏。 */
  enabled: boolean
  /** 空闲多久后自动隐藏（ms）。 */
  idleMs: number
  /** 外部条件（拖动中 / 下拉打开）为真时强制可见，不受空闲计时影响。 */
  pinned?: boolean
}

export type AutoHideControlsState = {
  /** 是否应展示控件（含 pinned 强制可见）。 */
  visible: boolean
  /** 展示并重置空闲计时（指针进入/移动时调用；重复调用不会造成持续重渲染）。 */
  reveal: () => void
  /** 立即隐藏（指针离开宿主、退出放大时调用）。 */
  hide: () => void
}

/**
 * 视频播放器式的「鼠标动就出现、静止一会儿就隐藏」控件显隐状态机。
 *
 * 放大预览底部的「时间线 + 走带按键」是一整块浮层，默认向下隐藏让画面完整可见；
 * 鼠标经过/移动时滑出，静止 `idleMs` 后自动收起。拖动时间线、画质下拉打开等
 * 交互期间必须钉住（`pinned`），否则指针被捕获或移动到 body 上的下拉浮层时
 * 控件会在操作中途消失。
 *
 * 复用点：任何贴在视频上的浮层控件都不应各自重写定时器（漏清定时器会让组件
 * 卸载后 setState 报错，也会让显隐逻辑各说各话）。
 */
export function useAutoHideControls({
  enabled,
  idleMs,
  pinned = false,
}: AutoHideControlsOptions): AutoHideControlsState {
  const [shown, setShown] = useState(false)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const clearTimer = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current)
      timerRef.current = null
    }
  }, [])

  const reveal = useCallback(() => {
    if (!enabled) return
    // 值相同时 React 会 bail out，因此高频 pointermove 不会持续重渲染
    setShown(true)
    clearTimer()
    timerRef.current = setTimeout(() => {
      timerRef.current = null
      setShown(false)
    }, idleMs)
  }, [enabled, idleMs, clearTimer])

  const hide = useCallback(() => {
    clearTimer()
    setShown(false)
  }, [clearTimer])

  // 宿主退出「需要自动隐藏」的状态（如收起放大）：清计时器并复位，
  // 避免下次进入时残留上一次的显示态。
  useEffect(() => {
    if (!enabled) hide()
  }, [enabled, hide])

  useEffect(() => clearTimer, [clearTimer])

  return { visible: shown || pinned, reveal, hide }
}
