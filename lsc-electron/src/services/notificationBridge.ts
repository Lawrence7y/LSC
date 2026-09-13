import type { ReactNode } from 'react'
import { message as antdStaticMessage, App as AntdApp } from 'antd'

export type IslandToastType = 'info' | 'success' | 'warning' | 'error' | 'loading' | 'record' | 'align'

export interface IslandToastOptions {
  id?: string
  key?: string | number
  type?: IslandToastType
  content: ReactNode
  /** 停留时长（单位：秒或毫秒；<= 60 视为秒，0 为常驻不自动关闭） */
  duration?: number
  onClose?: () => void
  icon?: ReactNode
}

export interface IslandToastItem {
  id: string
  key?: string | number
  type: IslandToastType
  content: ReactNode
  /** 毫秒数，0 为常驻 */
  duration: number
  /** 重复触发合并计数 */
  count: number
  phase: 'entering' | 'active' | 'exiting'
  onClose?: () => void
  icon?: ReactNode
  createdAt: number
}

type Listener = (current: IslandToastItem | null) => void

export class IslandNotificationManager {
  private current: IslandToastItem | null = null
  private queue: IslandToastItem[] = []
  private listeners = new Set<Listener>()
  private dismissTimer: ReturnType<typeof setTimeout> | null = null
  private transitionTimer: ReturnType<typeof setTimeout> | null = null
  private timerStartedAt = 0
  private timerRemaining = 0
  private isPaused = false

  /** 注册 UI 订阅者 */
  subscribe(listener: Listener): () => void {
    this.listeners.add(listener)
    listener(this.current)
    return () => {
      this.listeners.delete(listener)
    }
  }

  private notify() {
    this.listeners.forEach((fn) => {
      try {
        fn(this.current)
      } catch (err) {
        console.warn('[IslandNotification] listener error:', err)
      }
    })
  }

  /** 标准化时长：<=60 视作秒转为毫秒，0 为常驻，默认根据类型赋予合理时长 */
  private normalizeDuration(dur: number | undefined, type: IslandToastType): number {
    if (dur === 0) return 0
    if (typeof dur === 'number' && dur > 0) {
      return dur <= 60 ? Math.round(dur * 1000) : dur
    }
    // 默认时长：错误/警告留给用户更多阅读时间
    if (type === 'error' || type === 'warning') return 4200
    if (type === 'loading') return 0
    return 2800
  }

  /** 派发新提醒 */
  open(options: IslandToastOptions): () => void {
    const type: IslandToastType = options.type || 'info'
    const duration = this.normalizeDuration(options.duration, type)
    const id = options.id || `island_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`

    const newItem: IslandToastItem = {
      id,
      key: options.key,
      type,
      content: options.content,
      duration,
      count: 1,
      phase: 'entering',
      onClose: options.onClose,
      icon: options.icon,
      createdAt: Date.now(),
    }

    // 1. 同 Key 原地平滑更新（例如 loading -> success，或长任务进度更新）
    if (this.current && options.key != null && this.current.key === options.key) {
      this.clearTimers()
      this.current = {
        ...this.current,
        type,
        content: options.content,
        duration,
        icon: options.icon,
        phase: 'active',
      }
      this.notify()
      if (duration > 0) {
        this.startTimer(duration)
      }
      return () => this.destroy(options.key)
    }

    // 2. 相同文本高频合并（800ms 内相同内容合并为 x2, x3，防止刷屏震荡）
    if (
      this.current &&
      options.key == null &&
      this.current.type === type &&
      typeof this.current.content === 'string' &&
      this.current.content === options.content &&
      Date.now() - this.current.createdAt < 1200
    ) {
      this.clearTimers()
      this.current = {
        ...this.current,
        count: this.current.count + 1,
        duration,
        phase: 'active',
      }
      this.notify()
      if (duration > 0) {
        this.startTimer(duration)
      }
      return () => this.destroy(this.current?.id)
    }

    // 3. 当前无显示项：立即展示
    if (!this.current) {
      this.showItem(newItem)
      return () => this.destroy(newItem.id)
    }

    // 4. 高优先级打断：当前是非错误，新到达的是 error / warning 时立即抢占
    if ((type === 'error' || type === 'warning') && this.current.type !== 'error') {
      this.clearTimers()
      this.showItem(newItem)
      return () => this.destroy(newItem.id)
    }

    // 5. 其余情况排队（最多缓存 5 条最新消息）
    if (this.queue.length >= 5) {
      this.queue.shift()
    }
    this.queue.push(newItem)
    return () => this.destroy(newItem.id)
  }

  private showItem(item: IslandToastItem) {
    this.clearTimers()
    this.current = item
    this.notify()

    // 60ms 后切换到 active
    this.transitionTimer = setTimeout(() => {
      if (this.current && this.current.id === item.id) {
        this.current = { ...this.current, phase: 'active' }
        this.notify()
      }
    }, 60)

    if (item.duration > 0) {
      this.startTimer(item.duration)
    }
  }

  private startTimer(ms: number) {
    this.timerStartedAt = Date.now()
    this.timerRemaining = ms
    this.isPaused = false
    this.dismissTimer = setTimeout(() => {
      this.dismiss()
    }, ms)
  }

