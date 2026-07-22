/**
 * 播放头位置订阅式存储（模块级，不进 React state）。
 *
 * 背景：预览播放头是纯视觉元素，原先经 200ms setState 轮询驱动整个
 * Workbench 重渲染。改为「采样循环 writePlayhead + rAF 通知订阅者」，
 * 订阅方（Timeline 播放头图层）直接写 DOM，60fps 流畅且不参与 React 渲染周期。
 *
 * 注意：React state（previewPositions）仍保留用于 contentEnd 等逻辑计算，
 * 只是降频更新；本模块只负责高频视觉通道。
 */

export type PlayheadListener = (positions: Readonly<Record<string, number>>) => void
export type DisplayPlayheadListener = (absoluteTime: number) => void

/** 时间线显示轴绝对时间（common 或单房 preview），供 Timeline 直写 DOM */
const DISPLAY_KEY = '__display__'

const positions: Record<string, number> = {}
const listeners = new Set<PlayheadListener>()
const displayListeners = new Set<DisplayPlayheadListener>()
let rafId: number | null = null
let dirty = false

/** 采样循环写入（高频调用安全：阈值过滤 + rAF 合帧通知） */
export function writePlayhead(roomId: string, t: number): void {
  if (!roomId || typeof t !== 'number' || t < 0 || !Number.isFinite(t)) return
  if (Math.abs((positions[roomId] ?? -1) - t) <= 0.01) return
  positions[roomId] = t
  dirty = true
  scheduleFlush()
}

/** 写入时间线显示轴绝对播放头（与 timelineView.currentTime 同轴） */
export function writeDisplayPlayhead(absoluteTime: number): void {
  writePlayhead(DISPLAY_KEY, absoluteTime)
}

export function readPlayhead(roomId: string): number {
  return positions[roomId] ?? 0
}

export function readDisplayPlayhead(): number {
  return positions[DISPLAY_KEY] ?? 0
}

export function removePlayhead(roomId: string): void {
  if (roomId in positions) {
    delete positions[roomId]
    dirty = true
    scheduleFlush()
  }
}

export function subscribePlayhead(fn: PlayheadListener): () => void {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
  }
}

/** Timeline 专用：只接收显示轴绝对时间 */
export function subscribeDisplayPlayhead(fn: DisplayPlayheadListener): () => void {
  displayListeners.add(fn)
  return () => {
    displayListeners.delete(fn)
  }
}

function scheduleFlush(): void {
  if (rafId !== null) return
  rafId = requestAnimationFrame(() => {
    rafId = null
    if (!dirty) return
    dirty = false
    const snapshot = { ...positions }
    listeners.forEach((fn) => {
      try {
        fn(snapshot)
      } catch (err) {
        console.error('[playheadStore] listener error:', err)
      }
    })
    const displayT = snapshot[DISPLAY_KEY]
    if (typeof displayT === 'number') {
      displayListeners.forEach((fn) => {
        try {
          fn(displayT)
        } catch (err) {
          console.error('[playheadStore] display listener error:', err)
        }
      })
    }
  })
}
