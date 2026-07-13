import { useEffect, useCallback } from 'react'
import { wsClient } from '@/services/websocket'
import { useAppStore, ConnectionStatus } from '@/store/appStore'
import { ClipSegment } from '@/types'

// 模块级标记：整个应用生命周期只发起一次 connect()。
// useWebSocket() 会在 App、MainLayout、Workbench 等多处调用；连接可以共享，
// 但 WebSocket 全局事件处理器必须只挂一套，否则 MSE 分片会被重复投喂。
let _initialConnectStarted = false
let _sharedHandlersRefCount = 0
let _sharedHandlersCleanup: (() => void) | null = null

// 模块级 MSE init 段缓存：消除 mse_init 早于 VideoPreview 挂载到达的竞态。
// key = roomId, value = base64-encoded init segment data
const _mseInitCache: Record<string, string> = {}

// 模块级 MSE media 段缓存：消除 mse_segment 早于 VideoPreview 挂载到达的竞态。
// key = roomId, value = base64-encoded media segment data 数组（最多 10 个，约 5 秒）。
// player 注册时会回放缓存，避免初始几秒丢帧导致黑屏。
const _mseSegmentCache: Record<string, string[]> = {}
const _MSE_SEGMENT_CACHE_MAX = 10
// mse_segment 接收 watchdog：记录最后接收时间，超时自动重连
let _lastMseSegmentTime = 0
const _MSE_WATCHDOG_TIMEOUT_MS = 10000
let _mseWatchdogTimer: ReturnType<typeof setInterval> | null = null

function _cacheMseInit(roomId: string, b64Data: string): void {
  _mseInitCache[roomId] = b64Data
  // 最多缓存 20 个房间的 init 段，避免内存无限增长
  const keys = Object.keys(_mseInitCache)
  if (keys.length > 20) {
    delete _mseInitCache[keys[0]]
  }
}

function _cacheMseSegment(roomId: string, b64Data: string): void {
  if (!_mseSegmentCache[roomId]) {
    _mseSegmentCache[roomId] = []
  }
  const arr = _mseSegmentCache[roomId]
  arr.push(b64Data)
  // 超出上限丢弃最旧
  while (arr.length > _MSE_SEGMENT_CACHE_MAX) {
    arr.shift()
  }
}

/** 取出并清空某房间的 media 段缓存，供 player 注册时回放。 */
function _drainMseSegmentCache(roomId: string): string[] {
  const arr = _mseSegmentCache[roomId]
  if (!arr || arr.length === 0) return []
  delete _mseSegmentCache[roomId]
  return arr
}

/**
 * VideoPreview 注册 player 后调用此函数，回放在 player 未注册期间缓存的
 * media 段。返回 ArrayBuffer 数组，按时间顺序排列。
 * 这是消除"mse_segment 早于 player 注册到达"竞态的关键路径。
 */
export function drainPendingMseSegments(roomId: string): ArrayBuffer[] {
  const b64Arr = _drainMseSegmentCache(roomId)
  return b64Arr.map(b64 => _decodeBase64Segment(b64))
}

/** 获取某房间缓存的 init 段（不解码，返回 ArrayBuffer 或 null）。
 * 供 VideoPreview 创建 player 时优先用缓存 init 段 feedInit，
 * 避免等待 request_mse_init 往返。 */
export function getMseInitCache(roomId: string): ArrayBuffer | null {
  const b64 = _mseInitCache[roomId]
  if (!b64) return null
  return _decodeBase64Segment(b64)
}

function _decodeBase64Segment(b64Data: string): ArrayBuffer {
  const binary = atob(b64Data)
  const len = binary.length
  const bytes = new Uint8Array(len)
  for (let i = 0; i < len; i++) {
    bytes[i] = binary.charCodeAt(i)
  }
  return bytes.buffer
}

