import { useEffect, useCallback } from 'react'
import { message } from 'antd'
import { wsClient as _wsClient } from '@/services/websocket'
import type { RoomPipelineHealth, RoomSession, RuntimeEventPayload } from '@/types'

// 导出 wsClient 供需要在组件外订阅事件的场景使用
export const wsClient = _wsClient
import { useAppStore } from '@/store/appStore'
import { removePlayhead } from '@/utils/playheadStore'

// 模块级标记：整个应用生命周期只发起一次 connect()。
// useWebSocket() 会在 App、MainLayout、Workbench 等多处调用；连接可以共享，
// 但 WebSocket 全局事件处理器必须只挂一套，否则 MSE 分片会被重复投喂。
let _initialConnectStarted = false
let _sharedHandlersRefCount = 0
let _sharedHandlersCleanup: (() => void) | null = null

// 广播序列号追踪：检测丢消息并触发强制同步
let _lastBroadcastSeq: number | null = null

// Runtime events may arrive after a reconnect or alongside a room snapshot.
// Keep the newest generation/timestamp per room so stale events cannot roll
// the visible health state backwards.
const _lastRuntimeEventByRoom = new Map<string, { generation: number; occurredAt: number; eventId: string }>()

function _isStaleRuntimeEvent(payload: RuntimeEventPayload): boolean {
  const roomId = String(payload.room_id || '')
  if (!roomId) return true
  const generation = Number(payload.lease_generation ?? payload.generation ?? 0)
  const occurredAt = Number(payload.occurred_at || 0)
  const previous = _lastRuntimeEventByRoom.get(roomId)
  if (
    previous
    && (
      generation < previous.generation
      || (generation === previous.generation && occurredAt < previous.occurredAt)
      || (generation === previous.generation && occurredAt === previous.occurredAt && payload.event_id === previous.eventId)
    )
  ) {
    return true
  }
  _lastRuntimeEventByRoom.set(roomId, {
    generation,
    occurredAt,
    eventId: String(payload.event_id || ''),
  })
  return false
}

function _applyRuntimeEvent(payload: RuntimeEventPayload): void {
  if (_isStaleRuntimeEvent(payload)) return
  const store = useAppStore.getState()
  const room = store.rooms.find((item) => item.room_id === payload.room_id)
  if (!room) return

  const previous = room.pipeline_health
  const state = String(payload.state_to || payload.state || '').toUpperCase()
  const component = String(payload.component || payload.stage || '').toLowerCase()
  const safeContext = payload.safe_context || payload.context || {}
  const sink = String(safeContext.sink || '').toLowerCase()
  const health: RoomPipelineHealth = {
    schema_version: Number(payload.schema_version || previous?.schema_version || 1),
    platform_id: payload.platform_id || previous?.platform_id || room.platform,
    pipeline_mode: previous?.pipeline_mode,
    platform: previous?.platform || 'UNKNOWN',
    resolver: previous?.resolver || 'IDLE',
    ingest: (state || previous?.ingest || 'IDLE') as RoomPipelineHealth['ingest'],
    recording: previous?.recording || 'IDLE',
    preview: previous?.preview || 'IDLE',
    error: previous?.error,
    failure_kind: payload.failure_kind || previous?.failure_kind,
    support_level: previous?.support_level,
    connection_policy: previous?.connection_policy,
    credential_status: previous?.credential_status,
    credential_kinds: previous?.credential_kinds,
    lease_id: payload.lease_id || previous?.lease_id,
    candidate_id: payload.candidate_id || previous?.candidate_id,
    quality_id: previous?.quality_id,
    protocol: previous?.protocol,
    cdn_id: previous?.cdn_id,
    lease_expires_at: previous?.lease_expires_at,
    lease_refresh_at: previous?.lease_refresh_at,
    generation: Number(payload.lease_generation ?? payload.generation ?? previous?.generation ?? 0),
    upstream_generation: previous?.upstream_generation,
    recovery_attempt: payload.attempt ?? previous?.recovery_attempt,
    max_recovery_attempts: payload.max_attempts ?? previous?.max_recovery_attempts,
    resources: previous?.resources,
    updated_at: Number(payload.occurred_at || Date.now() / 1000),
  }

  if (component === 'recording' || sink === 'recording' || payload.event_type.includes('RECORDING')) {
    health.recording = payload.event_type === 'SINK_DETACHED' || payload.event_type === 'RECORDING_STOPPED'
      ? 'IDLE'
      : state === 'RUNNING' || payload.event_type === 'RECORDING_STARTED'
        ? 'RECORDING'
        : state === 'FAILED' || state === 'DEGRADED' ? 'ERROR' : health.recording
  }
  if (component === 'preview' || sink === 'preview' || payload.event_type.includes('PREVIEW')) {
    health.preview = payload.event_type === 'SINK_DETACHED'
      ? 'IDLE'
      : state === 'RUNNING' || payload.event_type === 'PREVIEW_ATTACHED'
        ? 'PLAYING'
        : state === 'FAILED' || state === 'DEGRADED' ? 'ERROR' : health.preview
  }

  store.updateRoom(payload.room_id, { pipeline_health: health })
}

