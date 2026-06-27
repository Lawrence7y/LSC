import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { Row, Col, Card, Input, Button, Space, message, Empty, Modal, Tooltip, Select, Alert } from 'antd'
import { PlusOutlined, VideoCameraOutlined } from '@ant-design/icons'
import { useWebSocket } from '@/hooks/useWebSocket'
import { useAppStore } from '@/store/appStore'
import { useKeyboardShortcuts } from '@/hooks/useKeyboardShortcuts'
import { RoomCard } from './components/RoomCard'
import { ControlBar } from './components/ControlBar'
import { ClipList } from './components/ClipList'
import { RecordSettings } from './components/RecordSettings'
import { ClipSegment } from '@/types'
import { EXPORT_PRESETS, getDefaultPreset } from '@/services/exportPresets'
import { ExportQueue, ExportJob } from '@/components/ExportQueue'
import { formatTime } from '@/utils/time'

// 扩展 ExportJob 以携带 preset_id，便于失败重试时复用原始导出预设
type WorkbenchExportJob = ExportJob & { preset_id?: string }

export default function Workbench() {
  const { isConnected, send, on } = useWebSocket()
  const rooms = useAppStore((state) => state.rooms)
  const selectedRoomId = useAppStore((state) => state.selectedRoomId)
  const connectionStatus = useAppStore((state) => state.connectionStatus)
  // L11: WebSocket 断连提示防抖——仅在断开超过 2 秒后才显示 banner，
  // 避免后端启动慢时反复 connect/disconnect 导致 banner 闪烁
  const [showDisconnectAlert, setShowDisconnectAlert] = useState(false)
  useEffect(() => {
    if (connectionStatus === 'disconnected') {
      const timer = setTimeout(() => setShowDisconnectAlert(true), 2000)
      return () => clearTimeout(timer)
    }
    setShowDisconnectAlert(false)
    return undefined
  }, [connectionStatus])
  const clips = useAppStore((state) => state.clips)
  const recentClips = useAppStore((state) => state.recentClips)
  const setSelectedRoomId = useAppStore((state) => state.setSelectedRoomId)
  const addClip = useAppStore((state) => state.addClip)
  const setClips = useAppStore((state) => state.setClips)
  const [loading, setLoading] = useState(false)
  const [url, setUrl] = useState('')
  const [previewClip, setPreviewClip] = useState<ClipSegment | null>(null)
  const [exportPresetId, setExportPresetId] = useState(getDefaultPreset().id)
  const [exportJobs, setExportJobs] = useState<WorkbenchExportJob[]>([])
  const [analyzing, setAnalyzing] = useState(false)
  const [analysisResults, setAnalysisResults] = useState<{start: number; end: number; score: number}[]>([])
  const [showAnalysisModal, setShowAnalysisModal] = useState(false)
  const [loopPreview, setLoopPreview] = useState(false)
  const [timelineZoom, setTimelineZoom] = useState(1)
  const [sortBy, setSortBy] = useState<string>('default')
  const loopTimerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const [fullscreenRoomId, setFullscreenRoomId] = useState<string | null>(null)
  const [selectedRoomIds, setSelectedRoomIds] = useState<Set<string>>(new Set())
  const [lastClickedIndex, setLastClickedIndex] = useState<number | null>(null)
  const pendingRoomSavesRef = useRef(0)
  const pendingAddUrlRef = useRef('')
  // 多 URL 添加时追踪待完成的响应数量，全部到达后才关闭 loading（M17）
  const pendingAddCountRef = useRef(0)
  // 预览播放位置（从 MSE player 定期读取，驱动时间线播放头）
  const [previewPositions, setPreviewPositions] = useState<Record<string, number>>({})
  // 缓存上次 previewPositions 快照，仅在 currentTime 真正变化时 setState，避免无差别重渲染
  const lastPreviewPositionsRef = useRef<Record<string, number>>({})
  useEffect(() => {
    const id = setInterval(() => {
      const registry = (window as any).__msePlayers
      if (!registry) return
      const next: Record<string, number> = {}
      let changed = false
      for (const rid of Object.keys(registry)) {
        const entry = registry[rid]
        const t = entry?.player?.videoElement?.currentTime
        if (typeof t === 'number' && t >= 0) {
          next[rid] = t
          const prev = lastPreviewPositionsRef.current[rid]
          if (prev === undefined || Math.abs(t - prev) > 0.01) {
            changed = true
          }
        }
      }
      if (changed) {
        lastPreviewPositionsRef.current = next
        setPreviewPositions(next)
      }
    }, 500)
    return () => clearInterval(id)
  }, [])

  // Escape 键退出全屏（capture 阶段拦截，避免触发其他快捷键如 mark_in/out）
  useEffect(() => {
    if (fullscreenRoomId === null) return
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        e.stopPropagation()
        setFullscreenRoomId(null)
      }
    }
    window.addEventListener('keydown', handleKeyDown, true)
    return () => window.removeEventListener('keydown', handleKeyDown, true)
  }, [fullscreenRoomId])

  // 程序切到后台后再切回前台时，恢复所有 MSE player 的播放
  useEffect(() => {
    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        const registry = (window as any).__msePlayers
        if (!registry) return
        for (const rid of Object.keys(registry)) {
          const player = registry[rid]?.player
          if (player && typeof player.resumePlayback === 'function') {
            player.resumePlayback()
          }
        }
      }
    }
    document.addEventListener('visibilitychange', handleVisibilityChange)
    return () => document.removeEventListener('visibilitychange', handleVisibilityChange)
  }, [])

  // Workbench 卸载时停止所有后端预览，防止 FFmpeg 空转 + segment 堆积
  useEffect(() => {
    return () => {
      const currentRooms = useAppStore.getState().rooms
      for (const room of currentRooms) {
        if (room.preview_enabled) {
          send('enable_preview', { room_id: room.room_id, enabled: false, mode: 'mse' })
        }
      }
    }
  }, [send])

  // Sync store's selectedRoomId with multi-select state
  useEffect(() => {
    setSelectedRoomId(selectedRoomIds.size === 1 ? [...selectedRoomIds][0] : null)
  }, [selectedRoomIds, setSelectedRoomId])

  // 反向同步：当 store 的 selectedRoomId 变化（例如从 Dashboard 跳转过来）且不在多选集合中时，
  // 将其同步到 selectedRoomIds，确保 Workbench 选中该房间
  useEffect(() => {
    if (selectedRoomId && !selectedRoomIds.has(selectedRoomId)) {
      setSelectedRoomIds(new Set([selectedRoomId]))
    }
  }, [selectedRoomId, selectedRoomIds])

  // 获取房间列表
  useEffect(() => {
    if (isConnected) {
      send('get_rooms', {})
    }
  }, [isConnected, send])

  // 监听后端事件（仅用于界面反馈，状态由 useWebSocket 统一写入 store）
  useEffect(() => {
    const unsubs: (() => void)[] = []
    unsubs.push(on('room_connect_finished', (data: { room_id: string; success: boolean; error: string }) => {
      if (!data.success) {
        message.error(`连接失败：${data.error || '未知错误'}`)
      }
    }))
    unsubs.push(on('recording_started', (data: { room_id: string; success: boolean; error: string }) => {
      if (data.success) {
        message.success('录制已开始')
      } else {
        message.error(`录制启动失败：${data.error || '未知错误'}`)
      }
    }))
    // 单房间录制响应（后端返回 start_recording_response，不是 recording_started）
    unsubs.push(on('start_recording_response', (data: { success?: boolean; error?: string }) => {
      if (data?.success) {
        message.success('录制已开始')
      } else {
        message.error(`录制启动失败：${data?.error || '未知错误'}`)
      }
    }))
    // 停止录制响应
    unsubs.push(on('stop_recording_response', (data: { success?: boolean; error?: string }) => {
      if (data?.success) {
        message.success('录制已停止')
      } else {
        message.error(`停止录制失败：${data?.error || '未知错误'}`)
      }
    }))
    return () => unsubs.forEach(u => u())
  }, [on])

  // 添加/删除房间成功后，发送 save_rooms 持久化房间列表
  useEffect(() => {
    const unsubs: (() => void)[] = []

    unsubs.push(on('rooms_updated', () => {
      if (pendingRoomSavesRef.current > 0) {
        pendingRoomSavesRef.current -= 1
        const currentRooms = useAppStore.getState().rooms
        send('save_rooms', { rooms: currentRooms })
      }
    }))

    unsubs.push(on('add_room_response', (data: { success?: boolean; error?: string; room_id?: string }) => {
      // 多 URL 添加：用计数器追踪待完成数量，全部响应到达后才关闭 loading（M17）
      if (pendingAddCountRef.current > 0) {
        pendingAddCountRef.current -= 1
        if (pendingAddCountRef.current === 0) {
          setLoading(false)
        }
      } else {
        // 兜底：单 URL 或异常情况下直接关闭 loading
        setLoading(false)
      }
      if (data?.success === false || data?.error) {
        // 添加失败：显示错误提示，保留输入框内容供重试
        if (pendingRoomSavesRef.current > 0) {
          pendingRoomSavesRef.current -= 1
        }
        message.error(data.error || '添加房间失败')
        // 恢复输入框为待添加的 URL（因为可能已被清空）
        if (pendingAddUrlRef.current) {
          setUrl(pendingAddUrlRef.current)
          pendingAddUrlRef.current = ''
        }
      } else {
        // 添加成功：清空输入框
        pendingAddUrlRef.current = ''
        setUrl('')
      }
    }))

    unsubs.push(on('remove_room_response', (data: { error?: string }) => {
      if (data?.error && pendingRoomSavesRef.current > 0) {
        pendingRoomSavesRef.current -= 1
      }
    }))

    return () => unsubs.forEach(u => u())
  }, [on, send])

  // 监听导出完成事件，更新导出队列状态
  useEffect(() => {
    const unsubs: (() => void)[] = []
    unsubs.push(on('clip_completed', (data: any) => {
      if (data?.job_id) {
        setExportJobs(prev => prev.map(j =>
          j.id === data.job_id
            ? { ...j, status: 'completed' as const, progress: 100, outputPath: data.output_path }
            : j
        ))
      }
    }))
    // 导出失败/取消：后端通过 clip_failed 通知，避免队列永久 running
    unsubs.push(on('clip_failed', (data: { job_id?: string; error?: string }) => {
      if (data?.job_id) {
        const isCancelled = data.error === '导出已取消'
        setExportJobs(prev => prev.map(j =>
          j.id === data.job_id
            ? { ...j, status: isCancelled ? 'cancelled' as const : 'failed' as const, error: data.error || '导出失败' }
            : j
        ))
        if (!isCancelled && data.error) {
          message.error(`导出失败：${data.error}`)
        }
      }
    }))
    // 导出进度上报
    unsubs.push(on('export_progress', (data: { job_id?: string; percent?: number }) => {
      if (data?.job_id && typeof data.percent === 'number') {
        const pct = data.percent
        setExportJobs(prev => prev.map(j =>
          j.id === data.job_id
            ? { ...j, progress: Math.max(0, Math.min(99, Math.round(pct))) }
            : j
        ))
      }
    }))
    // export_clip 提交响应：失败时立即置 failed，避免队列永久 running
    unsubs.push(on('export_clip_response', (data: { success?: boolean; error?: string; job_id?: string }) => {
      if (data?.job_id && data?.success === false) {
        setExportJobs(prev => prev.map(j =>
          j.id === data.job_id
            ? { ...j, status: 'failed' as const, error: data.error || '导出启动失败' }
            : j
        ))
        message.error(`导出失败：${data.error || '未知错误'}`)
      }
    }))
    // cancel_export 响应：后端确认取消后置 cancelled，失败时提示用户
    unsubs.push(on('cancel_export_response', (data: { success?: boolean; error?: string; job_id?: string }) => {
      // cancel_export 后端目前不返回 job_id，需要从入参追踪。这里仅处理失败提示。
      if (data?.success === false) {
        message.warning(`取消导出失败：${data.error || '任务可能已结束'}`)
      }
    }))
    return () => unsubs.forEach(u => u())
  }, [on])

  // 添加房间
  const handleAddRoom = async () => {
    if (loading) return
    const trimmedUrl = url.trim()
    if (!trimmedUrl) {
      message.warning('请输入直播间链接')
      return
    }

    // Support multi-line paste: split by newlines and add each URL
    const urls = trimmedUrl.split('\n').map(u => u.trim()).filter(Boolean)

    if (urls.length > 1) {
      message.info(`正在添加 ${urls.length} 个房间...`)
    }

    setLoading(true)
    // 多 URL 添加：计数器记录待完成响应数量，全部到达后才关闭 loading（M17）
    pendingAddCountRef.current = urls.length
    urls.forEach((u, i) => {
      pendingRoomSavesRef.current += 1
      if (i === urls.length - 1) {
        pendingAddUrlRef.current = u
      }
      send('add_room', { url: u })
    })
    // 不在此处清空输入框或 setLoading(false)
    // 改为在 add_room_response 回调中处理
  }

  // 连接房间
  const handleConnect = useCallback((roomId: string) => {
    send('connect_room', { room_id: roomId })
  }, [send])

  // 断开房间
  const handleDisconnect = useCallback((roomId: string) => {
    send('disconnect_room', { room_id: roomId })
  }, [send])

  // 切换静音
  const handleToggleMute = useCallback((roomId: string) => {
    const room = useAppStore.getState().rooms.find((r) => r.room_id === roomId)
    if (!room) return
    send('set_preview_muted', { room_id: roomId, muted: !room.preview_muted })
  }, [send])

  // 开始录制
  const handleStartRecord = useCallback((roomId: string) => {
    send('start_recording', { room_id: roomId })
  }, [send])

  // 停止录制
  const handleStopRecord = useCallback((roomId: string) => {
    send('stop_recording', { room_id: roomId })
  }, [send])

  // 启用/停止预览（Electron 模式：后端 FFmpeg 抓帧推送）
  const handleTogglePreview = useCallback((roomId: string, enabled: boolean) => {
    send('enable_preview', { room_id: roomId, enabled, mode: 'mse' })
  }, [send])

  // 全屏预览（点击同一房间切换全屏）
  const handleFullscreen = useCallback((roomId: string) => {
    setFullscreenRoomId(prev => prev === roomId ? null : roomId)
  }, [])

  // 删除房间
  const handleRemove = useCallback((roomId: string) => {
    // 项目记忆硬约束："Fullscreen preview must be exited before deleting a
    // room to avoid crashes"。若该房间正处于全屏预览，先退出全屏再删除，
    // 避免 VideoPreview 组件实例继续渲染已删除房间的 MSE streamer 导致崩溃。
    if (fullscreenRoomId === roomId) {
      setFullscreenRoomId(null)
    }
    pendingRoomSavesRef.current += 1
    send('remove_room', { room_id: roomId })
    setSelectedRoomIds(prev => {
      const next = new Set(prev)
      next.delete(roomId)
      return next
    })
  }, [send, fullscreenRoomId])

  // 选择房间（支持 Ctrl/Shift 多选）
  const handleSelect = useCallback((roomId: string, e: React.MouseEvent) => {
    const currentRooms = useAppStore.getState().rooms
    const roomIndex = currentRooms.findIndex(r => r.room_id === roomId)

    if (e.ctrlKey || e.metaKey) {
      // Ctrl+Click: toggle selection
      setSelectedRoomIds(prev => {
        const next = new Set(prev)
        if (next.has(roomId)) {
          next.delete(roomId)
        } else {
          next.add(roomId)
        }
        return next
      })
      setLastClickedIndex(roomIndex)
    } else if (e.shiftKey && lastClickedIndex !== null) {
      // Shift+Click: range selection
      const start = Math.min(lastClickedIndex, roomIndex)
      const end = Math.max(lastClickedIndex, roomIndex)
      const rangeIds = currentRooms.slice(start, end + 1).map(r => r.room_id)
      setSelectedRoomIds(new Set(rangeIds))
      setLastClickedIndex(roomIndex)
    } else {
      // Normal click: single selection
      setSelectedRoomIds(new Set([roomId]))
      setLastClickedIndex(roomIndex)
    }
  }, [lastClickedIndex])

  // 批量录制（绑定快捷键 Ctrl+R，需二次确认以防误触）
  const handleBatchRecord = useCallback(() => {
    const connectableRooms = useAppStore.getState().rooms.filter(r => r.is_connected && !r.is_recording)
    if (connectableRooms.length === 0) {
      message.info('没有可录制的房间')
      return
    }
    Modal.confirm({
      title: '确认批量录制',
      content: `将开始录制 ${connectableRooms.length} 个房间`,
      okText: '确认录制',
      cancelText: '取消',
      onOk: () => {
        connectableRooms.forEach(r => {
          send('start_recording', { room_id: r.room_id })
        })
      },
    })
  }, [send])

  // 批量停止（绑定快捷键 Ctrl+Shift+R，需二次确认以防误触）
  const handleBatchStop = useCallback(() => {
    const recordingRooms = useAppStore.getState().rooms.filter(r => r.is_recording)
    if (recordingRooms.length === 0) {
      message.info('没有正在录制的房间')
      return
    }
    Modal.confirm({
      title: '确认批量停止',
      content: `将停止 ${recordingRooms.length} 个房间的录制`,
      okText: '确认停止',
      cancelText: '取消',
      okButtonProps: { danger: true },
      onOk: () => {
        recordingRooms.forEach(r => {
          send('stop_recording', { room_id: r.room_id })
        })
      },
    })
  }, [send])

  // 获取 MSE player 的当前播放位置
  const getPreviewCurrentTime = useCallback((roomId: string): number => {
    const registry = (window as any).__msePlayers
    const entry = registry?.[roomId]
    if (entry?.player?.videoElement) {
      return entry.player.videoElement.currentTime
    }
    return 0
  }, [])

  // 直接控制 MSE player 的 video 元素（Electron 模式下后端无法控制 MSE video）
  const mseSeek = useCallback((roomId: string, time: number) => {
    const registry = (window as any).__msePlayers
    const video = registry?.[roomId]?.player?.videoElement as HTMLVideoElement | undefined
    if (video) {
      try { video.currentTime = time } catch { /* may fail if not ready */ }
    }
    send('seek', { room_id: roomId, time })
  }, [send])

  const mseTogglePlayPause = useCallback((roomId: string) => {
    const registry = (window as any).__msePlayers
    const video = registry?.[roomId]?.player?.videoElement as HTMLVideoElement | undefined
    if (video) {
      if (video.paused) video.play().catch(() => {})
      else video.pause()
    }
    send('toggle_play_pause', { room_id: roomId })
  }, [send])

  // 时间线跳转（多选时同步 seek 所有选中房间）
  const handleTimelineSeek = useCallback((time: number) => {
    selectedRoomIds.forEach(rid => mseSeek(rid, time))
  }, [selectedRoomIds, mseSeek])

  // 设置入点
  const handleMarkIn = useCallback((roomId: string) => {
    const time = getPreviewCurrentTime(roomId)
    send('set_mark_in', { room_id: roomId, time })
  }, [send, getPreviewCurrentTime])

  // 设置出点
  const handleMarkOut = useCallback((roomId: string) => {
    const time = getPreviewCurrentTime(roomId)
    send('set_mark_out', { room_id: roomId, time })
  }, [send, getPreviewCurrentTime])

  // 添加到切片列表
  const handleAddClip = useCallback((roomId: string) => {
    const currentRooms = useAppStore.getState().rooms
    const currentClips = useAppStore.getState().clips
    const room = currentRooms.find(r => r.room_id === roomId)
    if (room && room.mark_in !== null && room.mark_out !== null) {
      const newClip: ClipSegment = {
        start: room.mark_in,
        end: room.mark_out,
        label: `${room.streamer_name} - 片段 ${currentClips.length + 1}`,
        room_id: roomId,
      }
      addClip(newClip)
      message.success('已添加到切片列表')
    } else {
      message.warning('请先设置入点和出点')
    }
  }, [addClip])

  // ── 稳定的 ControlBar / RoomCard 回调 ──

  const handleControlPlayPause = useCallback(() => {
    selectedRoomIds.forEach(rid => mseTogglePlayPause(rid))
  }, [selectedRoomIds, mseTogglePlayPause])

  const handleControlSeekBack = useCallback(() => {
    selectedRoomIds.forEach(rid => {
      const cur = getPreviewCurrentTime(rid)
      mseSeek(rid, Math.max(0, cur - 10))
    })
  }, [selectedRoomIds, getPreviewCurrentTime, mseSeek])

  const handleControlSeekFwd = useCallback(() => {
    selectedRoomIds.forEach(rid => {
      const cur = getPreviewCurrentTime(rid)
      mseSeek(rid, cur + 10)
    })
  }, [selectedRoomIds, getPreviewCurrentTime, mseSeek])

  const handleControlMarkIn = useCallback(() => {
    selectedRoomIds.forEach(rid => {
      const time = getPreviewCurrentTime(rid)
      send('set_mark_in', { room_id: rid, time })
    })
  }, [selectedRoomIds, send, getPreviewCurrentTime])

  const handleControlMarkOut = useCallback(() => {
    selectedRoomIds.forEach(rid => {
      const time = getPreviewCurrentTime(rid)
      send('set_mark_out', { room_id: rid, time })
    })
  }, [selectedRoomIds, send, getPreviewCurrentTime])

  const handleControlAddClip = useCallback(() => {
    if (selectedRoomIds.size === 0) return
    selectedRoomIds.forEach(rid => handleAddClip(rid))
  }, [selectedRoomIds, handleAddClip])

  const handleControlFullscreen = useCallback(() => {
    if (selectedRoomId) setFullscreenRoomId(prev => prev === selectedRoomId ? null : selectedRoomId)
  }, [selectedRoomId])

  const handleGoLive = useCallback(() => {
    selectedRoomIds.forEach(rid => {
      const registry = (window as any).__msePlayers
      const video = registry?.[rid]?.player?.videoElement as HTMLVideoElement | undefined
      if (video && video.buffered.length > 0) {
        const bufEnd = video.buffered.end(video.buffered.length - 1)
        video.currentTime = Math.max(0, bufEnd - 0.5)
        video.play().catch(() => {})
      }
    })
  }, [selectedRoomIds])

  const handleMarkerDrag = useCallback((type: 'in' | 'out', time: number) => {
    selectedRoomIds.forEach(rid => {
      if (type === 'in') {
        send('set_mark_in', { room_id: rid, time })
      } else {
        send('set_mark_out', { room_id: rid, time })
      }
    })
  }, [selectedRoomIds, send])

  // 删除切片
  const handleDeleteClip = (index: number) => {
    setClips(clips.filter((_, i) => i !== index))
  }

  // 导出切片
  const handleExportClip = (clip: ClipSegment) => {
    setPreviewClip(clip)
  }

  const handleConfirmExport = () => {
    if (!previewClip) return
    const room = rooms.find(r => r.room_id === previewClip.room_id)
    const jobId = `export-${Date.now()}`

    // Add job to queue
    const newJob: WorkbenchExportJob = {
      id: jobId,
      roomName: room?.streamer_name || '未知房间',
      label: previewClip.label || '切片',
      startTime: formatTime(previewClip.start),
      startSeconds: previewClip.start,
      endSeconds: previewClip.end,
      duration: previewClip.end - previewClip.start,
      progress: 0,
      status: 'running',
      roomId: previewClip.room_id!,
      createdAt: Date.now(),
      preset_id: exportPresetId,
    }
    setExportJobs(prev => [...prev, newJob])

    send('export_clip', {
      room_id: previewClip.room_id,
      start: previewClip.start,
      end: previewClip.end,
      label: previewClip.label,
      preset_id: exportPresetId,
      job_id: jobId,
    })
    setPreviewClip(null)
    message.info('导出任务已提交')
  }

  const handleCancelExport = () => {
    setPreviewClip(null)
  }

  // ── 选区试听（循环播放） ──
  // Use ref for loopPreview so the callback stays stable
  const loopPreviewRef = useRef(loopPreview)
  loopPreviewRef.current = loopPreview

  const handleToggleLoop = useCallback(() => {
    if (loopPreviewRef.current) {
      // Stop loop
      setLoopPreview(false)
      if (loopTimerRef.current) {
        clearInterval(loopTimerRef.current)
        loopTimerRef.current = null
      }
      return
    }

    const state = useAppStore.getState()
    const currentSelectedId = state.selectedRoomId
    if (!currentSelectedId) return
    const room = state.rooms.find(r => r.room_id === currentSelectedId)
    if (!room || room.mark_in === null || room.mark_out === null) {
      message.warning('请先设置入点和出点')
      return
    }

    // Start loop: seek to mark_in then periodically check and seek back
    mseSeek(currentSelectedId, room.mark_in)
    setLoopPreview(true)

    // 定时器回调中通过 store 实时读取最新 state，避免闭包陈旧（M16）
    loopTimerRef.current = setInterval(() => {
      const currentState = useAppStore.getState()
      const id = currentState.selectedRoomId
      if (!id) return
      const r = currentState.rooms.find(rm => rm.room_id === id)
      if (r?.mark_in != null) {
        mseSeek(id, r.mark_in)
      }
    }, (room.mark_out - room.mark_in) * 1000 + 100)
  }, [mseSeek])

  // Cleanup loop timer on unmount
  useEffect(() => {
    return () => {
      if (loopTimerRef.current) clearInterval(loopTimerRef.current)
    }
  }, [])
  const handleStartAnalysis = () => {
    if (!selectedRoomId) {
      message.warning('请先选中一个已录制的房间')
      return
    }
    const room = rooms.find(r => r.room_id === selectedRoomId)
    if (!room?.is_recording && !room?.record_output_path) {
      message.warning('该房间没有录制文件可分析')
      return
    }
    setAnalyzing(true)
    send('start_analysis', { room_id: selectedRoomId, threshold: 0.3 })
  }

  // 监听分析结果
  useEffect(() => {
    const unsub = on('start_analysis_response', (data: any) => {
      setAnalyzing(false)
      if (data?.success && data?.highlights) {
        setAnalysisResults(data.highlights)
        setShowAnalysisModal(true)
      } else {
        message.error(data?.error || '分析失败')
      }
    })
    return () => unsub()
  }, [on])

  // 将分析结果导入切片列表
  const handleImportAnalysis = () => {
    const room = rooms.find(r => r.room_id === selectedRoomId)
    const newClips = analysisResults.map((h, i) => ({
      start: h.start,
      end: h.end,
      label: `${room?.streamer_name || '房间'} - 高光 ${i + 1}`,
      room_id: selectedRoomId!,
    }))
    setClips([...clips, ...newClips])
    setShowAnalysisModal(false)
    message.success(`已导入 ${newClips.length} 个高光片段`)
  }

  // ── 导出队列管理 ──
  const handleCancelJob = (jobId: string) => {
    // 通知后端取消导出，停止 FFmpeg 进程。
    // 不在此处立即置 cancelled —— 等 clip_failed（error='导出已取消'）到达后再置，
    // 避免后端取消失败时 UI 与实际状态不一致。
    send('cancel_export', { job_id: jobId })
  }

  const handleRetryJob = (jobId: string) => {
    const job = exportJobs.find(j => j.id === jobId)
    if (!job) return
    // 重试生成新 jobId 并重置状态，避免与残留映射碰撞
    const newJobId = `export-${Date.now()}`
    setExportJobs(prev => prev.map(j =>
      j.id === jobId
        ? { ...j, id: newJobId, status: 'running' as const, progress: 0, error: undefined }
        : j
    ))
    send('export_clip', {
      room_id: job.roomId,
      start: job.startSeconds,
      end: job.endSeconds,
      label: job.label,
      preset_id: job.preset_id,
      job_id: newJobId,
    })
  }

  const handleRemoveJob = (jobId: string) => {
    setExportJobs(prev => prev.filter(j => j.id !== jobId))
  }

  const handleClearCompleted = () => {
    setExportJobs(prev => prev.filter(j => j.status !== 'completed'))
  }

  const handleOpenExportFolder = (outputPath: string) => {
    if (window.electronAPI) {
      // 优先用 showItemInFolder 在资源管理器中高亮定位文件，
      // 避免 openPath 用默认播放器打开 .mp4（与"打开文件夹"按钮语义不符）。
      if (window.electronAPI.showItemInFolder) {
        window.electronAPI.showItemInFolder(outputPath)
      } else {
        window.electronAPI.openPath(outputPath)
      }
    }
  }

  // 点击最近切片：选中房间并跳转到入点
  const handleRecentClipClick = (clip: ClipSegment) => {
    if (!clip.room_id) return
    setSelectedRoomId(clip.room_id)
    mseSeek(clip.room_id, clip.start)
  }

  const selectedRoom = rooms.find(r => r.room_id === selectedRoomId)

  // Sort rooms (memoize to prevent new array reference on every render)
  const sortedRooms = useMemo(() => [...rooms].sort((a, b) => {
    switch (sortBy) {
      case 'status':
        return (a.is_recording ? -1 : 1) - (b.is_recording ? -1 : 1) ||
               (a.is_connected ? -1 : 1) - (b.is_connected ? -1 : 1)
      case 'platform':
        return (a.platform_name || '').localeCompare(b.platform_name || '')
      case 'name':
        return (a.streamer_name || '').localeCompare(b.streamer_name || '')
      default:
        return 0
    }
  }), [rooms, sortBy])

  // ── 工作区全局快捷键 ──
  const handleWorkbenchShortcut = useCallback(
    (id: string) => {
      const firstSelectedId = selectedRoomIds.size === 1 ? [...selectedRoomIds][0] : selectedRoomId

      if (!firstSelectedId && !['batch:record', 'batch:stop', 'select:all'].includes(id)) {
        return
      }

      switch (id) {
        case 'play:toggle':
          selectedRoomIds.forEach(rid => mseTogglePlayPause(rid))
          break
        case 'mark:in':
          selectedRoomIds.forEach(rid => handleMarkIn(rid))
          break
        case 'mark:out':
          selectedRoomIds.forEach(rid => handleMarkOut(rid))
          break
        case 'record:toggle': {
          // Iterate each selected room individually to avoid mixed-state issues
          selectedRoomIds.forEach(rid => {
            const r = rooms.find(r2 => r2.room_id === rid)
            if (!r) return
            if (r.is_recording) {
              handleStopRecord(rid)
            } else if (r.is_connected) {
              handleStartRecord(rid)
            }
          })
          break
        }
        case 'mute:toggle':
          selectedRoomIds.forEach(rid => handleToggleMute(rid))
          break
        case 'fullscreen':
          if (firstSelectedId) setFullscreenRoomId(prev => prev === firstSelectedId ? null : firstSelectedId)
          break
        case 'batch:record':
          handleBatchRecord()
          break
        case 'batch:stop':
          handleBatchStop()
          break
        case 'select:all':
          if (rooms.length > 0) {
            setSelectedRoomIds(new Set(rooms.map(r => r.room_id)))
            message.info(`已选中 ${rooms.length} 个房间`)
          }
          break
        case 'export:clip':
          if (clips.length > 0) {
            handleExportClip(clips[0])
          } else {
            message.info('切片列表为空')
          }
          break
      }
    },
    [selectedRoomIds, selectedRoomId, send, rooms, clips]
  )

  useKeyboardShortcuts(
    [
      { key: ' ',                                   id: 'play:toggle' },
      { key: 'i',                                   id: 'mark:in' },
      { key: 'o',                                   id: 'mark:out' },
      { key: 'r', ctrl: false,        preventDefault: false, id: 'record:toggle' },
      { key: 'm',                                   id: 'mute:toggle' },
      { key: 'f',                                   id: 'fullscreen' },
      { key: 'r', ctrl: true,                       id: 'batch:record' },
      { key: 'r', ctrl: true, shift: true,          id: 'batch:stop' },
      // 全选房间快捷键：使用 Ctrl+Shift+A 避免覆盖浏览器 Ctrl+A 全选默认行为
      { key: 'a', ctrl: true, shift: true,          id: 'select:all' },
      { key: 'e', ctrl: true,                       id: 'export:clip' },
    ],
    handleWorkbenchShortcut
  )

  // R 键按下的场景：仅在非输入框焦点时触发作录制/停止切换，且不吞掉默认行为（输入框需要 R）

  const batchRecordDisabled = !rooms.some(r => r.is_connected && !r.is_recording)
  const batchRecordTooltip = batchRecordDisabled
    ? (rooms.length === 0
        ? '没有可录制的房间'
        : !rooms.some(r => r.is_connected)
          ? '没有已连接的房间'
          : '所有已连接房间已在录制中')
    : undefined

  const batchStopDisabled = !rooms.some(r => r.is_recording)
  const batchStopTooltip = batchStopDisabled ? '没有正在录制的房间' : undefined

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      {/* WebSocket 连接状态提示（防抖：仅在断开超过 2 秒后显示） */}
      {showDisconnectAlert && (
        <Alert
          type="error"
          message="WebSocket 连接断开，正在重连..."
          banner
          showIcon
        />
      )}
      {/* 顶部操作栏 */}
      <div style={{ 
        padding: '16px 24px',
        background: 'var(--bg-secondary)',
        borderBottom: '1px solid var(--border-default)',
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
      }}>
        <Space>
          <h2 style={{ margin: 0, fontSize: 18, fontWeight: 600 }}>多房间工作台</h2>
          <span style={{ color: 'var(--text-tertiary)', fontSize: 13 }}>
            {rooms.length} 个房间
          </span>
          <Select
            size="small"
            value={sortBy}
            onChange={setSortBy}
            style={{ width: 110, marginLeft: 8, fontSize: 12 }}
            options={[
              { value: 'default', label: '默认排序' },
              { value: 'status', label: '按状态' },
              { value: 'platform', label: '按平台' },
              { value: 'name', label: '按名称' },
            ]}
          />
          <Button
            type="default"
            size="small"
            onClick={handleStartAnalysis}
            loading={analyzing}
            disabled={!selectedRoomId || !rooms.find(r => r.room_id === selectedRoomId)?.record_output_path}
          >
            {analyzing ? '分析中...' : '分析高光'}
          </Button>
        </Space>
        <Space>
          <Button
            size="small"
            onClick={() => {
              if (rooms.length > 0) {
                setSelectedRoomIds(new Set(rooms.map(r => r.room_id)))
                message.info(`已选中 ${rooms.length} 个房间`)
              }
            }}
            disabled={rooms.length === 0}
          >
            全选
          </Button>
          <Tooltip title={batchRecordTooltip}>
            <span>
              <Button
                type="primary"
                icon={<VideoCameraOutlined />}
                onClick={handleBatchRecord}
                disabled={batchRecordDisabled}
              >
                批量录制
              </Button>
            </span>
          </Tooltip>
          <Tooltip title={batchStopTooltip}>
            <span>
              <Button
                danger
                onClick={handleBatchStop}
                disabled={batchStopDisabled}
              >
                批量停止
              </Button>
            </span>
          </Tooltip>
        </Space>
      </div>

      {/* 主内容区 */}
      <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>
        {/* 左侧：房间卡片 + 控制栏 */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
          {/* 房间卡片网格 */}
          <div style={{ flex: 1, overflow: 'auto', padding: '16px 24px' }}>
            {rooms.length === 0 ? (
              <Empty
                description="暂无房间，请添加直播间地址"
                style={{ marginTop: 100 }}
              />
            ) : (
              <Row gutter={[16, 16]}>
                {sortedRooms.map(room => (
                  <Col key={room.room_id} xs={24} sm={12} lg={12} xl={8}>
                    <RoomCard
                      room={room}
                      send={send}
                      selected={selectedRoomIds.has(room.room_id)}
                      multiSelected={selectedRoomIds.size > 1 && selectedRoomIds.has(room.room_id)}
                      onSelect={handleSelect}
                      onConnect={handleConnect}
                      onDisconnect={handleDisconnect}
                      onStartRecord={handleStartRecord}
                      onStopRecord={handleStopRecord}
                      onRemove={handleRemove}
                      onTogglePreview={handleTogglePreview}
                      onToggleMute={handleToggleMute}
                      onFullscreen={handleFullscreen}
                      fullscreenRoomId={fullscreenRoomId}
                    />
                  </Col>
                ))}
              </Row>
            )}
          </div>

          {/* 底部控制栏 */}
          <ControlBar
            room={selectedRoom}
            multiSelectCount={selectedRoomIds.size > 1 ? selectedRoomIds.size : 0}
            loopPreview={loopPreview}
            clips={clips}
            previewPos={previewPositions[selectedRoom?.room_id ?? ''] ?? 0}
            onSeek={handleTimelineSeek}
            onPlayPause={handleControlPlayPause}
            onSeekBack={handleControlSeekBack}
            onSeekFwd={handleControlSeekFwd}
            onMarkIn={handleControlMarkIn}
            onMarkOut={handleControlMarkOut}
            onAddClip={handleControlAddClip}
            onFullscreen={handleControlFullscreen}
            onToggleLoop={handleToggleLoop}
            onGoLive={handleGoLive}
            zoomLevel={timelineZoom}
            onZoomChange={setTimelineZoom}
            onMarkerDrag={handleMarkerDrag}
          />
        </div>

        {/* 右侧面板 */}
        <div style={{ 
          width: 320,
          borderLeft: '1px solid var(--border-default)',
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
        }}>
          {/* 添加直播间 */}
          <Card 
            size="small" 
            title="添加直播间"
            style={{ 
              margin: 16, 
              marginBottom: 8,
              background: 'var(--bg-secondary)',
            }}
          >
            <Space.Compact style={{ width: '100%' }}>
              <Input
                placeholder="粘贴直播间链接..."
                value={url}
                onChange={e => setUrl(e.target.value)}
                onPressEnter={handleAddRoom}
              />
              <Button 
                type="primary" 
                icon={<PlusOutlined />}
                onClick={handleAddRoom}
                loading={loading}
              >
                添加
              </Button>
            </Space.Compact>
          </Card>

          {/* 录制设置 */}
          <RecordSettings />

          {/* 导出队列 */}
          <ExportQueue
            jobs={exportJobs}
            onCancel={handleCancelJob}
            onRetry={handleRetryJob}
            onRemove={handleRemoveJob}
            onOpenFolder={handleOpenExportFolder}
            onClearCompleted={handleClearCompleted}
          />

          {/* 切片列表 */}
          <ClipList
            clips={clips}
            onDelete={handleDeleteClip}
            onExport={handleExportClip}
          />
        </div>
      </div>

      {/* 最近切片栏 */}
      {recentClips.length > 0 && (
        <div style={{
          padding: '12px 24px',
          background: 'var(--bg-secondary)',
          borderTop: '1px solid var(--border-default)',
          flexShrink: 0,
          maxHeight: 120,
          overflow: 'hidden',
        }}>
          <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 10 }}>最近切片</div>
          <div
            role="list"
            style={{
              display: 'flex',
              gap: 12,
              overflowX: 'auto',
              paddingBottom: 4,
            }}
          >
            {recentClips.map((clip, index) => {
              const room = rooms.find(r => r.room_id === clip.room_id)
              const roomName = clip.room_name || room?.streamer_name || '未知房间'
              return (
                <div
                  key={`${clip.room_id}-${clip.start}-${index}`}
                  role="listitem"
                  tabIndex={0}
                  onClick={() => handleRecentClipClick(clip)}
                  onKeyDown={(e) => {
                    // Enter/Space 触发与点击相同的主操作
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault()
                      handleRecentClipClick(clip)
                    }
                  }}
                  style={{
                    minWidth: 180,
                    padding: '10px 12px',
                    background: 'var(--bg-primary)',
                    borderRadius: 8,
                    border: '1px solid var(--border-default)',
                    cursor: 'pointer',
                    flexShrink: 0,
                  }}
                >
                  <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 6, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {roomName}
                  </div>
                  <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                    {formatTime(clip.start)} - {formatTime(clip.end)}
                  </div>
                  <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginTop: 4 }}>
                    时长 {formatTime(clip.end - clip.start)}
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* 导出预览弹窗 */}
      <Modal
        title="导出切片预览"
        open={!!previewClip}
        onCancel={handleCancelExport}
        footer={[
          <Button key="cancel" onClick={handleCancelExport}>取消</Button>,
          <Button key="export" type="primary" onClick={handleConfirmExport}>确认导出</Button>,
        ]}
      >
        {previewClip && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div>
              <strong>房间：</strong>
              {previewClip.room_name || rooms.find(r => r.room_id === previewClip.room_id)?.streamer_name || '未知房间'}
            </div>
            <div><strong>入点：</strong>{formatTime(previewClip.start)}</div>
            <div><strong>出点：</strong>{formatTime(previewClip.end)}</div>
            <div><strong>时长：</strong>{formatTime(previewClip.end - previewClip.start)}</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <strong>导出预设：</strong>
              <Select
                value={exportPresetId}
                onChange={setExportPresetId}
                style={{ flex: 1, fontSize: 13 }}
                options={EXPORT_PRESETS.map(p => ({
                  value: p.id,
                  label: `${p.name} — ${p.description}`,
                }))}
              />
            </div>
          </div>
        )}
      </Modal>

      {/* 分析高光结果 Modal */}
      <Modal
        title="高光分析结果"
        open={showAnalysisModal}
        onCancel={() => setShowAnalysisModal(false)}
        footer={[
          <Button key="cancel" onClick={() => setShowAnalysisModal(false)}>关闭</Button>,
          <Button key="import" type="primary" onClick={handleImportAnalysis} disabled={analysisResults.length === 0}>
            导入到切片列表 ({analysisResults.length} 个)
          </Button>,
        ]}
        width={480}
      >
        {analysisResults.length === 0 ? (
          <div style={{ textAlign: 'center', padding: 24, color: 'var(--text-tertiary)' }}>
            未检测到高光片段，可能需要调整检测阈值或该视频场景变化较少。
          </div>
        ) : (
          <div style={{ maxHeight: 400, overflow: 'auto' }}>
            {analysisResults.map((h, i) => (
              <div
                key={i}
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  padding: '8px 12px',
                  borderRadius: 6,
                  background: i % 2 === 0 ? 'var(--bg-tertiary)' : 'transparent',
                  marginBottom: 4,
                }}
              >
                <span style={{ fontSize: 13, fontWeight: 500 }}>
                  高光 {i + 1}
                </span>
                <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                  {formatTime(h.start)} → {formatTime(h.end)}
                </span>
                <span style={{
                  fontSize: 11,
                  padding: '1px 6px',
                  borderRadius: 4,
                  background: h.score > 0.7 ? 'rgba(52,199,89,0.15)' : 'rgba(142,142,147,0.15)',
                  color: h.score > 0.7 ? '#34c759' : 'var(--text-tertiary)',
                }}>
                  {formatTime(h.end - h.start)}
                </span>
              </div>
            ))}
          </div>
        )}
      </Modal>
    </div>
  )
}
