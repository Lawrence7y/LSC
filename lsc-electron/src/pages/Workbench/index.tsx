import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { Row, Col, Card, Input, Button, Space, message, Empty, Modal, Tooltip, Select, Alert, Radio, Switch } from 'antd'
import { PlusOutlined, VideoCameraOutlined, SoundOutlined, MutedOutlined, SyncOutlined, SettingOutlined } from '@ant-design/icons'
import { useWebSocket } from '@/hooks/useWebSocket'
import { useAppStore } from '@/store/appStore'
import { useKeyboardShortcuts } from '@/hooks/useKeyboardShortcuts'
import { RoomCard } from './components/RoomCard'
import { ControlBar } from './components/ControlBar'
import { ClipList, type ExportProgressInfo } from './components/ClipList'
import { RefreshButton } from './components/RefreshButton'
import { ClipSegment, ContinuousAnalysisStatus } from '@/types'
import { EXPORT_PRESETS, getDefaultPreset } from '@/services/exportPresets'
import { formatTime } from '@/utils/time'
import { getAligner, type PreviewAudioCaptureDiagnostics } from '@/utils/previewAudioAligner'
import { AnalysisProgress } from '@/components/AnalysisProgress'
/** 分析模式 */
type AnalysisMode = 'valorant_round' | 'generic'

/** 高光段数据（支持 AI 分析扩展字段） */
interface Highlight {
  start: number
  end: number
  score: number
  reason?: string
  speech_score?: number
  visual_score?: number
  transcript?: string
}

type CaptureFailure = {
  roomId: string
  reason: string
  diagnostics?: PreviewAudioCaptureDiagnostics | null
}

function isApproximateClip(c: ClipSegment): boolean {
  return (
    c.mark_precision === 'approximate' ||
    (c.mark_precision !== 'exact' &&
      (c.mark_in_wallclock == null || c.mark_out_wallclock == null))
  )
}

/** 会停止录制的危险操作统一二次确认 */
function confirmStopRecording(title: string, content: string, onOk: () => void) {
  Modal.confirm({
    title,
    content,
    okText: '确认',
    okButtonProps: { danger: true },
    cancelText: '取消',
    onOk,
  })
}

function formatPreviewDegradationLabel(width: number, height: number, fps?: number): string {
  let label: string
  if (height === 360 || height === 480 || height === 720) {
    label = `${height}p`
  } else if (width > 0 && height > 0) {
    label = `${width}×${height}`
  } else {
    label = '较低画质'
  }
  if (fps && fps > 0) label += `@${fps}fps`
  return label
}

function formatCaptureFailureSummary(failures: CaptureFailure[]): string {
  if (failures.length === 0) return '原因未知'
  const labels: Record<string, string> = {
    no_video: '无预览播放器',
    worklet_not_loaded: '音频 Worklet 未加载',
    capture_stream_unavailable: '浏览器不支持音频捕获',
    no_audio_track: '无音轨',
    buffer_empty: 'buffer 空',
    silent: '静音或音量过低',
    timeout: '捕获超时',
    capture_exception: '捕获异常',
  }
  return failures
    .map(failure => {
      const label = labels[failure.reason] ?? failure.reason
      const sampleCount = failure.diagnostics?.sample_count
      const rms = failure.diagnostics?.rms
      const suffix = sampleCount !== undefined
        ? ` samples=${sampleCount}${typeof rms === 'number' ? ` rms=${rms.toFixed(5)}` : ''}`
        : ''
      return `${failure.roomId}:${label}${suffix}`
    })
    .join('；')
}