function _feedMseSegment(roomId: string, b64Data: string, type: 'init' | 'segment'): void {
  try {
    // 缓存 init 段，供后续 VideoPreview 挂载时直接取用
    if (type === 'init') {
      _cacheMseInit(roomId, b64Data)
    }

    const buffer = _decodeBase64Segment(b64Data)
    const registry = (window as any).__msePlayers as Record<string, any> | undefined
    const player = registry?.[roomId]
    if (!player) {
      // player 未注册时缓存 media 段，避免初始几秒丢帧。
      // init 段已通过 _cacheMseInit 缓存，此处只处理 media 段。
      if (type === 'segment') {
        _cacheMseSegment(roomId, b64Data)
      }
      return
    }

    if (type === 'init') {
      player.feedInit(buffer)
    } else {
      player.feedMedia(buffer)
    }
  } catch (e) {
    console.warn(`MSE ${type} decode failed for ${roomId}:`, e)
  }
}

function _attachSharedWebSocketHandlers(): () => void {
  const store = useAppStore.getState()
  _lastMseSegmentTime = Date.now()

  // 若 WebSocket 已连接，直接同步状态；否则显示 connecting 并启动连接。
  if (wsClient.connected) {
    store.setConnectionStatus('connected')
  } else {
    store.setConnectionStatus('connecting')
    if (!_initialConnectStarted) {
      _initialConnectStarted = true
      wsClient.connect().catch(() => {
        useAppStore.getState().setConnectionStatus('disconnected')
      })
    }
  }

  const unsubConnected = wsClient.on('connected', () => {
    useAppStore.getState().setConnectionStatus('connected')
    // 后端 on_connect 已主动推送 settings_loaded，无需重复请求
    // 延迟非关键请求，不阻塞首屏渲染
    setTimeout(() => {
      wsClient.send('get_disk_usage', {})
      wsClient.send('get_system_stats', {})
    }, 500)
    // S1: WS 重连后自动恢复所有预览。重连成功后，对 store 中 preview_enabled=true
    // 的房间重新发送 enable_preview(mse)，确保后端 MseStreamer 重建（旧进程可能已随
    // 断连终止）。使用 setTimeout 避免阻塞 get_settings 的处理。
    setTimeout(() => {
      const rooms = useAppStore.getState().rooms
      for (const room of rooms) {
        if (room.preview_enabled && room.is_connected) {
          console.log(`[WS] Reconnecting preview for room ${room.room_id} after WS reconnect`)
          wsClient.send('enable_preview', { room_id: room.room_id, enabled: true, mode: 'mse' })
        }
      }
    }, 500)
  })
  const unsubDisconnected = wsClient.on('disconnected', () => {
    useAppStore.getState().setConnectionStatus('disconnected')
  })

  const handleRooms = (data: { rooms: any[] }) => {
    if (data && Array.isArray(data.rooms)) {
      // 清理已停止预览房间的 init 重试计数器和缓存的 init 段
      const retryCounts = (window as any).__mseInitRetryCount as Record<string, number> | undefined
      for (const room of data.rooms) {
        if (!room.preview_enabled) {
          if (retryCounts && retryCounts[room.room_id] !== undefined) {
            delete retryCounts[room.room_id]
          }
          delete _mseInitCache[room.room_id]
        }
      }
      useAppStore.getState().setRooms(data.rooms)
      // 根据录制状态切换托盘图标
      const anyRecording = data.rooms.some((r: any) => r.is_recording)
      const anyError = data.rooms.some((r: any) => r.last_error && !r.is_recording)
      if (anyError) {
        window.electronAPI?.setTrayState?.('error')
      } else if (anyRecording) {
        window.electronAPI?.setTrayState?.('recording')
      } else {
        window.electronAPI?.setTrayState?.('idle')
      }
    }
  }
  const unsubRoomsUpdated = wsClient.on('rooms_updated', handleRooms)
  const unsubRoomsLoaded = wsClient.on('rooms_loaded', handleRooms)

  const unsubRoomUpdated = wsClient.on('room_updated', (data: { room_id: string } & Record<string, any>) => {
    if (data && data.room_id) {
      const { room_id, ...updates } = data
      useAppStore.getState().updateRoom(room_id, updates)
    }
  })

  const unsubClipCompleted = wsClient.on('clip_completed', (data: any) => {
    if (data && typeof data.start === 'number' && typeof data.end === 'number') {
      useAppStore.getState().addRecentClip(data as ClipSegment)
    }
    window.electronAPI?.setProgressBar?.(-1)
    useAppStore.getState().setExportProgress(null)
  })

  const unsubClipFailed = wsClient.on('clip_failed', () => {
    window.electronAPI?.setProgressBar?.(-1)
    useAppStore.getState().setExportProgress(null)
  })

  const unsubExportProgress = wsClient.on('export_progress', (data: any) => {
    if (data?.percent !== undefined) {
      const progress = Math.max(0, Math.min(1, data.percent / 100))
      window.electronAPI?.setProgressBar?.(progress)
      useAppStore.getState().setExportProgress({
        job_id: data.job_id || '',
        percent: data.percent,
      })
    }
  })

  const handleSettings = (data: any) => {
    if (data) {
      const { appSettings: savedAppSettings, ...recordSettings } = data
      useAppStore.getState().setSettings(recordSettings)
      if (savedAppSettings && typeof savedAppSettings === 'object') {
        useAppStore.getState().setAppSettings(savedAppSettings)
        if (savedAppSettings.theme) {
          if (savedAppSettings.theme === 'dark') {
            document.documentElement.classList.add('dark')
          } else {
            document.documentElement.classList.remove('dark')
          }
        }
      }
    }
  }
  const unsubSettingsLoaded = wsClient.on('settings_loaded', handleSettings)
  const unsubSettingsResponse = wsClient.on('get_settings_response', handleSettings)

  // 用白名单校验 connection_status，避免任意值污染状态
  const validStatus = ['connected', 'connecting', 'disconnected', 'reconnect_failed']
  const unsubConnectionStatus = wsClient.on('connection_status', (data: { status: unknown }) => {
    if (data && typeof data.status === 'string' && validStatus.includes(data.status)) {
      useAppStore.getState().setConnectionStatus(data.status as ConnectionStatus)
    }
  })

  // 重连过程中更新为 connecting，使状态显示与实际一致
  const unsubReconnecting = wsClient.on('reconnecting', () => {
    useAppStore.getState().setConnectionStatus('connecting')
  })

  // 重连次数耗尽：更新为 reconnect_failed，UI 可据此提示用户手动重连
  const unsubReconnectFailed = wsClient.on('reconnect_failed', () => {
    console.error('WebSocket reconnect failed: max attempts reached, backend may be unavailable')
    useAppStore.getState().setConnectionStatus('reconnect_failed')
  })

  const handleDiskUsage = (data: any) => {
    if (data && typeof data.total === 'number' && typeof data.used === 'number' && typeof data.free === 'number') {
      useAppStore.getState().setDiskUsage({ total: data.total, used: data.used, free: data.free })
    }
  }
  const unsubDiskUsage = wsClient.on('disk_usage', handleDiskUsage)
  const unsubDiskUsageResponse = wsClient.on('get_disk_usage_response', handleDiskUsage)

  const handleSystemStats = (data: any) => {
    if (data && typeof data.cpu_percent === 'number') {
      useAppStore.getState().setSystemStats({
        cpu_percent: data.cpu_percent,
        memory_percent: data.memory_percent,
        memory_total_gb: data.memory_total_gb,
        memory_used_gb: data.memory_used_gb,
        disk_percent: data.disk_percent,
        disk_total_gb: data.disk_total_gb,
        disk_free_gb: data.disk_free_gb,
      })
    }
  }
  const unsubSystemStats = wsClient.on('system_stats', handleSystemStats)

  const unsubDepStatus = wsClient.on('check_dependencies_response', (data: any) => {
    if (data && data.dependencies) {
      useAppStore.getState().setDependencyStatus(data.dependencies)
    }
  })

  const unsubRecordingQueue = wsClient.on('recording_queue', (data: {
    room_id?: string
    position?: number
    waiting?: boolean
  }) => {
    if (data?.room_id) {
      useAppStore.getState().updateRoom(data.room_id, {
        is_recording_starting: true,
        is_recording_queued: !!data.waiting,
        recording_queue_position: data.position ?? 0,
      })
    }
  })

  const unsubMseInit = wsClient.on('mse_init', (data: { room_id: string; data: string }) => {
    if (data?.room_id && data?.data) {
      _feedMseSegment(data.room_id, data.data, 'init')
    }
  })

  const unsubMseSegment = wsClient.on('mse_segment', (data: { room_id: string; data: string }) => {
    if (data?.room_id && data?.data) {
      _lastMseSegmentTime = Date.now()
      _feedMseSegment(data.room_id, data.data, 'segment')
    }
  })

  const unsubMseError = wsClient.on('mse_error', (data: { room_id: string; error: string }) => {
    if (data?.room_id) {
      console.warn(`MSE error for ${data.room_id}:`, data.error)
      useAppStore.getState().updateRoom(data.room_id, {
        mse_error: data.error,
        preview_enabled: false,
        mse_reconnecting: undefined,
      })
    }
  })

  const unsubMseReconnecting = wsClient.on('mse_reconnecting', (data: { room_id: string; attempt: number; max_attempts: number }) => {
    if (data?.room_id) {
      console.log(`MSE reconnecting for ${data.room_id}: attempt ${data.attempt}/${data.max_attempts}`)
      useAppStore.getState().updateRoom(data.room_id, {
        mse_reconnecting: { attempt: data.attempt, maxAttempts: data.max_attempts },
        mse_error: undefined,
      })
    }
  })

  const unsubMseReconnected = wsClient.on('mse_reconnected', (data: { room_id: string }) => {
    if (data?.room_id) {
      console.log(`MSE reconnected for ${data.room_id}`)
      useAppStore.getState().updateRoom(data.room_id, {
        mse_reconnecting: undefined,
        mse_error: undefined,
      })
    }
  })

  const unsubEnablePreviewResp = wsClient.on('enable_preview_response', (data: {
    success?: boolean
    error?: string
    room_id?: string
    degraded?: boolean
    width?: number
    height?: number
    fps?: number
    reason?: string
  }) => {
    if (data?.success && data.degraded && data.width && data.height) {
      useAppStore.getState().setPreviewDegradationBanner({
        width: data.width,
        height: data.height,
        fps: data.fps,
        reason: data.reason,
      })
    }
    if (data && !data.success && data.error) {
      console.warn('enable_preview failed:', data.error)
      if (data.room_id) {
        useAppStore.getState().updateRoom(data.room_id, {
          last_error: data.error,
          mse_error: data.error,
        })
      } else {
        const rooms = useAppStore.getState().rooms
        const connectedRoom = rooms.find(r => r.is_connected && !r.preview_enabled)
        if (connectedRoom) {
          useAppStore.getState().updateRoom(connectedRoom.room_id, {
            last_error: data.error,
            mse_error: data.error,
          })
        }
      }
    }
  })

  const unsubRequestMseInitResp = wsClient.on('request_mse_init_response', (data: { success?: boolean; note?: string; room_id?: string }) => {
    if (data && !data.success && data.room_id) {
      const roomId = data.room_id

      // 后端尚未就绪，但前端可能已通过 mse_init 广播收到了 init 段
      const cachedB64 = _mseInitCache[roomId]
      if (cachedB64) {
        try {
          const registry = (window as any).__msePlayers as Record<string, any> | undefined
          const player = registry?.[roomId]
          if (player) {
            player.feedInit(_decodeBase64Segment(cachedB64))
            console.log(`MSE init delivered from frontend cache for ${roomId}`)
            return
          }
        } catch (e) {
          console.warn(`MSE init cache delivery failed for ${roomId}:`, e)
        }
      }

      // 使用模块级 Map 跟踪重试次数，避免无限重试
      ;(window as any).__mseInitRetryCount = (window as any).__mseInitRetryCount || {}
      const counts = (window as any).__mseInitRetryCount as Record<string, number>
      const count = (counts[roomId] || 0) + 1
      counts[roomId] = count
      if (count > 10) {
        console.warn(`MSE init retry exhausted for ${roomId}`)
        useAppStore.getState().updateRoom(roomId, { mse_error: 'MSE 流初始化超时，请重试预览' })
        delete counts[roomId]
        return
      }
      console.log(`MSE init not ready for ${roomId}, retrying (${count}/10) in ${count}s...`)
      setTimeout(() => {
        wsClient.send('request_mse_init', { room_id: roomId })
      }, count * 1000)
    }
  })

  // mse_segment 接收 watchdog：超时自动重连，防止"半开连接"导致前端假死
  _mseWatchdogTimer = setInterval(() => {
    if (!wsClient.connected) return
    const elapsed = Date.now() - _lastMseSegmentTime
    // 有预览房间且超过 10s 未收到 segment，触发重连
    const hasPreview = useAppStore.getState().rooms.some(r => r.preview_enabled && r.is_connected)
    if (hasPreview && elapsed > _MSE_WATCHDOG_TIMEOUT_MS) {
      console.warn(`[WS] No mse_segment received for ${(elapsed / 1000).toFixed(1)}s, reconnecting...`)
      wsClient.reconnect()
    }
  }, 5000)

  return () => {
    unsubConnected()
    unsubDisconnected()
    unsubRoomsUpdated()
    unsubRoomsLoaded()
    unsubRoomUpdated()
    unsubClipCompleted()
    unsubClipFailed()
    unsubExportProgress()
    unsubSettingsLoaded()
    unsubSettingsResponse()
    unsubConnectionStatus()
    unsubReconnecting()
    unsubReconnectFailed()
    unsubDiskUsage()
    unsubDiskUsageResponse()
    unsubSystemStats()
    unsubDepStatus()
    unsubRecordingQueue()
    unsubMseInit()
    unsubMseSegment()
    unsubMseError()
    unsubMseReconnecting()
    unsubMseReconnected()
    unsubEnablePreviewResp()
    unsubRequestMseInitResp()
    if (_mseWatchdogTimer) {
      clearInterval(_mseWatchdogTimer)
      _mseWatchdogTimer = null
    }
  }
}

