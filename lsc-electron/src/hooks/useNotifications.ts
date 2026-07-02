import { useEffect, useRef } from 'react'
import { wsClient } from '@/services/websocket'

interface NotificationPayload {
  title: string
  body: string
  silent?: boolean
}

const TRIGGERS: Record<string, (data: any) => NotificationPayload | null> = {
  clip_completed: (d) => ({
    title: '切片导出完成',
    body: `${d.room_name || '房间'} 切片已就绪`,
  }),
  clip_failed: (d) => ({
    title: '切片导出失败',
    body: d.error || '未知错误',
  }),
  recording_started: (d) => d.success
    ? { title: '录制已开始', body: d.room_name || '直播间', silent: true }
    : { title: '录制启动失败', body: d.error || '未知错误' },
  room_connect_finished: (d) => d.success
    ? null
    : { title: '房间连接失败', body: d.error || '连接失败' },
  reconnect_failed: () => ({
    title: '后端连接断开',
    body: 'WebSocket 重连失败，请检查后端状态',
  }),
}

export function useNotifications() {
  const unsubsRef = useRef<(() => void)[]>([])

  useEffect(() => {
    const triggers = Object.keys(TRIGGERS)

    for (const event of triggers) {
      const handler = (data: any) => {
        const factory = TRIGGERS[event]
        const payload = factory(data)
        if (!payload) return
        // 窗口聚焦时不弹系统通知
        if (document.hasFocus()) return
        window.electronAPI?.showNotification?.(payload)
      }
      unsubsRef.current.push(wsClient.on(event, handler))
    }

    // backend-error 监听
    if (window.electronAPI?.onBackendError) {
      window.electronAPI.onBackendError((error) => {
        if (error) {
          window.electronAPI?.showNotification?.({
            title: '后端启动失败',
            body: error,
          })
        }
      })
    }

    return () => {
      unsubsRef.current.forEach((fn) => fn())
      unsubsRef.current = []
    }
  }, [])
}