// 按房间跟踪 mse_init 重试定时器（对象写法，便于测试与清理）
const _mseInitRetryTimers: Record<string, ReturnType<typeof setTimeout>> = {}

// 模块级 MSE init 段缓存：消除 mse_init 早于 VideoPreview 挂载到达的竞态。
// key = roomId, value = ArrayBuffer init segment
const _mseInitCache: Record<string, ArrayBuffer> = {}
const _mseInitCacheTime: Record<string, number> = {}

// 模块级 MSE media 段缓存：消除 mse_segment 早于 VideoPreview 挂载到达的竞态。
// key = roomId, value = ArrayBuffer media segments（最多 10 个，约 5 秒）。
// player 注册时会回放缓存，避免初始几秒丢帧导致黑屏。
const _mseSegmentCache: Record<string, ArrayBuffer[]> = {}
const _mseSegmentCacheTime: Record<string, number> = {}
const _MSE_SEGMENT_CACHE_MAX = 10
const _MSE_SEGMENT_CACHE_MAX_BYTES = 64 * 1024 * 1024
let _mseSegmentCacheBytes = 0
const _MSE_CACHE_TTL_MS = 5 * 60 * 1000
// mse_segment 接收 watchdog：按房间记录最后接收时间，超时按房间恢复预览
const _lastMseSegmentTimePerRoom: Map<string, number> = new Map()
const _mseWatchdogLastRecovery: Record<string, number> = {}
const _mseWatchdogFailCount: Record<string, number> = {}
const _MSE_WATCHDOG_TIMEOUT_MS = 10000
const _MSE_WATCHDOG_RECOVERY_COOLDOWN_MS = 15000
// 断流恢复尝试硬上限：连续超过该次数仍无分片则停止自动恢复并置 error 态，
// 防止挂机时对死流房间每 15s 无限重发 enable_preview（后端反复启停 FFmpeg）
const _MSE_WATCHDOG_MAX_FAILS = 3
let _mseWatchdogTimer: ReturnType<typeof setInterval> | null = null

/** 断连时写操作被丢弃的用户可见提示（useWebSocket.send 统一弹出） */
export const DISCONNECTED_SEND_WARNING = '未连接后端，操作未发送'

/** system_stats 节流：避免高频推送触发全树重渲染 */
const SYSTEM_STATS_MIN_INTERVAL_MS = 1000
let _lastSystemStatsAt = 0

