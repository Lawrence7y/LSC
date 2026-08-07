/**
 * 播放头 / 时钟订阅式存储（模块级，不进 React state）。
 *
 * 播放头：采样循环 writePlayhead + rAF 通知订阅者，Timeline 直写 DOM。
 * 时钟：录制已录时长、Live 贴边时刻在 rAF 循环中插值，避免 1s setInterval 跳变。
 */

export type PlayheadListener = (positions: Readonly<Record<string, number>>) => void
export type DisplayPlayheadListener = (absoluteTime: number) => void
export type ClockListener = () => void

/** 时间线显示轴绝对时间（common 或单房 preview），供 Timeline 直写 DOM */
const DISPLAY_KEY = '__display__'

const positions: Record<string, number> = {}
const listeners = new Set<PlayheadListener>()
const displayListeners = new Set<DisplayPlayheadListener>()
const clockListeners = new Set<ClockListener>()
let rafId: number | null = null
let dirty = false
let clockLoopId: number | null = null
let clockLoopRefs = 0

/** Live 右沿插值基准（contentEnd 采样点） */
let liveEdgeBase = { sec: 0, monoMs: 0 }

/** 采样循环写入（高频调用安全：亚帧阈值过滤 + rAF 合帧通知） */
export function writePlayhead(roomId: string, t: number): void {
  if (!roomId || typeof t !== 'number' || t < 0 || !Number.isFinite(t)) return
  // 0.001s：慢放（0.5x）时每显示帧约 8ms 媒体时间，旧阈值 0.01 会隔帧丢更新
  if (Math.abs((positions[roomId] ?? -1) - t) <= 0.001) return
  positions[roomId] = t
  dirty = true
  scheduleFlush()
}

/** 写入时间线显示轴绝对播放头（与 timelineView.currentTime 同轴） */
export function writeDisplayPlayhead(absoluteTime: number): void {
  writePlayhead(DISPLAY_KEY, absoluteTime)
}

/** Live 贴边：写入 contentEnd 采样，供 rAF 插值连续走秒 */
export function writeLiveEdgeBase(contentEndSec: number): void {
  if (!Number.isFinite(contentEndSec) || contentEndSec < 0) return
  liveEdgeBase = { sec: contentEndSec, monoMs: performance.now() }
}

export function readLiveEdgeDisplay(followLive: boolean): number {
  if (!followLive) return readDisplayPlayhead()
  if (liveEdgeBase.monoMs <= 0) return readDisplayPlayhead()
  return liveEdgeBase.sec + (performance.now() - liveEdgeBase.monoMs) / 1000
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

/** 时钟文案订阅：每帧回调一次（需配合 retainClockLoop） */
export function subscribeClock(fn: ClockListener): () => void {
  clockListeners.add(fn)
  return () => {
    clockListeners.delete(fn)
  }
}

/** 有录制/需要连续时钟时保持 rAF 循环 */
export function retainClockLoop(): () => void {
  clockLoopRefs += 1
  ensureClockLoop()
  return () => {
    clockLoopRefs = Math.max(0, clockLoopRefs - 1)
    if (clockLoopRefs === 0 && clockLoopId !== null) {
      cancelAnimationFrame(clockLoopId)
      clockLoopId = null
    }
  }
}

function ensureClockLoop(): void {
  if (clockLoopId !== null) return
  const tick = () => {
    clockLoopId = null
    if (clockLoopRefs <= 0) return
    clockListeners.forEach((fn) => {
      try {
        fn()
      } catch (err) {
        console.error('[playheadStore] clock listener error:', err)
      }
    })
    // 同时冲刷 display 订阅（Live 插值时也要刷新）
    dirty = true
    scheduleFlush()
    clockLoopId = requestAnimationFrame(tick)
  }
  clockLoopId = requestAnimationFrame(tick)
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
