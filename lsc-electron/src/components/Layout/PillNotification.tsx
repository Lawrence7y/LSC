import { useState, useEffect } from 'react'
import {
  CheckCircleFilled,
  InfoCircleFilled,
  WarningFilled,
  CloseCircleFilled,
  ThunderboltFilled,
  LoadingOutlined,
  CloseOutlined,
} from '@ant-design/icons'
import {
  islandManager,
  islandMessageApi,
  type IslandToastItem,
  type IslandToastType,
} from '@/services/notificationBridge'
import './PillNotification.css'

export type PillToastType = IslandToastType

export interface PillToastPayload {
  id?: string
  type?: PillToastType
  message?: React.ReactNode
  content?: React.ReactNode
  duration?: number
}

/**
 * 全局派发灵动通知气泡（向后兼容旧调用）
 */
export function emitPillToast(payload: PillToastPayload): void {
  islandMessageApi.open({
    id: payload.id,
    type: payload.type,
    content: payload.content ?? payload.message ?? '',
    duration: payload.duration,
  })
}

/**
 * 状态胶囊下挂载的灵动通知组件
 */
export function PillNotification() {
  const [toast, setToast] = useState<IslandToastItem | null>(null)

  useEffect(() => {
    return islandManager.subscribe((item) => {
      setToast(item)
    })
  }, [])

  if (!toast) return null

  const type = toast.type || 'info'

  const getIcon = () => {
    if (toast.icon) return toast.icon

    switch (type) {
      case 'record':
        return <span className="pill-dot pill-dot--record" />
      case 'align':
        return <ThunderboltFilled style={{ color: 'var(--brand-400, #4DC4BF)', fontSize: 13 }} />
      case 'loading':
        return <LoadingOutlined style={{ color: 'var(--brand-400, #4DC4BF)', fontSize: 13 }} />
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

  const isSticky = toast.duration === 0

  return (
    <div
      className={`pill-notification-wrapper pill-notification-wrapper--${toast.phase}`}
      aria-live="polite"
    >
      <div
        className={`pill-notification-body pill-notification-body--${type}`}
        onMouseEnter={() => islandManager.pause()}
        onMouseLeave={() => islandManager.resume()}
      >
        <span className="pill-notification-icon">{getIcon()}</span>
        <div className="pill-notification-text">{toast.content}</div>
        {toast.count > 1 && (
          <span className="pill-notification-count">×{toast.count}</span>
        )}
        {isSticky && (
          <button
            type="button"
            className="pill-notification-close"
            onClick={() => islandManager.dismiss()}
            title="关闭"
          >
            <CloseOutlined style={{ fontSize: 9 }} />
          </button>
        )}
      </div>
    </div>
  )
}
