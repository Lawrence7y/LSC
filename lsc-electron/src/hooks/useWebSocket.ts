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
// key = roomId, value = hex-encoded init segment data
const _mseInitCache: Record<string, string> = {}

// 模块级 MSE media 段缓存：消除 mse_segment 早于 VideoPreview 挂载到达的竞态。
// key = roomId, value = hex-encoded media segment data 数组（最多 10 个，约 5 秒）。
// player 注册时会回放缓存，避免初始几秒丢帧导致黑屏。
const _mseSegmentCache: Record<string, string[]> = {}
const _MSE_SEGMENT_CACHE_MAX = 10

function _cacheMseInit(roomId: string, hexData: string): void {
  _mseInitCache[roomId] = hexData
  // 最多缓存 20 个房间的 init 段，避免内存无限增长
  const keys = Object.keys(_mseInitCache)
  if (keys.length > 20) {
    delete _mseInitCache[keys[0]]
  }
}

function _cacheMseSegment(roomId: string, hexData: string): void {
  if (!_mseSegmentCache[roomId]) {
    _mseSegmentCache[roomId] = []
  }
  const arr = _mseSegmentCache[roomId]
  arr.push(hexData)
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
  const hexArr = _drainMseSegmentCache(roomId)
  return hexArr.map(hex => _decodeHexSegment(hex))
}

/** 获取某房间缓存的 init 段（不解码，返回 ArrayBuffer 或 null）。
 * 供 VideoPreview 创建 player 时优先用缓存 init 段 feedInit，
 * 避免等待 request_mse_init 往返。 */
export function getMseInitCache(roomId: string): ArrayBuffer | null {
  const hex = _mseInitCache[roomId]
  if (!hex) return null
  return _decodeHexSegment(hex)
}

function _decodeHexSegment(hexData: string): ArrayBuffer {
  const total = hexData.length / 2
  const bytes = new Uint8Array(total)
  for (let i = 0, j = 0; i < total; i++, j += 2) {
    bytes[i] = (parseInt(hexData.charAt(j), 16) << 4) | parseInt(hexData.charAt(j + 1), 16)
  }
  return bytes.buffer
}

function _feedMseSegment(roomId: string, hexData: string, type: 'init' | 'segment'): void {
  try {
    // 缓存 init 段，供后续 VideoPreview 挂载时直接取用
    if (type === 'init') {
      _cacheMseInit(roomId, hexData)
    }

    const buffer = _decodeHexSegment(hexData)
    const registry = (window as any).__msePlayers as Record<string, any> | undefined
    const player = registry?.[roomId]
    if (!player) {
      // player 未注册时缓存 media 段，避免初始几秒丢帧。
      // init 段已通过 _cacheMseInit 缓存，此处只处理 media 段。
      if (type === 'segment') {
        _cacheMseSegment(roomId, hexData)
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
    wsClient.send('get_disk_usage', {})
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
  })

  const handleSettings = (data: any) => {
    if (data) {
      useAppStore.getState().setSettings(data)
    }
  }
  const unsubSettingsLoaded = wsClient.on('settings_loaded', handleSettings)
  const unsubSettingsResponse = wsClient.on('get_settings_response', handleSettings)

  // 用白名单校验 connection_status，避免任意值污染状态
  const validStatus = ['connected', 'connecting', 'disconnected']
  const unsubConnectionStatus = wsClient.on('connection_status', (data: { status: unknown }) => {
    if (data && typeof data.status === 'string' && validStatus.includes(data.status)) {
      useAppStore.getState().setConnectionStatus(data.status as ConnectionStatus)
    }
  })

  // 重连过程中更新为 connecting，使状态显示与实际一致
  const unsubReconnecting = wsClient.on('reconnecting', () => {
    useAppStore.getState().setConnectionStatus('connecting')
  })

  // 重连次数耗尽：更新为 disconnected，UI 可据此提示用户重启应用
  const unsubReconnectFailed = wsClient.on('reconnect_failed', () => {
    console.error('WebSocket reconnect failed: max attempts reached, backend may be unavailable')
    useAppStore.getState().setConnectionStatus('disconnected')
  })

  const handleDiskUsage = (data: any) => {
    if (data && typeof data.total === 'number' && typeof data.used === 'number' && typeof data.free === 'number') {
      useAppStore.getState().setDiskUsage({ total: data.total, used: data.used, free: data.free })
    }
  }
  const unsubDiskUsage = wsClient.on('disk_usage', handleDiskUsage)
  const unsubDiskUsageResponse = wsClient.on('get_disk_usage_response', handleDiskUsage)

  const unsubMseInit = wsClient.on('mse_init', (data: { room_id: string; data: string }) => {
    if (data?.room_id && data?.data) {
      _feedMseSegment(data.room_id, data.data, 'init')
    }
  })

  const unsubMseSegment = wsClient.on('mse_segment', (data: { room_id: string; data: string }) => {
    if (data?.room_id && data?.data) {
      _feedMseSegment(data.room_id, data.data, 'segment')
    }
  })

  const unsubMseError = wsClient.on('mse_error', (data: { room_id: string; error: string }) => {
    if (data?.room_id) {
      console.warn(`MSE error for ${data.room_id}:`, data.error)
      useAppStore.getState().updateRoom(data.room_id, {
        mse_error: data.error,
        preview_enabled: false,
      })
    }
  })

  const unsubEnablePreviewResp = wsClient.on('enable_preview_response', (data: { success?: boolean; error?: string; room_id?: string }) => {
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
      const cachedHex = _mseInitCache[roomId]
      if (cachedHex) {
        try {
          const registry = (window as any).__msePlayers as Record<string, any> | undefined
          const player = registry?.[roomId]
          if (player) {
            player.feedInit(_decodeHexSegment(cachedHex))
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

  return () => {
    unsubConnected()
    unsubDisconnected()
    unsubRoomsUpdated()
    unsubRoomsLoaded()
    unsubRoomUpdated()
    unsubClipCompleted()
    unsubSettingsLoaded()
    unsubSettingsResponse()
    unsubConnectionStatus()
    unsubReconnecting()
    unsubReconnectFailed()
    unsubDiskUsage()
    unsubDiskUsageResponse()
    unsubMseInit()
    unsubMseSegment()
    unsubMseError()
    unsubEnablePreviewResp()
    unsubRequestMseInitResp()
  }
}

export function useWebSocket() {
  const connectionStatus = useAppStore((state) => state.connectionStatus)

  useEffect(() => {
    _sharedHandlersRefCount += 1
    if (!_sharedHandlersCleanup) {
      _sharedHandlersCleanup = _attachSharedWebSocketHandlers()
    }

    return () => {
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

  return { isConnected: connectionStatus === 'connected', send, on }
}
