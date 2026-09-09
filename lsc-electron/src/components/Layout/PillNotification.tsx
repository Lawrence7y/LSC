import { useState, useEffect, useRef, useCallback } from 'react'
import {
  CheckCircleFilled,
  InfoCircleFilled,
  WarningFilled,
  CloseCircleFilled,
  ThunderboltFilled,
} from '@ant-design/icons'
import './PillNotification.css'

export type PillToastType = 'success' | 'info' | 'warning' | 'error' | 'record' | 'align'

export interface PillToastPayload {
  id?: string
  type?: PillToastType
  message: string
  /** 停留时长（毫秒，默认 2600ms） */
  duration?: number
}

type ToastItem = PillToastPayload & {
  id: string
  phase: 'entering' | 'active' | 'exiting'
}

type Listener = (payload: PillToastPayload) => void
const listeners = new Set<Listener>()

/**
 * 全局派发灵动通知气泡（从顶栏连接与资源占用胶囊下缘冒出）
 */
export function emitPillToast(payload: PillToastPayload): void {
  listeners.forEach((fn) => {
    try {
      fn(payload)
    } catch (e) {
      console.warn('[PillNotification] emit error:', e)
    }
  })
}

/**
 * 状态胶囊下挂载的灵动通知组件
 */
export function PillNotification() {
  const [currentToast, setCurrentToast] = useState<ToastItem | null>(null)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const exitTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const clearAllTimers = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current)
      timerRef.current = null
    }
    if (exitTimerRef.current) {
      clearTimeout(exitTimerRef.current)
      exitTimerRef.current = null
    }
  }, [])

  const startDismiss = useCallback(() => {
    clearAllTimers()
    setCurrentToast((prev) => (prev ? { ...prev, phase: 'exiting' } : null))
    exitTimerRef.current = setTimeout(() => {
      setCurrentToast(null)
    }, 240) // 与 CSS 退出动画时长匹配
  }, [clearAllTimers])

  useEffect(() => {
    const handler: Listener = (payload) => {
      clearAllTimers()
      const id = payload.id || `${Date.now()}_${Math.random().toString(36).slice(2, 6)}`
      const duration = payload.duration ?? 2600

      // 入场阶段
      setCurrentToast({
        ...payload,
        id,
        phase: 'entering',
      })

      // 80ms 后切换到 active
      setTimeout(() => {
        setCurrentToast((prev) => (prev && prev.id === id ? { ...prev, phase: 'active' } : prev))
      }, 60)

      // 定时启动退出动画
      timerRef.current = setTimeout(() => {
        startDismiss()
      }, duration)
    }

    listeners.add(handler)
    return () => {
      listeners.delete(handler)
      clearAllTimers()
    }
  }, [clearAllTimers, startDismiss])

  if (!currentToast) return null

  const getIcon = (type: PillToastType = 'info') => {
    switch (type) {
      case 'record':
        return <span className="pill-dot pill-dot--record" />
      case 'align':
        return <ThunderboltFilled style={{ color: 'var(--brand-400, #4DC4BF)', fontSize: 13 }} />
      case 'success':
        return <CheckCircleFilled style={{ color: 'var(--state-success, #34c759)', fontSize: 13 }} />
      case 'warning':
        return <WarningFilled style={{ color: 'var(--state-warning, #ff9f0a)', fontSize: 13 }} />
      case 'error':
        return <CloseCircleFilled style={{ color: 'var(--state-error, #ff453a)', fontSize: 13 }} />
      case 'info':
      default:
        return <InfoCircleFilled style={{ color: 'var(--brand-400, #4DC4BF)', fontSize: 13 }} />
    }
  }

  const type = currentToast.type || 'info'

  return (
    <div
      className={`pill-notification-wrapper pill-notification-wrapper--${currentToast.phase}`}
      aria-live="polite"
    >
      <div className={`pill-notification-body pill-notification-body--${type}`}>
        <span className="pill-notification-icon">{getIcon(type)}</span>
        <span className="pill-notification-text">{currentToast.message}</span>
      </div>
    </div>
  )
}