/** 清理某房间的 MSE 缓存与重试定时器（预览关闭/房间移除时调用）。 */
export function clearMseRoomCache(roomId: string): void {
  delete _mseInitCache[roomId]
  delete _mseInitCacheTime[roomId]
  const segments = _mseSegmentCache[roomId]
  if (segments) {
    _mseSegmentCacheBytes -= segments.reduce((sum, item) => sum + item.byteLength, 0)
    _mseSegmentCacheBytes = Math.max(0, _mseSegmentCacheBytes)
  }
  delete _mseSegmentCache[roomId]
  delete _mseSegmentCacheTime[roomId]
  if (_mseInitRetryTimers[roomId]) {
    clearTimeout(_mseInitRetryTimers[roomId])
    delete _mseInitRetryTimers[roomId]
  }
  _lastMseSegmentTimePerRoom.delete(roomId)
  delete _mseWatchdogLastRecovery[roomId]
  delete _mseWatchdogFailCount[roomId]
  // 同步清理播放头快照，避免 rAF flush 携带死房间数据
  removePlayhead(roomId)
}

function _pruneExpiredMseCache(): void {
  const now = Date.now()
  for (const roomId of Object.keys(_mseInitCache)) {
    const ts = _mseInitCacheTime[roomId] ?? 0
    if (now - ts > _MSE_CACHE_TTL_MS) {
      delete _mseInitCache[roomId]
      delete _mseInitCacheTime[roomId]
    }
  }
  for (const roomId of Object.keys(_mseSegmentCache)) {
    const ts = _mseSegmentCacheTime[roomId] ?? 0
    if (now - ts > _MSE_CACHE_TTL_MS) {
      const segments = _mseSegmentCache[roomId]
      _mseSegmentCacheBytes -= segments?.reduce((sum, item) => sum + item.byteLength, 0) ?? 0
      delete _mseSegmentCache[roomId]
      delete _mseSegmentCacheTime[roomId]
    }
  }
  _mseSegmentCacheBytes = Math.max(0, _mseSegmentCacheBytes)
}

// 定期清理过期 MSE 缓存，避免 segment 缓存先过期时需等待下次 cache 调用才清理
setInterval(_pruneExpiredMseCache, 60_000)

export function _cacheMseInit(roomId: string, buffer: ArrayBuffer): void {
  _pruneExpiredMseCache()
  _mseInitCache[roomId] = buffer
  _mseInitCacheTime[roomId] = Date.now()
  // 最多缓存 20 个房间的 init 段，避免内存无限增长
  const keys = Object.keys(_mseInitCache)
  if (keys.length > 20) {
    const oldest = keys.sort((a, b) => (_mseInitCacheTime[a] ?? 0) - (_mseInitCacheTime[b] ?? 0))[0]
    if (oldest) clearMseRoomCache(oldest)
  }
}

export function _cacheMseSegment(roomId: string, buffer: ArrayBuffer): void {
  _pruneExpiredMseCache()
  if (!_mseSegmentCache[roomId]) {
    _mseSegmentCache[roomId] = []
  }
  _mseSegmentCacheTime[roomId] = Date.now()
  const arr = _mseSegmentCache[roomId]
  arr.push(buffer)
  _mseSegmentCacheBytes += buffer.byteLength
  // 超出上限丢弃最旧
  while (arr.length > _MSE_SEGMENT_CACHE_MAX) {
    const removed = arr.shift()
    if (removed) _mseSegmentCacheBytes -= removed.byteLength
  }
  // 跨房间总量也受限，优先淘汰最久未更新的房间。
  while (_mseSegmentCacheBytes > _MSE_SEGMENT_CACHE_MAX_BYTES) {
    const oldest = Object.keys(_mseSegmentCacheTime)
      .filter(key => (_mseSegmentCache[key]?.length ?? 0) > 0)
      .sort((a, b) => (_mseSegmentCacheTime[a] ?? 0) - (_mseSegmentCacheTime[b] ?? 0))[0]
    if (!oldest) break
    clearMseRoomCache(oldest)
  }
}

/** 取出并清空某房间的 media 段缓存，供 player 注册时回放。 */
function _drainMseSegmentCache(roomId: string): ArrayBuffer[] {
  const arr = _mseSegmentCache[roomId]
  if (!arr || arr.length === 0) return []
  delete _mseSegmentCache[roomId]
  _mseSegmentCacheBytes -= arr.reduce((sum, item) => sum + item.byteLength, 0)
  _mseSegmentCacheBytes = Math.max(0, _mseSegmentCacheBytes)
  return arr
}

