import { createElement, useCallback, type Dispatch, type MutableRefObject, type SetStateAction } from 'react'
import { App, message } from 'antd'
import { useAppStore } from '@/store/appStore'
import { getAligner } from '@/utils/previewAudioAligner'
import {
  RecordingSpecSelector,
  recordingSpecFromSettings,
} from '@/components/RecordingSpecSelector'
import { t } from '@/i18n'

// send 返回 boolean：false 表示断连且消息被丢弃（useWebSocket.send 已统一弹提示）
type SendFn = (type: string, data?: any) => boolean

/**
 * 房间卡操作回调（录制/预览/静音/删除等）。
 * 内部用 getState 现取 rooms，依赖仅 [send]（及必要的 setState），避免 rooms_updated 击穿 memo。
 */
export function useRoomActions(opts: {
  send: SendFn
  setExpandedRoomId: Dispatch<SetStateAction<string | null>>
  setSelectedRoomIds: Dispatch<SetStateAction<Set<string>>>
  pendingRoomSavesRef: MutableRefObject<number>
}): {
  handleToggleMute: (roomId: string) => void
  handleStartRecord: (roomId: string) => void
  handleStopRecord: (roomId: string) => void
  handleTogglePreview: (roomId: string, enabled: boolean) => void
  handleFullscreen: (roomId: string) => void
  handleCollapse: (roomId: string) => void
  handleRemove: (roomId: string) => void
  handleConnect: (roomId: string) => void
  handleDisconnect: (roomId: string) => void
} {
  const {
    send,
    setExpandedRoomId,
    setSelectedRoomIds,
    pendingRoomSavesRef,
  } = opts
  // context 版 modal：消费 ConfigProvider 主题/locale，避免静态 Modal.confirm 的
  // antd v5 deprecation 警告与上下文丢失
  const { modal } = App.useApp()

  const handleToggleMute = useCallback((roomId: string) => {
    const room = useAppStore.getState().rooms.find((r) => r.room_id === roomId)
    if (!room) return
    const newMuted = !room.preview_muted
    useAppStore.getState().updateRoom(roomId, { preview_muted: newMuted })
    if (!newMuted) {
      const ctx = getAligner().getContextSync()
      if (ctx.state === 'suspended') {
        ctx.resume().catch((e) => {
          console.warn('[Workbench] Failed to resume AudioContext on unmute:', e)
        })
      }
    }
    send('set_preview_muted', { room_id: roomId, muted: newMuted })
  }, [send])

  const handleStartRecord = useCallback((roomId: string) => {
    const spec = recordingSpecFromSettings(useAppStore.getState().settings)
    let selectedSpec = spec
    modal.confirm({
      title: t('选择录制规格'),
      icon: null,
      width: 620,
      okText: t('开始录制'),
      cancelText: t('取消'),
      content: createElement(RecordingSpecSelector, {
        initial: spec,
        onChange: (next) => { selectedSpec = next },
      }),
      onOk: () => {
        useAppStore.getState().updateRoom(roomId, { is_recording_starting: true, last_error: '' })
        send('start_recording', { room_id: roomId, recording_spec: selectedSpec })
      },
    })
  }, [send, modal])

  const handleStopRecord = useCallback((roomId: string) => {
    const ca = useAppStore.getState().continuousAnalysisStatus
    const analyzingThisRoom = Boolean(
      ca?.running && (ca.room_id === roomId || (ca.target_room_ids || []).includes(roomId)),
    )
    send('stop_recording', { room_id: roomId })
    if (analyzingThisRoom) {
      message.info(t('录制已停止。请稍候，持续分析正在收尾并将回合入列待确认，请勿立刻停止分析'), 6)
    }
  }, [send])

  const handleTogglePreview = useCallback((roomId: string, enabled: boolean) => {
    if (enabled) {
      const activePreviews = useAppStore.getState().rooms
        .filter(r => r.preview_enabled && r.room_id !== roomId).length
      if (activePreviews >= 4) {
        message.warning(t('最多 4 路同时预览，请先关闭一路'))
        return
      }
      if (activePreviews >= 3) {
        message.info(t('多路预览已自动降画质以保证流畅'), 3)
      }
    }
    send('enable_preview', { room_id: roomId, enabled, mode: 'mse' })
  }, [send])

  const handleFullscreen = useCallback((roomId: string) => {
    setExpandedRoomId(prev => (prev === roomId ? null : roomId))
  }, [setExpandedRoomId])

  const handleCollapse = useCallback((roomId: string) => {
    setExpandedRoomId(prev => (prev === roomId ? null : prev))
  }, [setExpandedRoomId])

  const handleRemove = useCallback((roomId: string) => {
    setExpandedRoomId(prev => (prev === roomId ? null : prev))
    const continuousStatus = useAppStore.getState().continuousAnalysisStatus
    if (continuousStatus?.running) {
      const targets = continuousStatus.target_room_ids || []
      if (continuousStatus.room_id === roomId || targets.includes(roomId)) {
        send('stop_continuous_analysis', { main_room_id: continuousStatus.room_id })
      }
    }
    pendingRoomSavesRef.current += 1
    send('remove_room', { room_id: roomId })
    setSelectedRoomIds(prev => {
      const next = new Set(prev)
      next.delete(roomId)
      return next
    })
  }, [send, setExpandedRoomId, setSelectedRoomIds, pendingRoomSavesRef])

  const handleConnect = useCallback((roomId: string) => {
    useAppStore.getState().updateRoom(roomId, { is_connecting: true, last_error: '' })
    send('connect_room', { room_id: roomId })
  }, [send])

  const handleDisconnect = useCallback((roomId: string) => {
    const doDisconnect = () => {
      const room = useAppStore.getState().rooms.find(r => r.room_id === roomId)
      if (room?.is_recording) {
        send('stop_recording', { room_id: roomId })
      }
      if (room?.preview_enabled) {
        send('enable_preview', { room_id: roomId, enabled: false, mode: 'mse' })
      }
      const continuousStatus = useAppStore.getState().continuousAnalysisStatus
      if (continuousStatus?.running) {
        const targets = continuousStatus.target_room_ids || []
        if (continuousStatus.room_id === roomId) {
          send('stop_continuous_analysis', { main_room_id: roomId })
        } else if (targets.includes(roomId)) {
          message.warning(t('该房间已退出持续分析映射，后续回合可能仅入列主房'))
        }
      }
      send('disconnect_room', { room_id: roomId })
    }
    const room = useAppStore.getState().rooms.find(r => r.room_id === roomId)
    if (room?.is_recording) {
      modal.confirm({
        title: t('确认断开'),
        content: t('断开将停止录制「{name}」', { name: room.streamer_name || t('未知主播') }),
        okText: t('确认'),
        okButtonProps: { danger: true },
        cancelText: t('取消'),
        onOk: doDisconnect,
      })
      return
    }
    doDisconnect()
  }, [send, modal])

  return {
    handleToggleMute,
    handleStartRecord,
    handleStopRecord,
    handleTogglePreview,
    handleFullscreen,
    handleCollapse,
    handleRemove,
    handleConnect,
    handleDisconnect,
  }
}
