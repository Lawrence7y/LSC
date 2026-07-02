import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { Row, Col, Card, Input, Button, Space, message, Empty, Modal, Tooltip, Select, Alert } from 'antd'
import { PlusOutlined, VideoCameraOutlined, SoundOutlined, MutedOutlined, SyncOutlined } from '@ant-design/icons'
import { useWebSocket } from '@/hooks/useWebSocket'
import { useAppStore } from '@/store/appStore'
import { useKeyboardShortcuts } from '@/hooks/useKeyboardShortcuts'
import { RoomCard } from './components/RoomCard'
import { ControlBar } from './components/ControlBar'
import { ClipList } from './components/ClipList'
import { RecordSettings } from './components/RecordSettings'
import { ClipSegment } from '@/types'
import { EXPORT_PRESETS, getDefaultPreset } from '@/services/exportPresets'
import { formatTime } from '@/utils/time'
import { getAligner } from '@/utils/previewAudioAligner'
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
  const setSelectedRoomId = useAppStore((state) => state.setSelectedRoomId)
  const addClip = useAppStore((state) => state.addClip)
  const setClips = useAppStore((state) => state.setClips)
  const [loading, setLoading] = useState(false)
  const [url, setUrl] = useState('')
  const [previewClip, setPreviewClip] = useState<ClipSegment | null>(null)
  const [exportPresetId, setExportPresetId] = useState(getDefaultPreset().id)
  const [analyzing, setAnalyzing] = useState(false)
  const [analysisResults, setAnalysisResults] = useState<{start: number; end: number; score: number}[]>([])
  const [showAnalysisModal, setShowAnalysisModal] = useState(false)
  const [loopPreview, setLoopPreview] = useState(false)
  const [timelineZoom, setTimelineZoom] = useState(1)
  const [allMuted, setAllMuted] = useState(false)
  const [sortBy, setSortBy] = useState<string>('default')
  const [aligning, setAligning] = useState(false)
  const aligningRoomIdsRef = useRef<Set<string>>(new Set())
  const loopTimerRef = useRef<ReturnType<typeof setInterval> | null>(null)
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
    if (expandedRoomId === null) return
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        e.stopPropagation()
        setExpandedRoomId(null)
        setFullscreenRoomId(null)
      }
    }
    window.addEventListener('keydown', handleKeyDown, true)
    return () => window.removeEventListener('keydown', handleKeyDown, true)
  }, [expandedRoomId])

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
      // 更新切片列表中对应切片的导出状态
      if (data?.room_id && typeof data.start === 'number' && typeof data.end === 'number') {
        const store = useAppStore.getState()
        const updatedClips = store.clips.map(c =>
          c.start === data.start && c.end === data.end && c.room_id === data.room_id
            ? { ...c, exported: true, outputPath: data.output_path }
            : c
        )
        store.setClips(updatedClips)
        message.success('切片导出完成')
      }
    }))
    // 导出失败/取消：后端通过 clip_failed 通知
    unsubs.push(on('clip_failed', (data: { job_id?: string; error?: string }) => {
      const isCancelled = data.error === '导出已取消'
      if (!isCancelled && data.error) {
        message.error(`导出失败：${data.error}`)
      }
    }))
    // export_clip 提交响应：失败时立即提示
    unsubs.push(on('export_clip_response', (data: { success?: boolean; error?: string; job_id?: string }) => {
      if (data?.job_id && data?.success === false) {
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

  // 连接房间
  const handleConnect = useCallback((roomId: string) => {
    console.log('[Workbench] 用户操作: 连接房间, roomId:', roomId);
    send('connect_room', { room_id: roomId })
  }, [send])

  // 断开房间
  const handleDisconnect = useCallback((roomId: string) => {
    console.log('[Workbench] 用户操作: 断开房间连接, roomId:', roomId);
    send('disconnect_room', { room_id: roomId })
  }, [send])

  // 切换静音
  const handleToggleMute = useCallback((roomId: string) => {
    const room = useAppStore.getState().rooms.find((r) => r.room_id === roomId)
    if (!room) return
    console.log('[Workbench] 用户操作: 切换静音状态, roomId:', roomId, 'newMuted:', !room.preview_muted);
    // 取消静音时显式 resume AudioContext，确保 Web Audio 路由有输出
    if (room.preview_muted) {
      const ctx = getAligner().getContextSync()
      if (ctx.state === 'suspended') {
        ctx.resume().catch((e) => {
          console.warn('[Workbench] Failed to resume AudioContext on unmute:', e)
        })
      }
    }
    send('set_preview_muted', { room_id: roomId, muted: !room.preview_muted })
  }, [send])

  // 开始录制
  const handleStartRecord = useCallback((roomId: string) => {
    console.log('[Workbench] 用户操作: 开始录制, roomId:', roomId);
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

  // 放大预览（单级：点击放大到左侧面板，再次点击退出；全屏由 video 原生 controls 提供）
  const handleFullscreen = useCallback((roomId: string) => {
    setExpandedRoomId(prev => prev === roomId ? null : roomId)
    setFullscreenRoomId(null)
  }, [])

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

  // 时间线跳转（多选时同步 seek 所有选中房间）
  const handleTimelineSeek = useCallback((time: number) => {
    console.log('[Workbench] 用户操作: 时间线跳转, time:', time.toFixed(2), '房间数:', selectedRoomIds.size)
    selectedRoomIds.forEach(rid => mseSeek(rid, time))
  }, [selectedRoomIds, mseSeek])

  // 设置入点
  const handleMarkIn = useCallback((roomId: string) => {
    const time = getPreviewCurrentTime(roomId)
    console.log('[Workbench] 用户操作: 设置入点, roomId:', roomId, 'time:', time.toFixed(2))
    send('set_mark_in', { room_id: roomId, time })
  }, [send, getPreviewCurrentTime])

  // 设置出点
  const handleMarkOut = useCallback((roomId: string) => {
    const time = getPreviewCurrentTime(roomId)
    console.log('[Workbench] 用户操作: 设置出点, roomId:', roomId, 'time:', time.toFixed(2))
    send('set_mark_out', { room_id: roomId, time })
  }, [send, getPreviewCurrentTime])

  // 添加到切片列表
  const handleAddClip = useCallback((roomId: string) => {
    const currentRooms = useAppStore.getState().rooms
    const currentClips = useAppStore.getState().clips
    const room = currentRooms.find(r => r.room_id === roomId)
    if (room && room.mark_in !== null && room.mark_out !== null) {
      console.log('[Workbench] 用户操作: 添加切片, roomId:', roomId, 'mark_in:', room.mark_in, 'mark_out:', room.mark_out)
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
    selectedRoomIds.forEach(rid => {
      const registry = (window as any).__msePlayers
      const entry = registry?.[rid]
      const player = entry?.player
      if (player && typeof player.resumePlayback === 'function') {
        // 使用 MSE player 的 resumePlayback() 方法：
        // 1. 自动 seek 到缓冲区末尾
        // 2. 重置 live-edge 对齐标志，允许后续自动校准
        // 3. 触发 _tryPlay() 延迟重试机制
        player.resumePlayback()
      } else {
        // 备用逻辑：player 不可用时直接操作 video 元素
        const video = entry?.player?.videoElement as HTMLVideoElement | undefined
        if (video && video.buffered.length > 0) {
          const bufEnd = video.buffered.end(video.buffered.length - 1)
          video.currentTime = Math.max(0, bufEnd - 0.5)
          video.play().catch(() => {})
        }
      }
    })
  }, [selectedRoomIds])

  // Phase 3: 音频对齐结果监听器
  useEffect(() => {
    const unsub = on('align_preview_audio_response', (data: any) => {
      setAligning(false)
      if (!data?.success || !data?.offsets) {
        console.warn('[Workbench] 音频对齐失败:', data?.error)
        message.warning('音频对齐失败，已使用缓冲区对齐')
        return
      }
      const offsets = data.offsets as Record<string, number>
      const scores = (data.scores || {}) as Record<string, number>
      const referenceRoomId = data.reference_room_id as string
      const registry = (window as any).__msePlayers
      let alignedCount = 0
      let mutedCount = 0
      aligningRoomIdsRef.current.forEach(rid => {
        const offset = offsets[rid]
        if (offset === undefined) return

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
        if (offset !== undefined && offset > 0.05 && rid !== referenceRoomId) {
          send('set_preview_muted', { room_id: rid, muted: true })
          mutedCount++
        }
      })

      const scoreValues = Object.values(scores) as number[]
      const avgScore = scoreValues.length > 0
        ? scoreValues.reduce((a, b) => a + b, 0) / scoreValues.length
        : 0
      console.log('[Workbench] 音频对齐完成: aligned=' + alignedCount + ', avgScore=' + avgScore.toFixed(3))
      const muteMsg = mutedCount > 0 ? `，已静音 ${mutedCount} 个快房间消除回声` : ''
      message.success(`已精确对齐 ${alignedCount} 个直播间（置信度 ${Math.round(avgScore * 100)}%）${muteMsg}`)
    })
    return () => unsub()
  }, [on, send])

  const handleAlignLive = useCallback(async () => {
    if (selectedRoomIds.size === 0) return
    console.log('[Workbench] 用户操作: 一键对齐, 房间数:', selectedRoomIds.size)
    const registry = (window as any).__msePlayers
    if (!registry) return

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

    // 少于 2 个房间时不需要音频对齐
    if (selectedRoomIds.size < 2) {
      message.success(`已对齐 ${selectedRoomIds.size} 个直播间`)
      return
    }
    message.info('已粗略对齐，正在精确对齐...')

    // Phase 2: 并行音频捕获 + 后端 FFT 计算
    setAligning(true)
    aligningRoomIdsRef.current = new Set(selectedRoomIds)
    try {
      const aligner = getAligner()
      const capturePromises = [...selectedRoomIds].map(async rid => {
        const video = registry?.[rid]?.player?.videoElement as HTMLVideoElement | undefined
        if (!video) return null
        const pcm = await aligner.captureAudio(rid, video, 3.0)
        if (!pcm) return null
        return { room_id: rid, sample_rate: 16000, pcm_base64: aligner.base64Encode(pcm) }
      })
      const results = (await Promise.all(capturePromises)).filter((r): r is { room_id: string; sample_rate: number; pcm_base64: string } => r !== null)
      if (results.length < 2) {
        setAligning(false)
        message.warning('音频捕获不足，已使用缓冲区对齐')
        return
      }
      send('align_preview_audio', { rooms: results })
    } catch (err) {
      setAligning(false)
      console.error('[Workbench] 音频对齐异常:', err)
      message.warning('音频对齐失败，已使用缓冲区对齐')
    }
  }, [selectedRoomIds, send])

  const handleMarkerDrag = useCallback((type: 'in' | 'out', time: number) => {
    console.log('[Workbench] 用户操作: 标记拖拽, type:', type, 'time:', time.toFixed(2))
    selectedRoomIds.forEach(rid => {
      if (type === 'in') {
        send('set_mark_in', { room_id: rid, time })
      } else {
        send('set_mark_out', { room_id: rid, time })
      }
    })
  }, [selectedRoomIds, send])

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
    const jobId = `export-${Date.now()}`

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
    console.log('[Workbench] 用户操作: 启动场景分析, roomId:', selectedRoomId)
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
    console.log('[Workbench] 用户操作: 导入分析结果, 片段数:', newClips.length)
    setClips([...clips, ...newClips])
    setShowAnalysisModal(false)
    message.success(`已导入 ${newClips.length} 个高光片段`)
  }

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
          <Button
            size="small"
            icon={<SyncOutlined />}
            onClick={handleAlignLive}
            loading={aligning}
            disabled={aligning || selectedRoomIds.size === 0}
          >
            {aligning ? '对齐中' : '一键对齐'}
          </Button>
          <Button
            size="small"
            type={allMuted ? 'primary' : 'default'}
            icon={allMuted ? <MutedOutlined /> : <SoundOutlined />}
            onClick={() => {
              const newMuted = !allMuted
              setAllMuted(newMuted)
              rooms.forEach(r => {
                if (r.preview_enabled) {
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
                      onToggleMultiSelect={handleToggleMultiSelect}
                      expandedRoomId={expandedRoomId}
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
            onToggleLoop={handleToggleLoop}
            onGoLive={handleGoLive}
            zoomLevel={timelineZoom}
            onZoomChange={setTimelineZoom}
            onMarkerDrag={handleMarkerDrag}
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

          {/* 录制设置 */}
          <RecordSettings />

          {/* 切片列表 */}
          <ClipList
            clips={clips}
            onDelete={handleDeleteClip}
            onExport={handleExportClip}
            onOpenFile={handleOpenExportFile}
            onOpenFolder={handleOpenExportFolder}
          />
        </div>
      </div>

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