export default function Workbench() {
  const { isConnected, send, on } = useWebSocket()
  const rooms = useAppStore((state) => state.rooms)
  const selectedRoomId = useAppStore((state) => state.selectedRoomId)
  const connectionStatus = useAppStore((state) => state.connectionStatus)
  const settings = useAppStore((state) => state.settings)
  const appSettings = useAppStore((state) => state.appSettings)
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
  const continuousAnalysisStatus = useAppStore((state) => state.continuousAnalysisStatus)
  const previewDegradationBanner = useAppStore((state) => state.previewDegradationBanner)
  const dismissPreviewDegradationBanner = useAppStore((state) => state.dismissPreviewDegradationBanner)
  const setSelectedRoomId = useAppStore((state) => state.setSelectedRoomId)
  const addClip = useAppStore((state) => state.addClip)
  const setClips = useAppStore((state) => state.setClips)
  const setContinuousAnalysisStatus = useAppStore((state) => state.setContinuousAnalysisStatus)
  const [loading, setLoading] = useState(false)
  const [url, setUrl] = useState('')
  const [previewClip, setPreviewClip] = useState<ClipSegment | null>(null)
  const [exportPresetId, setExportPresetId] = useState(appSettings.default_export_preset || getDefaultPreset().id)
  // 分析导出 Modal 状态（持续分析 + 同步分析导出合并）
  const [continuousModalOpen, setContinuousModalOpen] = useState(false)
  const [continuousMainRoom, setContinuousMainRoom] = useState<string | null>(null)

  const [continuousPresetId, setContinuousPresetId] = useState(appSettings.default_export_preset || getDefaultPreset().id)
  const [analysisIsContinuous, setAnalysisIsContinuous] = useState(false)
  const [continuousSubmitting, setContinuousSubmitting] = useState(false)
  // 运行中的持续分析状态
  const [continuousAnalyzing, setContinuousAnalyzing] = useState(false)
  const [continuousRoomId, setContinuousRoomId] = useState<string | null>(null)
  const [continuousTargetRoomIds, setContinuousTargetRoomIds] = useState<string[]>([])
  const [analysisGameType, setAnalysisGameType] = useState<AnalysisMode>('generic')
  const isValorantRoundCutting = analysisGameType === 'valorant_round'
  const continuousActiveRoomRef = useRef<string | null>(null)
  // 同步导出模式标记（response 监听器据此预创建 clips 关联 job_id）
  const isSyncExportModeRef = useRef(false)
  const syncMainRoomRef = useRef<string | null>(null)
  const syncTargetRoomIdsRef = useRef<string[]>([])
  const [loopPreview, setLoopPreview] = useState(false)
  const [timelineZoom, setTimelineZoom] = useState(1)
  const [allMuted, setAllMuted] = useState(false)
  const [sortBy, setSortBy] = useState<string>('default')
  const [aligning, setAligning] = useState(false)
  const [exportProgressMap, setExportProgressMap] = useState<Record<string, ExportProgressInfo>>({})
  const aligningRoomIdsRef = useRef<Set<string>>(new Set())
  const alignButtonRef = useRef<HTMLButtonElement | null>(null)
  const loopTimerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  // 长按刷新：由 RefreshButton 组件内部管理粒子动效
  const [fullscreenRoomId, setFullscreenRoomId] = useState<string | null>(null)
  // 两级放大：第一级为“区域放大”（填满左侧面板），第二级为“全屏”（fixed 覆盖视口）
  const [expandedRoomId, setExpandedRoomId] = useState<string | null>(null)
  const [selectedRoomIds, setSelectedRoomIds] = useState<Set<string>>(new Set())
  const selectedRoomIdsRef = useRef<Set<string>>(new Set())
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
    }, 200)  // S4: 从 500ms 提升到 200ms，时间线播放头更平滑
    return () => clearInterval(id)
  }, [])

  // Escape 键退出放大（capture 阶段拦截，避免触发其他快捷键如 mark_in/out）
  useEffect(() => {
    if (expandedRoomId === null && fullscreenRoomId === null) return
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        e.stopPropagation()
        if (fullscreenRoomId !== null) {
          setFullscreenRoomId(null)
        } else {
          setExpandedRoomId(null)
        }
      }
    }
    window.addEventListener('keydown', handleKeyDown, true)
    return () => window.removeEventListener('keydown', handleKeyDown, true)
  }, [expandedRoomId, fullscreenRoomId])

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

  // Sync store's selectedRoomId with multi-select state
  useEffect(() => {
    selectedRoomIdsRef.current = selectedRoomIds
    setSelectedRoomId(selectedRoomIds.size === 1 ? [...selectedRoomIds][0] : null)
  }, [selectedRoomIds, setSelectedRoomId])

  // 反向同步：当 store 的 selectedRoomId 变化（例如从 Dashboard 跳转过来）且不在多选集合中时，
  // 将其同步到 selectedRoomIds，确保 Workbench 选中该房间。
  // 注意：不依赖 selectedRoomIds，改用 ref 读取，避免取消勾选时 effect 误触发导致循环。
  useEffect(() => {
    if (selectedRoomId && !selectedRoomIdsRef.current.has(selectedRoomId)) {
      setSelectedRoomIds(new Set([selectedRoomId]))
    }
  }, [selectedRoomId])

  // 获取房间列表 + 查询当前是否有正在运行的持续分析任务
  useEffect(() => {
    if (isConnected) {
      send('get_rooms', {})
      send('get_continuous_analysis_status', {})
    }
  }, [isConnected, send])

  // 轮询补偿：每 5s 查询持续分析状态，防止后端广播遗漏导致 UI 冻结
  useEffect(() => {
    if (!isConnected || !continuousAnalyzing) return
    const timer = setInterval(() => {
      send('get_continuous_analysis_status', {})
    }, 5000)
    return () => clearInterval(timer)
  }, [isConnected, continuousAnalyzing, send])

  // 监听后端事件（仅用于界面反馈，状态由 useWebSocket 统一写入 store）
  useEffect(() => {
    const unsubs: (() => void)[] = []
    const applyContinuousStatus = (data: ContinuousAnalysisStatus) => {
      const previous = useAppStore.getState().continuousAnalysisStatus
      const merged = previous?.room_id === data?.room_id
        ? { ...previous, ...data }
        : data
      setContinuousAnalysisStatus(merged)
      if (data?.running && data?.room_id) {
        setContinuousAnalyzing(true)
        setContinuousRoomId(data.room_id)
        continuousActiveRoomRef.current = data.room_id
        if (Array.isArray(data.target_room_ids)) {
          setContinuousTargetRoomIds(data.target_room_ids)
        }
      } else {
        setContinuousAnalyzing(false)
        setContinuousRoomId(null)
        continuousActiveRoomRef.current = null
        setContinuousTargetRoomIds([])
      }
    }
    unsubs.push(on('continuous_analysis_status', applyContinuousStatus))
    unsubs.push(on('get_continuous_analysis_status_response', applyContinuousStatus))
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
    // 仅 accepted=false / success=false+error 时回滚乐观 is_connecting；
    // async 受理成功不得 toast「连接成功」，真正结果等 room_connect_finished
    unsubs.push(on('connect_room_response', (data: {
      success?: boolean
      accepted?: boolean
      async?: boolean
      error?: string
      room_id?: string
    }) => {
      const rejected = data?.accepted === false || (data?.success === false && !!data?.error)
      if (data?.room_id && rejected) {
        useAppStore.getState().updateRoom(data.room_id, { is_connecting: false })
        if (data.error) {
          message.error(`连接失败：${data.error}`)
        }
      }
    }))
    // 单房间录制响应（后端返回 start_recording_response，不是 recording_started）
    unsubs.push(on('start_recording_response', (data: { success?: boolean; error?: string; room_id?: string }) => {
      if (data?.room_id) {
        useAppStore.getState().updateRoom(data.room_id, { is_recording_starting: false })
      }
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
    unsubs.push(on('export_progress', (data: any) => {
      if (data?.job_id && typeof data.percent === 'number') {
        const progressStore = useAppStore.getState()
        progressStore.setClips(progressStore.clips.map(c => c.job_id === data.job_id ? { ...c, export_status: 'exporting' as const } : c))
        setExportProgressMap(prev => ({
          ...prev,
          [data.job_id]: {
            percent: data.percent,
            elapsed: data.elapsed ?? 0,
            total: data.total ?? 0,
          },
        }))
      }
    }))
    unsubs.push(on('clip_completed', (data: any) => {
      if (data?.job_id) {
        const store = useAppStore.getState()
        const updatedClips = store.clips.map(c =>
          c.job_id === data.job_id
            ? { ...c, exported: true, outputPath: data.output_path, export_status: 'completed' as const, export_error: undefined }
            : c
        )
        store.setClips(updatedClips)
        setExportProgressMap(prev => {
          if (!prev[data.job_id]) return prev
          const next = { ...prev }
          delete next[data.job_id]
          return next
        })
        message.success('切片导出完成')
      }
    }))
    // 导出失败/取消：后端通过 clip_failed 通知
    unsubs.push(on('clip_failed', (data: { room_id?: string; job_id?: string; error?: string }) => {
      const isCancelled = data.error === '导出已取消'
      if (!isCancelled && data.error) {
        message.error(`导出失败：${data.error}`)
      } else if (!isCancelled && !data.error) {
        message.error('导出失败：未知错误（后端未返回原因，请查看日志）')
      }
      if (data?.job_id) {
        const jid = data.job_id
        setExportProgressMap(prev => {
          if (!prev[jid]) return prev
          const next = { ...prev }
          delete next[jid]
          return next
        })
        const store = useAppStore.getState()
        const updatedClips = store.clips.map(c =>
          c.job_id === jid
            ? { ...c, export_status: 'failed' as const, export_error: data.error || '导出失败' }
            : c
        )
        store.setClips(updatedClips)
      }
    }))
    // export_clip 提交响应：失败时立即提示
    unsubs.push(on('export_clip_response', (data: { success?: boolean; error?: string; job_id?: string }) => {
      if (data?.job_id && data?.success === false) {
        message.error(`导出失败：${data.error || '未知错误'}`)
        setExportProgressMap(prev => {
          if (!prev[data.job_id!]) return prev
          const next = { ...prev }
          delete next[data.job_id!]
          return next
        })
      }
    }))
    // cancel_export 响应：后端确认取消后置 cancelled，失败时提示用户
    unsubs.push(on('cancel_export_response', (data: { success?: boolean; error?: string; job_id?: string }) => {
      if (data?.success === false) {
        message.warning(`取消导出失败：${data.error || '任务可能已结束'}`)
      }
    }))
    return () => unsubs.forEach(u => u())
  }, [on])

  // 添加房间
  const handleAddRoom = async () => {
    console.log('[Workbench] 用户操作: 添加房间, url:', url);
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

  // 连接房间（乐观更新 is_connecting，避免等后端广播才出现 loading）
  const handleConnect = useCallback((roomId: string) => {
    console.log('[Workbench] 用户操作: 连接房间, roomId:', roomId);
    useAppStore.getState().updateRoom(roomId, { is_connecting: true, last_error: '' })
    send('connect_room', { room_id: roomId })
  }, [send])

  // 断开房间：先停止录制/预览/分析，再断开连接（录制中须二次确认）
  const handleDisconnect = useCallback((roomId: string) => {
    console.log('[Workbench] 用户操作: 断开房间连接, roomId:', roomId);
    const doDisconnect = () => {
      const room = useAppStore.getState().rooms.find(r => r.room_id === roomId)
      if (room?.is_recording) {
        send('stop_recording', { room_id: roomId })
      }
      if (room?.preview_enabled) {
        send('enable_preview', { room_id: roomId, enabled: false, mode: 'mse' })
      }
      // 如果该房间正在被持续分析，停止它
      const continuousStatus = useAppStore.getState().continuousAnalysisStatus
      if (continuousStatus?.running && continuousStatus.room_id === roomId) {
        send('stop_continuous_analysis', { main_room_id: roomId })
      }
      // 最后断开连接
      send('disconnect_room', { room_id: roomId })
    }
    const room = useAppStore.getState().rooms.find(r => r.room_id === roomId)
    if (room?.is_recording) {
      Modal.confirm({
        title: '确认断开',
        content: `断开将停止录制「${room.streamer_name || '未知主播'}」`,
        okText: '确认',
        okButtonProps: { danger: true },
        cancelText: '取消',
        onOk: doDisconnect,
      })
      return
    }
    doDisconnect()
  }, [send])

  // 切换静音（乐观写 store，与 VideoPreview 一致，避免 stale rooms_updated 打回图标）
  const handleToggleMute = useCallback((roomId: string) => {
    const room = useAppStore.getState().rooms.find((r) => r.room_id === roomId)
    if (!room) return
    const newMuted = !room.preview_muted
    console.log('[Workbench] 用户操作: 切换静音状态, roomId:', roomId, 'newMuted:', newMuted);
    useAppStore.getState().updateRoom(roomId, { preview_muted: newMuted })
    // 取消静音时显式 resume AudioContext，确保 Web Audio 路由有输出
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

  // 开始录制（乐观显示 loading，避免等 FFmpeg 启动完成才有反馈）
  const handleStartRecord = useCallback((roomId: string) => {
    console.log('[Workbench] 用户操作: 开始录制, roomId:', roomId);
    useAppStore.getState().updateRoom(roomId, { is_recording_starting: true, last_error: '' })
    send('start_recording', { room_id: roomId })
  }, [send])

  // 停止录制
  const handleStopRecord = useCallback((roomId: string) => {
    console.log('[Workbench] 用户操作: 停止录制, roomId:', roomId);
    send('stop_recording', { room_id: roomId })
  }, [send])

  // 启用/停止预览（Electron 模式：后端 FFmpeg 抓帧推送）
  const handleTogglePreview = useCallback((roomId: string, enabled: boolean) => {
    console.log('[Workbench] 用户操作: 切换预览状态, roomId:', roomId, 'enabled:', enabled);
    send('enable_preview', { room_id: roomId, enabled, mode: 'mse' })
  }, [send])

  // 放大预览（两级：第一次进入左侧区域放大，第二次进入真正全屏）
  const handleFullscreen = useCallback((roomId: string) => {
    if (fullscreenRoomId === roomId) {
      setFullscreenRoomId(null)
      setExpandedRoomId(roomId)
      return
    }
    if (expandedRoomId === roomId) {
      setFullscreenRoomId(roomId)
      return
    }
    setExpandedRoomId(roomId)
    setFullscreenRoomId(null)
  }, [expandedRoomId, fullscreenRoomId])

  const handleCollapse = useCallback((roomId: string) => {
    if (fullscreenRoomId === roomId) {
      setFullscreenRoomId(null)
      return
    }
    if (expandedRoomId === roomId) {
      setExpandedRoomId(null)
    }
  }, [expandedRoomId, fullscreenRoomId])

  const handleExitFullscreen = useCallback((roomId: string) => {
    if (fullscreenRoomId === roomId) {
      setFullscreenRoomId(null)
      setExpandedRoomId(roomId)
    }
  }, [fullscreenRoomId])

  // 删除房间
  const handleRemove = useCallback((roomId: string) => {
    console.log('[Workbench] 用户操作: 删除房间, roomId:', roomId);
    // 项目记忆硬约束：“Fullscreen preview must be exited before deleting a
    // room to avoid crashes”。若该房间正处于全屏预览，先退出全屏再删除，
    // 避免 VideoPreview 组件实例继续渲染已删除房间的 MSE streamer 导致崩溃。
    if (fullscreenRoomId === roomId) {
      setFullscreenRoomId(null)
    }
    if (expandedRoomId === roomId) {
      setExpandedRoomId(null)
    }
    pendingRoomSavesRef.current += 1
    send('remove_room', { room_id: roomId })
    setSelectedRoomIds(prev => {
      const next = new Set(prev)
      next.delete(roomId)
      return next
    })
  }, [send, fullscreenRoomId, expandedRoomId])

  // 选择房间（支持 Ctrl/Shift 多选）
  const handleSelect = useCallback((roomId: string, e: React.MouseEvent) => {
    console.log('[Workbench] 用户操作: 选择/切换选中状态, roomId:', roomId);
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

  // Checkbox 多选切换（无需 Ctrl 键，点击即切换选中/取消）
  const handleToggleMultiSelect = useCallback((roomId: string, e: React.MouseEvent) => {
    const currentRooms = useAppStore.getState().rooms
    const roomIndex = currentRooms.findIndex(r => r.room_id === roomId)

    if (e.shiftKey && lastClickedIndex !== null) {
      // Shift+Click on checkbox: range selection
      const start = Math.min(lastClickedIndex, roomIndex)
      const end = Math.max(lastClickedIndex, roomIndex)
      const rangeIds = currentRooms.slice(start, end + 1).map(r => r.room_id)
      setSelectedRoomIds(prev => {
        const next = new Set(prev)
        rangeIds.forEach(id => next.add(id))
        return next
      })
    } else {
      // Normal click on checkbox: toggle selection
      setSelectedRoomIds(prev => {
        const next = new Set(prev)
        if (next.has(roomId)) {
          next.delete(roomId)
        } else {
          next.add(roomId)
        }
        return next
      })
    }
    setLastClickedIndex(roomIndex)
  }, [lastClickedIndex])

  // 批量录制（绑定快捷键 Ctrl+R，需二次确认以防误触）
  const handleBatchRecord = useCallback(() => {
    const connectableRooms = useAppStore.getState().rooms.filter(r => r.is_connected && !r.is_recording)
    if (connectableRooms.length === 0) {
      message.info('没有可录制的房间')
      return
    }
    console.log('[Workbench] 用户操作: 批量录制, 房间数:', connectableRooms.length)
    Modal.confirm({
      title: '确认批量录制',
      content: `将开始录制 ${connectableRooms.length} 个房间`,
      okText: '确认录制',
      cancelText: '取消',
      onOk: () => {
        connectableRooms.forEach(r => {
          useAppStore.getState().updateRoom(r.room_id, { is_recording_starting: true, last_error: '' })
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
    console.log('[Workbench] 用户操作: 批量停止, 房间数:', recordingRooms.length)
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
    console.log('[Workbench] MSE seek:', roomId, 'time:', time.toFixed(2))
    const registry = (window as any).__msePlayers
    const video = registry?.[roomId]?.player?.videoElement as HTMLVideoElement | undefined
    if (video) {
      if (video.buffered.length > 0) {
        const bufStart = video.buffered.start(0)
        const bufEnd = video.buffered.end(video.buffered.length - 1)
        if (time >= bufStart && time <= bufEnd) {
          try { video.currentTime = time } catch {}
        } else {
          video.currentTime = Math.max(bufStart, bufEnd - 0.5)
          video.play().catch(() => {})
        }
      }
    }
    send('seek', { room_id: roomId, time })
  }, [send])

  const mseTogglePlayPause = useCallback((roomId: string) => {
    console.log('[Workbench] 用户操作: 播放/暂停切换, roomId:', roomId)
    const registry = (window as any).__msePlayers
    const video = registry?.[roomId]?.player?.videoElement as HTMLVideoElement | undefined
    if (video) {
      if (video.paused) video.play().catch(() => {})
      else video.pause()
    }
    send('toggle_play_pause', { room_id: roomId })
  }, [send])

  // 时间线跳转（多选时按 content_offset 调整每房间 seek 位置）
  const handleTimelineSeek = useCallback((time: number) => {
    console.log('[Workbench] 用户操作: 时间线跳转, time:', time.toFixed(2), '房间数:', selectedRoomIds.size)
    selectedRoomIds.forEach(rid => {
      const room = rooms.find(r => r.room_id === rid)
      const offset = room?.content_offset ?? 0
      mseSeek(rid, time - offset)
    })
  }, [selectedRoomIds, mseSeek, rooms])

  // 设置入点
  const handleMarkIn = useCallback((roomId: string) => {
    const time = getPreviewCurrentTime(roomId)
    console.log('[Workbench] 用户操作: 设置入点, roomId:', roomId, 'time:', time.toFixed(2))
    send('set_mark_in', { room_id: roomId, time, live: true })
  }, [send, getPreviewCurrentTime])

  // 设置出点
  const handleMarkOut = useCallback((roomId: string) => {
    const time = getPreviewCurrentTime(roomId)
    console.log('[Workbench] 用户操作: 设置出点, roomId:', roomId, 'time:', time.toFixed(2))
    send('set_mark_out', { room_id: roomId, time, live: true })
  }, [send, getPreviewCurrentTime])

  // 添加到切片列表
  const handleAddClip = useCallback((roomId: string) => {
    const currentRooms = useAppStore.getState().rooms
    const currentClips = useAppStore.getState().clips
    const room = currentRooms.find(r => r.room_id === roomId)
    if (!room?.record_output_path) {
      message.warning('请先开始录制后再添加切片')
      return
    }
    if (room && room.mark_in !== null && room.mark_out !== null) {
      console.log('[Workbench] 用户操作: 添加切片, roomId:', roomId, 'mark_in:', room.mark_in, 'mark_out:', room.mark_out)
      const newClip: ClipSegment = {
        start: room.mark_in,
        end: room.mark_out,
        label: `${room.streamer_name} - 片段 ${currentClips.length + 1}`,
        room_id: roomId,
        mark_in_wallclock: room.mark_in_wallclock ?? null,
        mark_out_wallclock: room.mark_out_wallclock ?? null,
        recording_start_mono: room.recording_start_mono ?? null,
        recording_media_start_mono: room.recording_media_start_mono ?? null,
        content_offset: room.content_offset ?? 0,
        mark_precision:
          room.mark_in_wallclock != null &&
          room.mark_out_wallclock != null &&
          (room.recording_media_start_mono ?? room.recording_start_mono) != null
            ? 'exact'
            : 'approximate',
      }
      addClip(newClip)
      message.success('已添加到切片列表')
    } else {
      message.warning('请先设置入点和出点')
    }
  }, [addClip])

  // ── 稳定的 ControlBar / RoomCard 回调 ──

  const handleControlPlayPause = useCallback(() => {
    console.log('[Workbench] 用户操作: 控制栏播放/暂停, 房间数:', selectedRoomIds.size)
    selectedRoomIds.forEach(rid => mseTogglePlayPause(rid))
  }, [selectedRoomIds, mseTogglePlayPause])

  const handleControlSeekBack = useCallback(() => {
    console.log('[Workbench] 用户操作: 后退10秒, 房间数:', selectedRoomIds.size)
    selectedRoomIds.forEach(rid => {
      const cur = getPreviewCurrentTime(rid)
      mseSeek(rid, Math.max(0, cur - 10))
    })
  }, [selectedRoomIds, getPreviewCurrentTime, mseSeek])

  const handleControlSeekFwd = useCallback(() => {
    console.log('[Workbench] 用户操作: 前进10秒, 房间数:', selectedRoomIds.size)
    selectedRoomIds.forEach(rid => {
      const cur = getPreviewCurrentTime(rid)
      mseSeek(rid, cur + 10)
    })
  }, [selectedRoomIds, getPreviewCurrentTime, mseSeek])

  const handleControlMarkIn = useCallback(() => {
    console.log('[Workbench] 用户操作: 控制栏设置入点, 房间数:', selectedRoomIds.size)
    selectedRoomIds.forEach(rid => {
      const time = getPreviewCurrentTime(rid)
      send('set_mark_in', { room_id: rid, time })
    })
  }, [selectedRoomIds, send, getPreviewCurrentTime])

  const handleControlMarkOut = useCallback(() => {
    console.log('[Workbench] 用户操作: 控制栏设置出点, 房间数:', selectedRoomIds.size)
    selectedRoomIds.forEach(rid => {
      const time = getPreviewCurrentTime(rid)
      send('set_mark_out', { room_id: rid, time })
    })
  }, [selectedRoomIds, send, getPreviewCurrentTime])

  const handleControlAddClip = useCallback(() => {
    if (selectedRoomIds.size === 0) return
    console.log('[Workbench] 用户操作: 控制栏添加切片, 房间数:', selectedRoomIds.size)
    selectedRoomIds.forEach(rid => handleAddClip(rid))
  }, [selectedRoomIds, handleAddClip])

  const handleGoLive = useCallback(() => {
    console.log('[Workbench] 用户操作: 跳转到直播最新位置, 房间数:', selectedRoomIds.size)
    if (selectedRoomIds.size === 0) {
      console.warn('[Workbench] 直播按钮诊断: 未选中房间')
      return
    }
    const registry = (window as any).__msePlayers
    selectedRoomIds.forEach(rid => {
      const entry = registry?.[rid]
      const player = entry?.player
      const video = player?.videoElement as HTMLVideoElement | undefined
      const bufferedLength = video?.buffered?.length ?? 0
      const bufferedStart = bufferedLength > 0 ? video!.buffered.start(0) : null
      const bufferedEnd = bufferedLength > 0 ? video!.buffered.end(bufferedLength - 1) : null
      console.log('[Workbench] 直播按钮诊断', {
        roomId: rid,
        hasPlayer: !!player,
        hasVideo: !!video,
        readyState: video?.readyState ?? null,
        currentTime: video?.currentTime ?? null,
        bufferedStart,
        bufferedEnd,
      })
      if (player && typeof player.goLive === 'function') {
        // 使用 MSE player 的 goLive() 方法：
        // 1. 强制 seek 到缓冲区末尾
        // 2. 重置 live-edge 对齐标志，允许后续自动校准
        // 3. 触发 _tryPlay() 延迟重试机制
        player.goLive()
      } else {
        // 备用逻辑：player 不可用时直接操作 video 元素
        if (video && video.buffered.length > 0) {
          const bufEnd = video.buffered.end(video.buffered.length - 1)
          video.currentTime = Math.max(0, bufEnd - 0.5)
          video.play().catch(() => {})
        } else {
          console.warn('[Workbench] 直播按钮诊断: MSE player 不可用或 buffer 为空', { roomId: rid })
        }
      }
    })
  }, [selectedRoomIds])

  // Phase 3: 音频对齐结果监听器
  useEffect(() => {
    const unsub = on('align_preview_audio_response', (data: any) => {
      setAligning(false)
      message.destroy('align')
      if (!data?.success || !data?.offsets) {
        console.warn('[Workbench] 音频对齐失败:', data?.error)
        message.warning('未精确对齐：已仅做预览缓冲区对齐，导出可能不同步')
        return
      }
      const offsets = data.offsets as Record<string, number>
      const scores = (data.scores || {}) as Record<string, number>
      const referenceRoomId = data.reference_room_id as string
      const registry = (window as any).__msePlayers
      const alignmentTrustThreshold = 0.3
      let alignedCount = 0
      let lowConfidenceCount = 0
      let mutedCount = 0
      const trustedScores: number[] = []
      aligningRoomIdsRef.current.forEach(rid => {
        const offset = offsets[rid]
        if (offset === undefined) return
        const score = scores[rid] ?? 0
        if (score < alignmentTrustThreshold) {
          lowConfidenceCount++
          send('set_content_offset', { room_id: rid, offset: 0 })
          return
        }

        trustedScores.push(score)
        // 回传 content_offset 到后端（用于导出时补偿）
        send('set_content_offset', { room_id: rid, offset })

        if (offset < 0.05) { alignedCount++; return }
        const video = registry?.[rid]?.player?.videoElement as HTMLVideoElement | undefined
        if (video && video.buffered.length > 0) {
          const bufStart = video.buffered.start(0)
          const target = Math.max(bufStart, video.currentTime - offset)
          try { video.currentTime = target } catch {}
          video.play().catch(() => {})
          alignedCount++
        }
      })

      // 自动静音非参考房间（快的房间），消除多路音频叠加的回声/空灵感
      aligningRoomIdsRef.current.forEach(rid => {
        const offset = offsets[rid]
        const score = scores[rid] ?? 0
        if (offset !== undefined && score >= alignmentTrustThreshold && offset > 0.05 && rid !== referenceRoomId) {
          send('set_preview_muted', { room_id: rid, muted: true })
          mutedCount++
        }
      })

      const avgScore = trustedScores.length > 0
        ? trustedScores.reduce((a, b) => a + b, 0) / trustedScores.length
        : 0
      console.log(
        '[Workbench] 音频对齐完成: aligned=' + alignedCount
        + ', lowConfidence=' + lowConfidenceCount
        + ', avgScore=' + avgScore.toFixed(3),
      )
      const muteMsg = mutedCount > 0
        ? `，已静音 ${mutedCount} 个快房间（可手动取消静音）`
        : ''
      if (lowConfidenceCount > 0) {
        message.warning(
          `精确对齐 ${alignedCount} 路，${lowConfidenceCount} 路置信度不足已跳过${muteMsg}`,
        )
      } else {
        message.success(
          `已精确对齐 ${alignedCount} 个直播间（置信度 ${Math.round(avgScore * 100)}%）${muteMsg}`,
        )
      }
    })
    return () => unsub()
  }, [on, send])

  const handleAlignLive = useCallback(async () => {
    if (selectedRoomIds.size === 0) return
    console.log('[Workbench] 用户操作: 一键对齐, 房间数:', selectedRoomIds.size)
    const registry = (window as any).__msePlayers
    if (!registry) return

    // 智能跳过：所有房间已有缓存 offset 时跳过 Phase 1
    const allHaveOffset = [...selectedRoomIds].every(rid => {
      const room = rooms.find(r => r.room_id === rid)
      return room && (room.content_offset || 0) > 0.01
    })

    if (!allHaveOffset || selectedRoomIds.size < 2) {
      // Phase 1: 即时缓冲对齐
      let minBufferEnd = Infinity
      selectedRoomIds.forEach(rid => {
        const video = registry?.[rid]?.player?.videoElement as HTMLVideoElement | undefined
        if (video && video.buffered.length > 0) {
          const end = video.buffered.end(video.buffered.length - 1)
          if (end < minBufferEnd) minBufferEnd = end
        }
      })
      if (minBufferEnd === Infinity) return
      const targetTime = Math.max(0, minBufferEnd - 1)
      selectedRoomIds.forEach(rid => {
        const video = registry?.[rid]?.player?.videoElement as HTMLVideoElement | undefined
        if (video) {
          try { video.currentTime = targetTime } catch {}
          video.play().catch(() => {})
        }
      })
      // 等待 seek 完成
      await Promise.all([...selectedRoomIds].map(rid => {
        const video = registry?.[rid]?.player?.videoElement as HTMLVideoElement | undefined
        if (!video) return Promise.resolve()
        return new Promise<void>(resolve => {
          if (Math.abs(video.currentTime - targetTime) < 0.5) { resolve(); return }
          const onSeeked = () => { video.removeEventListener('seeked', onSeeked); resolve() }
          video.addEventListener('seeked', onSeeked)
          setTimeout(resolve, 2000)
        })
      }))
    }

    // 少于 2 个房间时不需要音频对齐
    if (selectedRoomIds.size < 2) {
      message.info('已同步预览进度（单房间无需音频对齐）')
      return
    }
    message.loading({ content: '对齐中...', key: 'align', duration: 0 })

    // Phase 2: 并行音频捕获 + 后端 FFT 计算
    setAligning(true)
    aligningRoomIdsRef.current = new Set(selectedRoomIds)
    try {
      const aligner = getAligner()
      const previewAlignDuration = 8.0
      const captureFailures: CaptureFailure[] = []
      const capturePromises = [...selectedRoomIds].map(async rid => {
        const entry = registry?.[rid]
        const video = entry?.player?.videoElement as HTMLVideoElement | undefined
        if (!video) {
          captureFailures.push({ roomId: rid, reason: 'no_video' })
          return null
        }
        const pcm = await aligner.captureAudio(rid, video, previewAlignDuration)
        const captureDiagnostics = aligner.getLastCaptureDiagnostics(rid)
        if (!pcm) {
          captureFailures.push({
            roomId: rid,
            reason: captureDiagnostics?.reason ?? 'unknown',
            diagnostics: captureDiagnostics,
          })
          return null
        }
        const buffered = video.buffered
        return {
          room_id: rid,
          sample_rate: 16000,
          pcm_base64: aligner.base64Encode(pcm),
          diagnostics: {
            current_time: video.currentTime,
            buffer_start: buffered.length ? buffered.start(0) : null,
            buffer_end: buffered.length ? buffered.end(buffered.length - 1) : null,
            ingest_mode: entry?.ingestMode || 'unknown',
            ready_state: captureDiagnostics?.ready_state ?? video.readyState,
            has_audio_track: captureDiagnostics?.has_audio_track ?? true,
            rms: captureDiagnostics?.rms ?? null,
            sample_count: captureDiagnostics?.sample_count ?? pcm.length,
            capture_reason: captureDiagnostics?.reason ?? 'ok',
          },
        }
      })
      const results = (await Promise.all(capturePromises)).filter((r): r is {
        room_id: string
        sample_rate: number
        pcm_base64: string
        diagnostics: {
          current_time: number
          buffer_start: number | null
          buffer_end: number | null
          ingest_mode: string
          ready_state: number
          has_audio_track: boolean
          rms: number | null
          sample_count: number
          capture_reason: string
        }
      } => r !== null)
      if (results.length < 2) {
        setAligning(false)
        message.destroy('align')
        const failureSummary = formatCaptureFailureSummary(captureFailures)
        console.warn('[Workbench] 音频捕获不足诊断', {
          selectedRooms: [...selectedRoomIds],
          capturedRooms: results.map(r => r.room_id),
          captureFailures,
          failureSummary,
        })
        message.warning('未精确对齐：已仅做预览缓冲区对齐，导出可能不同步')
        return
      }
      send('align_preview_audio', { rooms: results })
    } catch (err) {
      setAligning(false)
      message.destroy('align')
      console.error('[Workbench] 音频对齐异常:', err)
      message.warning('未精确对齐：已仅做预览缓冲区对齐，导出可能不同步')
    }
  }, [selectedRoomIds, send, rooms])

  const handleMarkerDragEnd = useCallback((type: 'in' | 'out', time: number) => {
    console.log('[Workbench] 用户操作: 标记拖拽结束, type:', type, 'time:', time.toFixed(2))
    selectedRoomIds.forEach(rid => {
      if (type === 'in') {
        send('set_mark_in', { room_id: rid, time, live: false })
      } else {
        send('set_mark_out', { room_id: rid, time, live: false })
      }
    })
    if (selectedRoomIds.size > 0) {
      mseSeek([...selectedRoomIds][0], time)
    }
    // 拖拽不写墙钟：近似定位，避免假精确；精确导出请用 I/O
    message.info('近似定位：拖拽标记可能偏差数秒，精确导出请用 I / O 键', 3)
  }, [selectedRoomIds, send, mseSeek])

  const handleDeleteMarker = useCallback((type: 'in' | 'out') => {
    console.log('[Workbench] 用户操作: 删除标记, type:', type)
    selectedRoomIds.forEach(rid => {
      if (type === 'in') {
        send('set_mark_in', { room_id: rid, time: null })
      } else {
        send('set_mark_out', { room_id: rid, time: null })
      }
    })
    message.info(type === 'in' ? '已删除入点' : '已删除出点')
  }, [selectedRoomIds, send])

  // 删除切片
  const handleDeleteClip = (index: number) => {
    console.log('[Workbench] 用户操作: 删除切片, index:', index)
    setClips(clips.filter((_, i) => i !== index))
  }

  // 导出切片
  const handleExportClip = (clip: ClipSegment, _index?: number) => {
    console.log('[Workbench] 用户操作: 导出切片, roomId:', clip.room_id, 'label:', clip.label)
    setPreviewClip(clip)
  }

  const handleExportMany = (targets: ClipSegment[]) => {
    if (targets.length === 0) return
    if (targets.length === 1) {
      handleExportClip(targets[0])
      return
    }
    const hasApproximate = targets.some(isApproximateClip)
    const store = useAppStore.getState()
    let queued = 0
    targets.forEach((clip, i) => {
      const room = store.rooms.find(r => r.room_id === clip.room_id)
      if (!room?.record_output_path) return
      const jobId = `export-${Date.now()}-${i}`
      send('export_clip', {
        room_id: clip.room_id,
        start: clip.start,
        end: clip.end,
        label: clip.label,
        preset_id: exportPresetId || useAppStore.getState().appSettings?.default_export_preset || '',
        job_id: jobId,
        source: clip.is_ai_highlight ? 'ai_highlight' : 'manual',
        mark_in_wallclock: clip.mark_in_wallclock,
        mark_out_wallclock: clip.mark_out_wallclock,
        recording_start_mono: clip.recording_start_mono,
        recording_media_start_mono: clip.recording_media_start_mono,
        content_offset: clip.content_offset,
        use_room_marks: false,
      })
      queued += 1
      store.setClips(useAppStore.getState().clips.map(c =>
        c.clip_id === clip.clip_id || (c.start === clip.start && c.end === clip.end && c.room_id === clip.room_id)
          ? { ...c, job_id: jobId, exported: false, export_status: 'queued' as const, export_error: undefined }
          : c
      ))
    })
    if (queued > 0) {
      if (hasApproximate) {
        message.warning(
          `含近似定位切片，导出时间可能偏差数秒；精确导出请用 I / O 键标记。已提交 ${queued} 个导出任务`,
        )
      } else {
        message.success(`已提交 ${queued} 个导出任务`)
      }
    } else {
      message.warning('没有可导出的切片（缺少录制文件）')
    }
  }

  // 打开导出的文件
  const handleOpenExportFile = (outputPath: string) => {
    console.log('[Workbench] 用户操作: 打开导出文件, path:', outputPath)
    if (window.electronAPI) {
      window.electronAPI.openPath(outputPath)
    }
  }

  const handleConfirmExport = () => {
    if (!previewClip) return
    console.log('[Workbench] 用户操作: 确认导出, roomId:', previewClip.room_id, 'start:', previewClip.start, 'end:', previewClip.end, 'preset:', exportPresetId)
    // 检查该房间是否有可用的录制文件
    const room = rooms.find(r => r.room_id === previewClip.room_id)
    if (!room) {
      message.error('房间不存在')
      return
    }
    if (!room.record_output_path) {
      message.error('该房间没有录制文件，请先开始录制再导出切片')
      return
    }
    const isApproximate = isApproximateClip(previewClip)
    const jobId = `export-${Date.now()}`

    send('export_clip', {
      room_id: previewClip.room_id,
      start: previewClip.start,
      end: previewClip.end,
      label: previewClip.label,
      preset_id: exportPresetId,
      job_id: jobId,
      source: previewClip.is_ai_highlight ? 'ai_highlight' : 'manual',
      mark_in_wallclock: previewClip.mark_in_wallclock,
      mark_out_wallclock: previewClip.mark_out_wallclock,
      recording_start_mono: previewClip.recording_start_mono,
      recording_media_start_mono: previewClip.recording_media_start_mono,
      content_offset: previewClip.content_offset,
      use_room_marks: false,
    })
    // 将 job_id 写入对应 clip，使 ClipList 能关联导出进度
    const store = useAppStore.getState()
    store.setClips(store.clips.map(c =>
      c.start === previewClip.start && c.end === previewClip.end && c.room_id === previewClip.room_id
        ? { ...c, job_id: jobId, exported: false, export_status: 'queued', export_error: undefined }
        : c
    ))
    setPreviewClip(null)
    if (isApproximate) {
      message.warning(
        '该切片为近似定位，导出时间可能偏差数秒；精确导出请用 I / O 键标记。导出任务已提交',
      )
    } else {
      message.info('导出任务已提交')
    }
  }

  const handleCancelExportModal = () => {
    setPreviewClip(null)
  }

  const handleCancelExport = useCallback((jobId: string) => {
    send('cancel_export', { job_id: jobId })
  }, [send])

  // ── 选区试听（循环播放） ──
  // Use ref for loopPreview so the callback stays stable
  const loopPreviewRef = useRef(loopPreview)
  loopPreviewRef.current = loopPreview

  const handleToggleLoop = useCallback(() => {
    if (loopPreviewRef.current) {
      console.log('[Workbench] 用户操作: 停止循环试听')
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
    console.log('[Workbench] 用户操作: 开始循环试听, roomId:', currentSelectedId, 'mark_in:', room.mark_in, 'mark_out:', room.mark_out)

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
  // 同步分析导出启用条件：多选≥2 + 所有选中房间 align_group_id 一致非空 + 有录制文件
  const selectedRoomList = rooms.filter(r => selectedRoomIds.has(r.room_id))
  const currentTargetIds = selectedRoomIds.size > 0
    ? [...selectedRoomIds]
    : (selectedRoomId ? [selectedRoomId] : [])
  const currentTargetRoomList = rooms.filter(r => currentTargetIds.includes(r.room_id))
  const continuousTargetRooms = rooms.filter(r => continuousTargetRoomIds.includes(r.room_id))
  const targetHasRecordings = currentTargetRoomList.every(r => r.record_output_path)
  const targetAlignGroupReady = currentTargetRoomList.length <= 1 || (() => {
    const groups = new Set(currentTargetRoomList.map(r => r.align_group_id || ''))
    return groups.size === 1 && !groups.has('')
  })()
  const analysisEnabled = currentTargetRoomList.length >= 1
    && targetHasRecordings
    && targetAlignGroupReady
  const analysisNeedsAlign = currentTargetRoomList.length >= 1
    && targetHasRecordings
    && !targetAlignGroupReady
  const analysisTooltip = currentTargetRoomList.length < 1
    ? '请先选择要分析的房间'
    : !targetHasRecordings
      ? '选中房间需先有录制文件，请先开始录制'
      : !targetAlignGroupReady
        ? currentTargetRoomList.length > 1
          ? '多房间分析需先点击「一键对齐」，且各房间对齐组一致（仅缓冲区对齐不可用于分析）'
          : '请先点击一键对齐'
        : '分析主直播间高光，按对齐偏移映射导出'

  const scrollToAlignButton = useCallback(() => {
    alignButtonRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    message.info('请点击上方「一键对齐」完成多房间同步')
  }, [])

  // 分析导出确认（持续分析 / 同步分析导出 合并）
  const handleConfirmAnalysisExport = () => {
    if (!continuousMainRoom) {
      message.warning('请选择主直播间')
      return
    }
    const targetRoomIds = analysisIsContinuous ? continuousTargetRoomIds : [...selectedRoomIds]
    const targetRooms = rooms.filter(r => targetRoomIds.includes(r.room_id))
    if (targetRooms.length === 0) {
      message.warning('请先选择房间')
      return
    }
    if (!targetRooms.every(r => r.record_output_path)) {
      message.error('选中房间缺少录制文件')
      return
    }
    if (!targetRoomIds.includes(continuousMainRoom)) {
      message.error('主直播间必须在目标房间中')
      return
    }
    if (targetRooms.length > 1) {
      const groupSet = new Set(targetRooms.map(r => r.align_group_id || ''))
      if (groupSet.size !== 1 || groupSet.has('')) {
        message.error('多房间分析需要先一键对齐，且对齐组一致')
        return
      }
    }

    if (analysisIsContinuous) {
      send('start_continuous_analysis', {
        main_room_id: continuousMainRoom,
        target_room_ids: targetRoomIds,
        mode: isValorantRoundCutting ? 'valorant_round' : 'scene',
        interval: 20,
        threshold: 0.3,
        game: isValorantRoundCutting ? 'valorant' : 'generic',
      })
      setContinuousModalOpen(false)
      message.info('持续分析启动请求已发送')
    } else {
      if (targetRoomIds.length < 2) {
        message.warning('请至少选中 2 个房间（或开启持续分析）')
        return
      }
      const jobPrefix = `hlexport-${Date.now()}`
      isSyncExportModeRef.current = true
      syncMainRoomRef.current = continuousMainRoom
      syncTargetRoomIdsRef.current = [...targetRoomIds]
      setContinuousSubmitting(true)
      const analysisMode = isValorantRoundCutting ? 'valorant_round' : 'scene'
      console.log('[Workbench] 用户操作: 多房间同步分析导出, main:', continuousMainRoom,
        'targets:', targetRoomIds, 'mode:', analysisMode)
      send('start_analysis_export', {
        main_room_id: continuousMainRoom,
        target_room_ids: targetRoomIds,
        mode: analysisMode,
        threshold: 0.3,
        whisper_model: 'auto',
        weights: { audio: 0.45, visual: 0.35, scene: 0.20 },
        absolute_threshold: settings.analysis_settings?.absolute_threshold ?? 0.15,
        preset_id: continuousPresetId,
        job_prefix: jobPrefix,
        game: isValorantRoundCutting ? 'valorant' : 'generic',
      })
      setContinuousModalOpen(false)
      setContinuousSubmitting(false)
    }
  }

  // 监听分析结果与进度
  useEffect(() => {
    const unsubs: (() => void)[] = []
    // 同步分析导出响应：后端已自动提交导出，前端预创建 clips 关联 job_id
    unsubs.push(on('start_analysis_export_response', (data: any) => {
      if (data?.install_guide) {
        message.warning('AI 分析依赖未安装，请运行: pip install -r requirements-ai.txt')
        isSyncExportModeRef.current = false
        return
      }
      if (data?.success && data?.highlights) {
        // 预创建 clips，让 ClipList 显示导出进度（用 store 最新 rooms/ref 避免闭包旧值）
        if (isSyncExportModeRef.current && data.job_ids && Array.isArray(data.job_ids)) {
          const curRooms = useAppStore.getState().rooms
          const mainRoom = curRooms.find(r => r.room_id === syncMainRoomRef.current)
          const targetIds = syncTargetRoomIdsRef.current
          const newClips: ClipSegment[] = []
          data.highlights.forEach((h: Highlight, i: number) => {
            targetIds.forEach(rid => {
              const room = curRooms.find(r => r.room_id === rid)
              const delta = (mainRoom?.content_offset || 0) - (room?.content_offset || 0)
              const mappedStart = Math.max(0, h.start + delta)
              const mappedEnd = Math.max(0, h.end + delta)
              // job_id 格式：{job_prefix}-{i}-{rid}
              const jid = data.job_ids.find((id: string) => id.endsWith(`-${i}-${rid}`))
              if (jid) {
                newClips.push({
                  start: mappedStart,
                  end: mappedEnd,
                  label: `${room?.streamer_name || rid}_高光${i + 1}`,
                  room_id: rid,
                  room_name: room?.streamer_name,
                  job_id: jid,
                  exported: false,
                })
              }
            })
          })
          const store = useAppStore.getState()
          store.setClips([...store.clips, ...newClips])
          message.success(`已提交 ${data.job_ids.length} 个导出任务（${targetIds.length} 房间 × ${data.highlights.length} 高光）`)
        }
      } else {
        message.error(data?.error || '同步分析导出失败')
      }
      isSyncExportModeRef.current = false
      syncTargetRoomIdsRef.current = []
    }))
    unsubs.push(on('start_continuous_analysis_response', (data: any) => {
      if (data?.success) {
        const roomId = data.main_room_id || continuousMainRoom
        const targetRoomIds = Array.isArray(data.target_room_ids) ? data.target_room_ids : continuousTargetRoomIds
        setContinuousAnalyzing(true)
        setContinuousRoomId(roomId)
        continuousActiveRoomRef.current = roomId
        if (Array.isArray(data.target_room_ids)) {
          setContinuousTargetRoomIds(data.target_room_ids)
        }
        setContinuousAnalysisStatus({
          running: true,
          room_id: roomId,
          target_room_ids: targetRoomIds,
          mode: data.mode || 'scene',
          analyzed_duration: 0,
          total_highlights: 0,
          phase: 'running',
          updated_at: Date.now(),
        })
        message.success('持续分析已启动（快速回合检测），边录边分析新内容')
      } else {
        setContinuousAnalyzing(false)
        setContinuousRoomId(null)
        continuousActiveRoomRef.current = null
        setContinuousAnalysisStatus({
          running: false,
          room_id: null,
          phase: 'error',
          error: data?.error || '持续分析启动失败',
          updated_at: Date.now(),
        })
        message.error(data?.error || '持续分析启动失败')
      }
    }))
    unsubs.push(on('stop_continuous_analysis_response', (data: any) => {
      if (data?.success) {
        setContinuousAnalyzing(false)
        setContinuousRoomId(null)
        continuousActiveRoomRef.current = null
        message.success('持续分析已停止')
      } else {
        message.error(data?.error || '持续分析停止失败')
      }
    }))
    // 录制结束后持续分析收尾完成通知
    unsubs.push(on('continuous_analysis_complete', (data: any) => {
      message.success(`录制结束分析完成：共 ${data?.total_highlights || 0} 个回合`)
      setContinuousAnalyzing(false)
      setContinuousRoomId(null)
      continuousActiveRoomRef.current = null
      setContinuousAnalysisStatus({
        running: false,
        room_id: data?.room_id ?? null,
        total_highlights: data?.total_highlights ?? 0,
        phase: 'completed',
        updated_at: Date.now(),
      })
    }))
    // 刷新房间状态响应
    unsubs.push(on('refresh_room_status', (data: any) => {
      if (data?.success) {
        message.success(`已刷新 ${data?.refreshed || 0} 个房间状态`)
      }
    }))
    // 持续分析高光更新：仅显示通知，不添加切片到列表
    // 切片由 clip_queued 统一添加（使用映射后时间做 clip_id，避免多房间时 clip_id 不一致导致重复）
    unsubs.push(on('continuous_highlights', (data: any) => {
      const newCount = data?.new_count || 0
      if (newCount > 0) {
        const total = data?.total || 0
        message.success(`持续分析: 新增 ${newCount} 个回合 (累计 ${total})`)
      }
    }))
    return () => unsubs.forEach(u => u())
  }, [on])

  // ── 流式高光推送：仅显示通知，不添加切片到列表 ──
  // 切片由 clip_queued 统一添加（使用映射后时间做 clip_id，避免多房间时 clip_id 不一致导致重复）
  useEffect(() => {
    const unsubs: Array<() => void> = []
    unsubs.push(on('highlight_stream', (data: any) => {
      if (!data?.highlight || !data?.room_id) return
      // 只用于实时进度通知，不添加切片到列表
      // 切片在导出入队时由 clip_queued 事件统一添加
    }))
    return () => unsubs.forEach(u => u())
  }, [on])

  // ── 后端自动导出入队通知：切片添加到列表 ──
  useEffect(() => {
    const unsubs: Array<() => void> = []
    unsubs.push(on('clip_queued', (data: any) => {
      if (!data?.clip_id || !data?.room_id) return
      const st = useAppStore.getState()
      if (st.clips.some(c => c.clip_id === data.clip_id)) return
      st.addClip({
        start: data.start,
        end: data.end,
        label: data.label || '高光',
        room_id: data.room_id,
        room_name: data.room_name,
        clip_id: data.clip_id,
        job_id: data.job_id,
        export_status: data.export_deferred ? 'pending' : 'queued',
        is_ai_highlight: true,
      })
      message.success(data.export_deferred ? `已添加切片(待导出): ${data.label}` : `已添加切片: ${data.label}`)
    }))
    unsubs.push(on('clip_export_started', (data: any) => {
      if (!data?.clip_id && !data?.job_id) return
      const st = useAppStore.getState()
      st.setClips(st.clips.map(c => {
        if ((data.clip_id && c.clip_id === data.clip_id) || (data.job_id && c.job_id === data.job_id)) {
          return { ...c, export_status: 'queued' as const, job_id: data.job_id || c.job_id }
        }
        return c
      }))
    }))
    return () => unsubs.forEach(u => u())
  }, [on])

  // ── 导出文件操作 ──
  const handleOpenExportFolder = (outputPath: string) => {
    console.log('[Workbench] 用户操作: 打开导出文件夹, path:', outputPath)
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

  // 点击切片：选中房间并跳转到入点
  // 多选时取第一个选中房间作为时间线代表，确保时间线正常显示
  const selectedRoom = rooms.find(r => r.room_id === selectedRoomId)
    || (selectedRoomIds.size > 0 ? rooms.find(r => r.room_id === [...selectedRoomIds][0]) : undefined)

  const exportSummary = useMemo(() => clips.reduce((summary, clip) => {
    const status = clip.export_status ?? (clip.exported ? 'completed' : undefined)
    if (status === 'pending') summary.queued += 1
    else if (status === 'queued' || status === 'exporting' || status === 'completed' || status === 'failed') summary[status] += 1
    return summary
  }, { queued: 0, exporting: 0, completed: 0, failed: 0 }), [clips])

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
          const toStop: string[] = []
          const toStart: string[] = []
          selectedRoomIds.forEach(rid => {
            const r = rooms.find(r2 => r2.room_id === rid)
            if (!r) return
            if (r.is_recording) {
              toStop.push(rid)
            } else if (r.is_connected) {
              toStart.push(rid)
            }
          })
          toStart.forEach(rid => handleStartRecord(rid))
          if (toStop.length === 1) {
            const r = rooms.find(r2 => r2.room_id === toStop[0])
            confirmStopRecording(
              '确认停止录制',
              `将停止录制「${r?.streamer_name || '未知主播'}」`,
              () => handleStopRecord(toStop[0]),
            )
          } else if (toStop.length > 1) {
            confirmStopRecording(
              '确认停止录制',
              `将停止 ${toStop.length} 个房间的录制`,
              () => toStop.forEach(rid => handleStopRecord(rid)),
            )
          }
          break
        }
        case 'mute:toggle':
          selectedRoomIds.forEach(rid => handleToggleMute(rid))
          break
        case 'fullscreen':
          if (firstSelectedId) handleFullscreen(firstSelectedId)
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

  // ── 刷新按钮回调 ──
  const handleRefreshShortClick = useCallback(() => {
    send('refresh_room_status', {})
    const currentRooms = useAppStore.getState().rooms
    currentRooms.forEach(r => {
      if (r.preview_enabled && r.is_connected) {
        send('enable_preview', { room_id: r.room_id, enabled: false, mode: 'mse' })
        setTimeout(() => {
          send('enable_preview', { room_id: r.room_id, enabled: true, mode: 'mse' })
        }, 300)
      }
    })
    message.info('正在刷新房间状态和预览...')
  }, [send])

  const handleRefreshLongPress = useCallback(() => {
    Modal.confirm({
      title: '确认刷新全部',
      content: '将停止全部房间的录制、预览与分析，然后重启预览',
      okText: '确认',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: () => {
        const currentRooms = useAppStore.getState().rooms
        message.info('长按刷新全部：正在停止所有录制、预览和分析...')
        currentRooms.forEach(r => {
          if (r.is_recording) send('stop_recording', { room_id: r.room_id })
          if (r.preview_enabled) send('enable_preview', { room_id: r.room_id, enabled: false, mode: 'mse' })
        })
        const currentAnalysisStatus = useAppStore.getState().continuousAnalysisStatus
        if (currentAnalysisStatus?.running && currentAnalysisStatus.room_id) {
          send('stop_continuous_analysis', { main_room_id: currentAnalysisStatus.room_id })
        }
        // 1.5 秒后重启连接和预览
        setTimeout(() => {
          send('refresh_room_status', {})
          const freshRooms = useAppStore.getState().rooms
          freshRooms.forEach(r => {
            if (r.is_connected) {
              setTimeout(() => {
                send('enable_preview', { room_id: r.room_id, enabled: true, mode: 'mse' })
              }, 500)
            }
          })
          message.success('刷新全部完成：所有房间已重启')
        }, 1500)
      },
    })
  }, [send])

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
      {previewDegradationBanner && (
        <Alert
          type="info"
          banner
          showIcon
          closable
          onClose={dismissPreviewDegradationBanner}
          message={`多路预览已降为 ${formatPreviewDegradationLabel(
            previewDegradationBanner.width,
            previewDegradationBanner.height,
            previewDegradationBanner.fps,
          )} 以保流畅`}
          description={previewDegradationBanner.reason}
        />
      )}
      {/* 顶部操作栏 */}
      <div style={{ 
        padding: '16px 24px',
        background: 'var(--bg-secondary)',
        borderBottom: '1px solid var(--border-default)',
        display: 'flex',
        flexDirection: 'column',
        gap: 12,
      }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12 }}>
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
            <Tooltip title={continuousAnalyzing ? '停止持续分析' : analysisTooltip}>
              <span>
                <Button
                  size="small"
                  type={continuousAnalyzing ? 'primary' : 'default'}
                  disabled={!continuousAnalyzing && !analysisEnabled}
                  onClick={() => {
                    if (!continuousAnalyzing && !analysisEnabled) {
                      if (currentTargetRoomList.length < 1) return
                      if (!targetHasRecordings) {
                        message.warning('请先开始录制')
                        return
                      }
                      if (!targetAlignGroupReady) {
                        scrollToAlignButton()
                        return
                      }
                      return
                    }
                    if (continuousAnalyzing) {
                      const activeRoomId = continuousActiveRoomRef.current || continuousRoomId || undefined
                      send('stop_continuous_analysis', { main_room_id: activeRoomId })
                      setContinuousModalOpen(false)
                      message.info('持续分析停止请求已发送')
                    } else {
                      const targetRoomIds = currentTargetIds
                      if (targetRoomIds.length === 0) return
                      setContinuousTargetRoomIds(targetRoomIds)
                      setContinuousMainRoom(targetRoomIds[0])
                      setContinuousPresetId(getDefaultPreset().id)
                      setAnalysisIsContinuous(false)
                      setContinuousModalOpen(true)
                    }
                  }}
                >
                  {continuousAnalyzing ? '停止持续分析' : '分析导出'}
                </Button>
              </span>
            </Tooltip>
            {analysisNeedsAlign && (
              <Button type="link" size="small" onClick={scrollToAlignButton} style={{ padding: '0 4px' }}>
                去对齐
              </Button>
            )}
          </Space>
          <Space wrap>
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
            <Button
              ref={alignButtonRef}
              size="small"
              icon={<SyncOutlined spin={aligning} />}
              onClick={handleAlignLive}
              loading={aligning}
              disabled={aligning || selectedRoomIds.size === 0}
            >
              {aligning ? '对齐中...' : '一键对齐'}
            </Button>

            <RefreshButton
              onShortClick={handleRefreshShortClick}
              onLongPress={handleRefreshLongPress}
            />

            <Button
              size="small"
              type={allMuted ? 'primary' : 'default'}
              icon={allMuted ? <MutedOutlined /> : <SoundOutlined />}
              onClick={() => {
                const newMuted = !allMuted
                setAllMuted(newMuted)
                rooms.forEach(r => {
                  if (r.preview_enabled) {
                    useAppStore.getState().updateRoom(r.room_id, { preview_muted: newMuted })
                    send('set_preview_muted', { room_id: r.room_id, muted: newMuted })
                  }
                })
              }}
              disabled={rooms.length === 0}
            >
              {allMuted ? '取消静音' : '静音'}
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
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
          <AnalysisProgress status={continuousAnalysisStatus} compact exportSummary={exportSummary} />
        </div>
      </div>

      {/* 主内容区 */}
      <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>
        {/* 左侧：房间卡片 + 控制栏 */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>

          {/* 房间卡片网格 — 放大通过 CSS position:fixed 在 RoomCard 内部实现，不销毁实例 */}
          <div style={{ flex: 1, overflow: 'auto', padding: '16px 24px' }}>
            {rooms.length === 0 ? (
              <Empty
                description="暂无房间，请添加直播间地址"
                style={{ marginTop: 100 }}
              />
            ) : (
              <Row gutter={[16, 16]}>
                {sortedRooms.map(room => {
                  const isExpanded = expandedRoomId === room.room_id && !fullscreenRoomId
                  return (
                    <Col key={room.room_id} xs={24} sm={isExpanded ? 24 : 12} lg={isExpanded ? 24 : 12} xl={isExpanded ? 24 : 8}>
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
                        onToggleMultiSelect={handleToggleMultiSelect}
                        expandedRoomId={expandedRoomId}
                        fullscreenRoomId={fullscreenRoomId}
                        onCollapse={handleCollapse}
                        onExitFullscreen={handleExitFullscreen}
                      />
                    </Col>
                  )
                })}
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
            onToggleLoop={handleToggleLoop}
            onGoLive={handleGoLive}
            zoomLevel={timelineZoom}
            onZoomChange={setTimelineZoom}
            onMarkerDragEnd={handleMarkerDragEnd}
            onDeleteMarker={handleDeleteMarker}
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

          {/* 切片列表 */}
          <ClipList
            clips={clips}
            onDelete={handleDeleteClip}
            onExport={handleExportClip}
            onExportMany={handleExportMany}
            onOpenFile={handleOpenExportFile}
            onOpenFolder={handleOpenExportFolder}
            onCancelExport={handleCancelExport}
            exportProgress={exportProgressMap}
          />
        </div>
      </div>

      {/* 导出预览弹窗 */}
      <Modal
        title="导出切片预览"
        open={!!previewClip}
        onCancel={handleCancelExportModal}
        footer={[
          <Button key="cancel" onClick={handleCancelExportModal}>取消</Button>,
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

      {/* 分析导出 Modal（持续分析 + 同步分析导出合并） */}
      <Modal
        title={analysisIsContinuous ? '持续分析设置' : '多房间同步分析导出'}
        open={continuousModalOpen}
        onCancel={() => setContinuousModalOpen(false)}
        width={520}
        footer={[
          <Button key="cancel" onClick={() => setContinuousModalOpen(false)}>取消</Button>,
          <Button
            key="confirm"
            type="primary"
            loading={continuousSubmitting}
            disabled={!continuousMainRoom}
            onClick={handleConfirmAnalysisExport}
          >
            {analysisIsContinuous ? '开始持续分析' : '开始分析与导出'}
          </Button>,
        ]}
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div style={{
            padding: '8px 12px', borderRadius: 6,
            background: 'var(--bg-tertiary)', fontSize: 12, color: 'var(--text-secondary)',
          }}>
            {analysisIsContinuous
              ? '边录边分析主直播间高光，自动同步导入所有目标房间的切片列表。'
              : `分析主直播间高光，按 content_offset 映射到所有选中房间导出。选中 ${selectedRoomList.length} 个房间。`}
          </div>
          <div>
            <div style={{ fontWeight: 600, marginBottom: 8 }}>主直播间（用于高光分析）</div>
            <Radio.Group
              value={continuousMainRoom}
              onChange={(e) => setContinuousMainRoom(e.target.value)}
              style={{ display: 'flex', flexDirection: 'column', gap: 6 }}
            >
              {(analysisIsContinuous ? continuousTargetRooms : selectedRoomList).map(r => (
                <Radio key={r.room_id} value={r.room_id}>
                  {r.streamer_name || r.room_id}
                  {!r.record_output_path && <span style={{ color: 'var(--state-error)', marginLeft: 8 }}>（无录制文件）</span>}
                </Radio>
              ))}
            </Radio.Group>
          </div>
          <div>
            <div style={{ fontWeight: 600, marginBottom: 8 }}>直播类型</div>
            <Radio.Group
              value={analysisGameType}
              onChange={(e) => setAnalysisGameType(e.target.value)}
              optionType="button"
              buttonStyle="solid"
            >
              <Radio.Button value="valorant_round">无畏契约回合切割</Radio.Button>
              <Radio.Button value="generic">通用直播</Radio.Button>
            </Radio.Group>
          </div>

          {!analysisIsContinuous && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <strong>导出预设：</strong>
              <Select
                value={continuousPresetId}
                onChange={setContinuousPresetId}
                style={{ flex: 1, fontSize: 13 }}
                options={EXPORT_PRESETS.map(p => ({ value: p.id, label: `${p.name} — ${p.description}` }))}
              />
            </div>
          )}
          <div style={{
            display: 'flex', alignItems: 'center', gap: 8,
            padding: '8px 12px', borderRadius: 6,
            background: 'var(--bg-tertiary)',
          }}>
            <strong>持续分析</strong>
            <Switch
              checked={analysisIsContinuous}
              onChange={(checked) => {
                setAnalysisIsContinuous(checked)
              }}
              disabled={continuousAnalyzing}
            />
            <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
              {analysisIsContinuous ? '开启后边录边分析，自动导入高光到切片列表' : '关闭则为单次分析并导出所有选中房间'}
            </span>
          </div>
        </div>
      </Modal>
    </div>
  )
}