/**
 * VideoPreview 注册 player 后调用此函数，回放在 player 未注册期间缓存的
 * media 段。返回 ArrayBuffer 数组，按时间顺序排列。
 * 这是消除"mse_segment 早于 player 注册到达"竞态的关键路径。
 */
export function drainPendingMseSegments(roomId: string): ArrayBuffer[] {
  return _drainMseSegmentCache(roomId)
}

/** 获取某房间缓存的 init 段（不解码，返回 ArrayBuffer 或 null）。
 * 供 VideoPreview 创建 player 时优先用缓存 init 段 feedInit，
 * 避免等待 request_mse_init 往返。 */
export function getMseInitCache(roomId: string): ArrayBuffer | null {
  return _mseInitCache[roomId] ?? null
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

function _coerceMsePayload(data: ArrayBuffer | string): ArrayBuffer {
  if (typeof data === 'string') {
    return _decodeBase64Segment(data)
  }
  return data
}

function _feedMseSegment(roomId: string, data: ArrayBuffer | string, type: 'init' | 'segment'): void {
  try {
    const buffer = _coerceMsePayload(data)
    // 缓存 init 段，供后续 VideoPreview 挂载时直接取用
    if (type === 'init') {
      _cacheMseInit(roomId, buffer)
    }

    const registry = window.__msePlayers as Record<string, any> | undefined
    const player = registry?.[roomId]
    if (!player) {
      // player 未注册时缓存 media 段，避免初始几秒丢帧。
      // init 段已通过 _cacheMseInit 缓存，此处只处理 media 段。
      if (type === 'segment') {
        _cacheMseSegment(roomId, buffer)
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
    // Reconcile the authoritative room snapshot before consuming incremental
    // runtime events after a reconnect; this prevents event gaps from leaving
    // health dimensions stuck in an old generation.
    setTimeout(() => {
      wsClient.send('get_rooms', {})
    }, 100)
    // 后端 on_connect 已主动推送 settings_loaded，无需重复请求
    // 延迟非关键请求，不阻塞首屏渲染
    setTimeout(() => {
      wsClient.send('get_system_stats', {})
    }, 500)
    // S1: WS 重连后自动恢复所有预览。重连成功后，对 store 中 preview_enabled=true
    // 的房间重新发送 enable_preview(mse)，确保后端 MseStreamer 重建（旧进程可能已随
    // 断连终止）。使用 setTimeout 避免阻塞 get_settings 的处理。
    setTimeout(() => {
      const rooms = useAppStore.getState().rooms
      const uiState = useAppStore.getState().uiState
      for (const room of rooms) {
        if (room.preview_enabled && room.is_connected && !uiState[room.room_id]?.mse_reconnecting) {
          console.log(`[WS] Reconnecting preview for room ${room.room_id} after WS reconnect`)
          wsClient.send('enable_preview', { room_id: room.room_id, enabled: true, mode: 'mse' })
        }
      }
    }, 1000)
  })
  const unsubDisconnected = wsClient.on('disconnected', () => {
    useAppStore.getState().setConnectionStatus('disconnected')
  })
  const unsubscribeBackendReady = window.electronAPI?.onBackendReady?.(() => {
    if (wsClient.connected) return
    useAppStore.getState().setConnectionStatus('connecting')
    wsClient.reconnect().catch(() => {
      useAppStore.getState().setConnectionStatus('disconnected')
    })
  })

  const handleRooms = (data: { rooms: any[] }) => {
    if (data && Array.isArray(data.rooms)) {
      // 房间被移除（不再出现在整表中）：清理其 MSE 缓存与播放头，
      // 只遍历现列表的发现不了已消失房间，必须与上一份快照对比
      const incomingIds = new Set(data.rooms.map((r) => r.room_id))
      for (const prev of useAppStore.getState().rooms) {
        if (!incomingIds.has(prev.room_id)) {
          clearMseRoomCache(prev.room_id)
          _lastRuntimeEventByRoom.delete(prev.room_id)
        }
      }
      const retryCounts = window.__mseInitRetryCount
      for (const room of data.rooms) {
        if (!room.preview_enabled) {
          if (retryCounts && retryCounts[room.room_id] !== undefined) {
            delete retryCounts[room.room_id]
          }
          clearMseRoomCache(room.room_id)
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
  const unsubRoomsUpdated = wsClient.on('rooms_updated', (data: any) => {
    // 检测广播序列号是否连续，发现丢消息时触发强制同步
    if (data && typeof data._seq === 'number') {
      if (_lastBroadcastSeq !== null && data._seq > _lastBroadcastSeq + 1) {
        // 序列号不连续，有消息丢失，请求全量同步
        wsClient.send('get_rooms', {})
      }
      _lastBroadcastSeq = data._seq
    }
    handleRooms(data)
  })
  const unsubRoomsLoaded = wsClient.on('rooms_loaded', handleRooms)

  const unsubRoomUpdated = wsClient.on('room_updated', (data: any) => {
    if (data && data.room_id) {
      // 如果有完整 room 对象，使用增量更新（P0-2: rooms_updated 增量更新）
      if (data.room) {
        useAppStore.getState().updateRoomIncremental(data.room_id, data.room as RoomSession)
      } else {
        // 否则使用字段级更新
        const { room_id, ...updates } = data as { room_id: string } & Partial<RoomSession>
        useAppStore.getState().updateRoom(room_id, updates)
      }
    }
  })

  const unsubRuntimeEvent = wsClient.on('runtime_event', (data: RuntimeEventPayload) => {
    if (data && data.room_id) {
      _applyRuntimeEvent(data)
    }
  })

  const unsubClipCompleted = wsClient.on('clip_completed', () => {
    window.electronAPI?.setProgressBar?.(-1)
  })

  const unsubClipFailed = wsClient.on('clip_failed', () => {
    window.electronAPI?.setProgressBar?.(-1)
  })

  const unsubExportProgress = wsClient.on('export_progress', (data: any) => {
    if (data?.percent !== undefined) {
      const progress = Math.max(0, Math.min(1, data.percent / 100))
      window.electronAPI?.setProgressBar?.(progress)
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

  // 重连过程中更新为 connecting，使状态显示与实际一致
  const unsubReconnecting = wsClient.on('reconnecting', () => {
    useAppStore.getState().setConnectionStatus('connecting')
  })

  // 重连次数耗尽：更新为 reconnect_failed，UI 可据此提示用户手动重连
  const unsubReconnectFailed = wsClient.on('reconnect_failed', () => {
    console.error('WebSocket reconnect failed: max attempts reached, backend may be unavailable')
    useAppStore.getState().setConnectionStatus('reconnect_failed')
  })

  const handleSystemStats = (data: any) => {
    if (!data || typeof data.cpu_percent !== 'number') return
    const now = Date.now()
    if (now - _lastSystemStatsAt < SYSTEM_STATS_MIN_INTERVAL_MS) return
    _lastSystemStatsAt = now
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

  const unsubRecordingStopped = wsClient.on('recording_stopped', (data: {
    room_id?: string
    reason?: string
    message?: string
  }) => {
    if (data?.room_id) {
      const updates: Record<string, any> = {
        is_recording: false,
        is_recording_starting: false,
        is_reconnecting: false,
      }
      if (data.message) {
        updates.last_error = data.message
      }
      useAppStore.getState().updateRoom(data.room_id, updates)
    }
  })

  const unsubMseInit = wsClient.on('mse_init', (data: { room_id: string; data: ArrayBuffer | string }) => {
    if (data?.room_id && data?.data) {
      _feedMseSegment(data.room_id, data.data, 'init')
    }
  })

  const unsubMseSegment = wsClient.on('mse_segment', (data: { room_id: string; data: ArrayBuffer | string }) => {
    if (data?.room_id && data?.data) {
      _lastMseSegmentTimePerRoom.set(data.room_id, Date.now())
      _mseWatchdogFailCount[data.room_id] = 0
      _feedMseSegment(data.room_id, data.data, 'segment')
    }
  })

  const unsubMseError = wsClient.on('mse_error', (data: { room_id: string; error: string; reason?: string }) => {
    if (data?.room_id) {
      console.warn(`MSE error for ${data.room_id}:`, data.error)
      const reason = data.reason || 'unknown'
      // 非 offline：保持 preview_enabled，以便后端自动重连后仍能挂载 VideoPreview 收分片
      useAppStore.getState().updateRoom(data.room_id, {
        mse_error: data.error,
        mse_reconnecting: undefined,
        preview_phase: 'error' as const,
        ...(reason === 'offline' ? { preview_enabled: false } : {}),
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

  const unsubMseReconnected = wsClient.on('mse_reconnected', (data: {
    room_id: string
    degraded?: boolean
    width?: number
    height?: number
    fps?: number
    reason?: string
  }) => {
    if (data?.room_id) {
      console.log(`MSE reconnected for ${data.room_id}`)
      useAppStore.getState().updateRoom(data.room_id, {
        mse_reconnecting: undefined,
        mse_error: undefined,
      })
      if (data.degraded && data.width && data.height) {
        useAppStore.getState().setPreviewDegradationBanner({
          width: data.width,
          height: data.height,
          fps: data.fps,
          reason: data.reason,
        })
      }
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
      const cachedInit = _mseInitCache[roomId]
      if (cachedInit) {
        try {
          const registry = window.__msePlayers as Record<string, any> | undefined
          const player = registry?.[roomId]
          if (player) {
            player.feedInit(cachedInit)
            console.log(`MSE init delivered from frontend cache for ${roomId}`)
            return
          }
        } catch (e) {
          console.warn(`MSE init cache delivery failed for ${roomId}:`, e)
        }
      }

      // 使用模块级 Map 跟踪重试次数，避免无限重试
      ;window.__mseInitRetryCount = window.__mseInitRetryCount || {}
      const counts = window.__mseInitRetryCount
      const count = (counts[roomId] || 0) + 1
      counts[roomId] = count
      if (count > 10) {
        console.warn(`MSE init retry exhausted for ${roomId}`)
        useAppStore.getState().updateRoom(roomId, { mse_error: 'MSE 流初始化超时，请重试预览' })
        delete counts[roomId]
        return
      }
      console.log(`MSE init not ready for ${roomId}, retrying (${count}/10) in ${count}s...`)
      const timerId = setTimeout(() => {
        delete _mseInitRetryTimers[roomId]
        wsClient.send('request_mse_init', { room_id: roomId })
      }, count * 1000)
      _mseInitRetryTimers[roomId] = timerId
    }
  })

  const unsubPreviewPhase = wsClient.on('preview_phase', (data: { room_id: string; phase: string }) => {
    if (data?.room_id) {
      useAppStore.getState().updateRoom(data.room_id, {
        preview_phase: data.phase as any,
      })
    }
  })

  // mse_segment 接收 watchdog：按房间检查，排除 refreshing_url/probing 阶段与暂停预览
  _mseWatchdogTimer = setInterval(() => {
    if (!wsClient.connected) return
    _pruneExpiredMseCache()
    const now = Date.now()
    const currentStore = useAppStore.getState()
    const rooms = currentStore.rooms
    for (const r of rooms) {
      if (!r.preview_enabled || !r.is_connected) continue
      if (r.preview_paused) continue
      const roomUi = currentStore.uiState[r.room_id]
      const previewPhase = roomUi?.preview_phase ?? r.preview_phase
      if (previewPhase && previewPhase !== 'streaming') continue
      if (roomUi?.mse_reconnecting) continue
      const lastRecv = _lastMseSegmentTimePerRoom.get(r.room_id)
      if (!lastRecv) continue
      const stall = now - lastRecv
      if (stall <= _MSE_WATCHDOG_TIMEOUT_MS) continue

      const lastRecovery = _mseWatchdogLastRecovery[r.room_id] ?? 0
      if (now - lastRecovery < _MSE_WATCHDOG_RECOVERY_COOLDOWN_MS) continue

      _mseWatchdogLastRecovery[r.room_id] = now
      // 恢复时不得把 lastRecv 伪装成 now，否则会掩盖持续 stall
      const fails = (_mseWatchdogFailCount[r.room_id] ?? 0) + 1
      _mseWatchdogFailCount[r.room_id] = fails
      console.warn(`[WS] Stall detected for room ${r.room_id} (${(stall / 1000).toFixed(1)}s), recovering preview...`)

      if (fails >= _MSE_WATCHDOG_MAX_FAILS) {
        // 恢复尝试耗尽：停止自动恢复，置 error 态提示用户手动处理。
        // phase 变更为 error 后本 watchdog 会跳过该房间（非 streaming），不再触发。
        console.warn(`[WS] Preview stall recovery exhausted for room ${r.room_id} (${fails}/${_MSE_WATCHDOG_MAX_FAILS}), disabling auto-recovery`)
        message.warning({ content: '预览持续中断，请手动重新开启预览', key: `mse-stall-${r.room_id}`, duration: 5 })
        useAppStore.getState().updateRoom(r.room_id, {
          preview_phase: 'error' as const,
          mse_error: '预览持续中断，请手动重新开启预览',
        })
        // 清理 watchdog 记录，用户手动重开后重新计数
        _lastMseSegmentTimePerRoom.delete(r.room_id)
        delete _mseWatchdogLastRecovery[r.room_id]
        delete _mseWatchdogFailCount[r.room_id]
      } else if (fails >= 2) {
        message.warning({ content: '预览恢复中', key: `mse-stall-${r.room_id}`, duration: 3 })
        wsClient.send('enable_preview', { room_id: r.room_id, enabled: true, mode: 'mse' })
      } else if (_mseInitCache[r.room_id]) {
        wsClient.send('request_mse_init', { room_id: r.room_id })
      } else {
        wsClient.send('enable_preview', { room_id: r.room_id, enabled: true, mode: 'mse' })
      }
    }
  }, 5000)

  return () => {
    unsubConnected()
    unsubDisconnected()
    unsubscribeBackendReady?.()
    unsubRoomsUpdated()
    unsubRoomsLoaded()
    unsubRoomUpdated()
    unsubRuntimeEvent()
    unsubClipCompleted()
    unsubClipFailed()
    unsubExportProgress()
    unsubSettingsLoaded()
    unsubSettingsResponse()
    unsubReconnecting()
    unsubReconnectFailed()
    unsubSystemStats()
    unsubDepStatus()
    unsubRecordingQueue()
    unsubRecordingStopped()
    unsubMseInit()
    unsubMseSegment()
    unsubMseError()
    unsubMseReconnecting()
    unsubMseReconnected()
    unsubEnablePreviewResp()
    unsubRequestMseInitResp()
    unsubPreviewPhase()
    if (_mseWatchdogTimer) {
      clearInterval(_mseWatchdogTimer)
      _mseWatchdogTimer = null
    }
    Object.values(_mseInitRetryTimers).forEach(clearTimeout)
    for (const key of Object.keys(_mseInitRetryTimers)) {
      delete _mseInitRetryTimers[key]
    }
    _lastRuntimeEventByRoom.clear()
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

  const send = useCallback((type: string, data: any): boolean => {
    const ok = wsClient.send(type, data)
    if (!ok) {
      message.warning(DISCONNECTED_SEND_WARNING)
    }
    return ok
  }, [])

  const on = useCallback((event: string, handler: (data: any) => void) => {
    return wsClient.on(event as any, handler)
  }, [])

  const reconnect = useCallback(() => {
    wsClient.reconnect()
  }, [])

  return { isConnected: connectionStatus === 'connected', connectionStatus, send, on, reconnect }
}