  /** 鼠标悬停时暂停自动关闭计时 */
  pause() {
    if (this.isPaused || !this.dismissTimer || !this.current || this.current.duration === 0) return
    this.isPaused = true
    clearTimeout(this.dismissTimer)
    this.dismissTimer = null
    const elapsed = Date.now() - this.timerStartedAt
    this.timerRemaining = Math.max(800, this.timerRemaining - elapsed)
  }

  /** 鼠标离开时恢复计时 */
  resume() {
    if (!this.isPaused || !this.current || this.current.duration === 0) return
    this.isPaused = false
    this.timerStartedAt = Date.now()
    this.dismissTimer = setTimeout(() => {
      this.dismiss()
    }, this.timerRemaining)
  }

  /** 平滑收回当前通知 */
  dismiss() {
    if (!this.current) return
    this.clearTimers()
    const closingItem = this.current
    this.current = { ...closingItem, phase: 'exiting' }
    this.notify()

    // 等待退出动画（240ms）完毕
    this.transitionTimer = setTimeout(() => {
      try {
        closingItem.onClose?.()
      } catch (e) {
        console.error('[IslandNotification] onClose callback error:', e)
      }

      this.current = null
      this.notify()

      // 消费队列中的下一条通知
      if (this.queue.length > 0) {
        const next = this.queue.shift()!
        this.showItem(next)
      }
    }, 240)
  }

  /** 根据 key 或 id 销毁通知 */
  destroy(targetKey?: string | number) {
    if (targetKey == null) {
      // 销毁全部
      this.queue = []
      this.dismiss()
      return
    }

    // 从队列清除匹配项
    this.queue = this.queue.filter((item) => item.key !== targetKey && item.id !== targetKey)

    // 若当前正在展示该项，立即关闭
    if (this.current && (this.current.key === targetKey || this.current.id === targetKey)) {
      this.dismiss()
    }
  }

  /** 彻底重置所有状态与计时器（用于单测清理） */
  clearImmediately() {
    this.clearTimers()
    this.queue = []
    this.current = null
    this.isPaused = false
    this.notify()
  }

  private clearTimers() {
    if (this.dismissTimer) {
      clearTimeout(this.dismissTimer)
      this.dismissTimer = null
    }
    if (this.transitionTimer) {
      clearTimeout(this.transitionTimer)
      this.transitionTimer = null
    }
  }

  getCurrent(): IslandToastItem | null {
    return this.current
  }
}

export const islandManager = new IslandNotificationManager()

// ==========================================
// 统一参数解析器，无缝兼容 Ant Design message API
// ==========================================
function parseAntdArgs(
  type: IslandToastType,
  first: any,
  second?: any,
  third?: any
): IslandToastOptions {
  if (first && typeof first === 'object' && 'content' in first) {
    return {
      content: first.content,
      type: first.type || type,
      duration: first.duration,
      key: first.key,
      onClose: first.onClose,
      icon: first.icon,
    }
  }

  const duration = typeof second === 'number' ? second : undefined
  const onClose = typeof second === 'function' ? second : third

  return {
    content: first,
    type,
    duration,
    onClose,
  }
}

function toMessageType(dismiss: () => void): any {
  const fn: any = () => dismiss()
  fn.then = (onfulfilled?: (val: any) => any, onrejected?: (val: any) => any) => {
    return Promise.resolve(true).then(onfulfilled, onrejected)
  }
  return fn
}

/** 灵动岛 API 对象，签名与 Antd message 完全对齐 */
export const islandMessageApi: any = {
  open: (config: IslandToastOptions) => toMessageType(islandManager.open(config)),
  success: (content: any, duration?: any, onClose?: any) =>
    toMessageType(islandManager.open(parseAntdArgs('success', content, duration, onClose))),
  error: (content: any, duration?: any, onClose?: any) =>
    toMessageType(islandManager.open(parseAntdArgs('error', content, duration, onClose))),
  warning: (content: any, duration?: any, onClose?: any) =>
    toMessageType(islandManager.open(parseAntdArgs('warning', content, duration, onClose))),
  info: (content: any, duration?: any, onClose?: any) =>
    toMessageType(islandManager.open(parseAntdArgs('info', content, duration, onClose))),
  loading: (content: any, duration?: any, onClose?: any) =>
    toMessageType(islandManager.open(parseAntdArgs('loading', content, duration, onClose))),
  destroy: (key?: string | number) => islandManager.destroy(key),
}

let isBridgeInstalled = false

/**
 * 全局安装灵动岛消息桥接：
 * 1. 劫持 antd 静态导出的 `message` (如 Workbench/index.tsx 等直接引用的位置)
 * 2. 包装 AntdApp.useApp() 返回的 `message` 实例 (如 Settings, useRoomActions 等)
 */
export function installIslandMessageBridge(): void {
  if (isBridgeInstalled) return
  isBridgeInstalled = true

  // 1. 劫持静态 antd message
  try {
    Object.assign(antdStaticMessage, islandMessageApi)
  } catch (err) {
    console.warn('[installIslandMessageBridge] failed to patch static message:', err)
  }

  // 2. 劫持 App.useApp
  try {
    const originalUseApp = AntdApp.useApp
    if (typeof originalUseApp === 'function') {
      AntdApp.useApp = () => {
        const app = originalUseApp()
        return {
          ...app,
          message: {
            ...app.message,
            ...islandMessageApi,
          },
        }
      }
    }
  } catch (err) {
    console.warn('[installIslandMessageBridge] failed to patch AntdApp.useApp:', err)
  }
}
