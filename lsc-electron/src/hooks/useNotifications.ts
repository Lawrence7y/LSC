import { useEffect, useRef } from 'react'
import { wsClient } from '@/services/websocket'
import { t } from '@/i18n'

interface NotificationPayload {
  title: string
  body: string
  silent?: boolean
}

const TRIGGERS: Record<string, (data: any) => NotificationPayload | null> = {
  clip_completed: (d) => ({
    title: t('切片导出完成'),
    body: `${d.room_name || t('房间')} ${t('切片已就绪')}`,
  }),
  clip_failed: (d) => ({
    title: t('切片导出失败'),
    body: d.error || t('未知错误'),
  }),
  recording_started: (d) => d.success
    ? { title: t('录制已开始'), body: d.room_name || t('直播间'), silent: true }
    : { title: t('录制启动失败'), body: d.error || t('未知错误') },
  room_connect_finished: (d) => d.success
    ? null
    : { title: t('房间连接失败'), body: d.error || t('连接失败') },
  reconnect_failed: () => ({
    title: t('后端连接断开'),
    body: t('WebSocket 重连失败，请检查后端状态'),
  }),
  recording_stopped: (d) => ({
    title: d.reason === 'disk_full' ? t('磁盘空间不足') : t('录制已停止'),
    body: d.message || (d.room_name || t('房间')) + t('录制已停止'),
  }),
}

// 关键错误事件：即使窗口聚焦也必须通知，避免用户错过重要失败信息
const CRITICAL_EVENTS = new Set(['clip_failed', 'reconnect_failed', 'recording_stopped'])

export function useNotifications() {
  const unsubsRef = useRef<(() => void)[]>([])

  useEffect(() => {
    const triggers = Object.keys(TRIGGERS)

    for (const event of triggers) {
      const handler = (data: any) => {
        const factory = TRIGGERS[event]
        const payload = factory(data)
        if (!payload) return
        // 窗口聚焦时跳过非关键通知；关键错误事件始终通知
        if (document.hasFocus() && !CRITICAL_EVENTS.has(event)) return
        window.electronAPI?.showNotification?.(payload)
      }
      unsubsRef.current.push(wsClient.on(event as any, handler))
    }

    // backend-error 监听（返回单条注销函数，卸载时只注销自己，
    // 不再 removeBackendErrorListeners 全量清除误删其他模块的监听）
    let unsubBackendError: (() => void) | void = undefined
    if (window.electronAPI?.onBackendError) {
      unsubBackendError = window.electronAPI.onBackendError((error) => {
        if (error) {
          window.electronAPI?.showNotification?.({
            title: t('后端启动失败'),
            body: error,
          })
        }
      })
    }

    return () => {
      unsubsRef.current.forEach((fn) => fn())
      unsubsRef.current = []
      unsubBackendError?.()
    }
  }, [])
}
