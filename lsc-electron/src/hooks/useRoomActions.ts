import { createElement, useCallback, type Dispatch, type MutableRefObject, type SetStateAction } from 'react'
import { App } from 'antd'
import { useAppStore } from '@/store/appStore'
import { getAligner } from '@/utils/previewAudioAligner'
import {
  RecordingSpecSelector,
  recordingSpecFromSettings,
} from '@/components/RecordingSpecSelector'
import { t } from '@/i18n'
import { confirmDangerous, stopRecordConfirmContent } from '@/utils/confirmDangerous'

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
  /** 带二次确认的停录入口（确认文案与 R 键 / 批量路径同源） */
  requestStopRecord: (roomId: string) => void
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
  // context 版 modal / message：消费 ConfigProvider 主题与 locale。
  // 静态 Modal.confirm / message.* 在 antd v5 下不走主题，暗色界面会弹浅色气泡。
  const { modal, message } = App.useApp()

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
  }, [send, message])

  /**
   * 单房间「停止录制」带二次确认入口：确认文案与 R 键 / 批量路径同源。
   * 卡片以前自己弹一份简化确认（不提持续分析收尾），导致同一动作按入口
   * 给不同信息量；现在统一走这里。
   */
  const requestStopRecord = useCallback((roomId: string) => {
    const st = useAppStore.getState()
    const room = st.rooms.find(r => r.room_id === roomId)
    const ca = st.continuousAnalysisStatus
    const analyzingThisRoom = Boolean(
      ca?.running && (ca.room_id === roomId || (ca.target_room_ids || []).includes(roomId)),
    )
    confirmDangerous(
      modal,
      t('确认停止录制'),
      stopRecordConfirmContent([room?.streamer_name || t('未知主播')], analyzingThisRoom),
      () => handleStopRecord(roomId),
    )
  }, [modal, handleStopRecord])

  const handleTogglePreview = useCallback((roomId: string, enabled: boolean) => {
    if (!enabled) {
      // 本地回看通道由前端持有：关闭预览时必须一并退出，避免残留一个无源的"回看中"卡片
      useAppStore.getState().exitReview(roomId)
    }
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
    const room = useAppStore.getState().rooms.find(r => r.room_id === roomId)
    const name = room?.streamer_name || t('该直播间')
    const isRecording = !!room?.is_recording

    const doRemove = () => {
      setExpandedRoomId(prev => (prev === roomId ? null : prev))
      const continuousStatus = useAppStore.getState().continuousAnalysisStatus
      if (continuousStatus?.running) {
        const targets = continuousStatus.target_room_ids || []
        if (continuousStatus.room_id === roomId || targets.includes(roomId)) {
          send('stop_continuous_analysis', { main_room_id: continuousStatus.room_id })
        }
      }
      if (isRecording) {
        send('stop_recording', { room_id: roomId })
      }
      useAppStore.getState().exitReview(roomId)
      if (room?.preview_enabled) {
        send('enable_preview', { room_id: roomId, enabled: false, mode: 'mse' })
      }
      pendingRoomSavesRef.current += 1
      send('remove_room', { room_id: roomId })
      setSelectedRoomIds(prev => {
        const next = new Set(prev)
        next.delete(roomId)
        return next
      })
    }

    modal.confirm({
      title: isRecording ? t('确认停止录制并删除房间？') : t('确认删除房间？'),
      content: isRecording
        ? t('「{name}」当前正在录制中！删除将立即中止录制，并从工作台移除该房间。', { name })
        : t('将从工作台移除「{name}」，已录制的文件不受影响。', { name }),
      okText: isRecording ? t('停止并删除') : t('确认删除'),
      okButtonProps: { danger: true },
      cancelText: t('取消'),
      onOk: doRemove,
    })
  }, [modal, send, setExpandedRoomId, setSelectedRoomIds, pendingRoomSavesRef])

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
      useAppStore.getState().exitReview(roomId)
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
    requestStopRecord,
    handleTogglePreview,
    handleFullscreen,
    handleCollapse,
    handleRemove,
    handleConnect,
    handleDisconnect,
  }
}