export function useWebSocket() {
  const connectionStatus = useAppStore((state) => state.connectionStatus)

  useEffect(() => {
    _sharedHandlersRefCount += 1
    if (!_sharedHandlersCleanup) {
      _sharedHandlersCleanup = _attachSharedWebSocketHandlers()
    }

    // 监听 Electron 主进程的清理全部房间事件（应用退出时触发）
    const cleanupOnExit = window.electronAPI?.onCleanupAllRooms?.(() => {
      console.log('[useWebSocket] 收到清理全部房间通知，正在停止所有录制/预览/分析...')
      const state = useAppStore.getState()
      // 停止所有录制
      state.rooms.forEach(r => {
        if (r.is_recording) {
          wsClient.send('stop_recording', { room_id: r.room_id })
        }
        if (r.preview_enabled) {
          wsClient.send('enable_preview', { room_id: r.room_id, enabled: false, mode: 'mse' })
        }
      })
      // 停止持续分析
      if (state.continuousAnalysisStatus?.running && state.continuousAnalysisStatus.room_id) {
        wsClient.send('stop_continuous_analysis', { main_room_id: state.continuousAnalysisStatus.room_id })
      }
    })

    return () => {
      cleanupOnExit?.() // 移除 IPC 监听器
      _sharedHandlersRefCount = Math.max(0, _sharedHandlersRefCount - 1)
      if (_sharedHandlersRefCount === 0) {
        _sharedHandlersCleanup?.()
        _sharedHandlersCleanup = null
      }
    }
  }, [])

  const send = useCallback((type: string, data: any) => {
    wsClient.send(type, data)
  }, [])

  const on = useCallback((event: string, handler: (data: any) => void) => {
    return wsClient.on(event, handler)
  }, [])

  const reconnect = useCallback(() => {
    wsClient.reconnect()
  }, [])

  return { isConnected: connectionStatus === 'connected', connectionStatus, send, on, reconnect }
}
