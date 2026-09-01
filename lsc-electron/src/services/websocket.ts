import { type WSPayloadMap, type WSMessageType } from '@/types'
import { resolveWebSocketUrl, type BackendElectronApi, type WebSocketEnv } from './websocketUrl'
import { tryParseMseBinaryFrame } from '@/utils/mseBinary'

type MessageHandler<T = unknown> = (data: T) => void

const DISCONNECTED_QUEUEABLE_TYPES = new Set([
  'get_rooms',
  'get_settings',
  'get_system_stats',
  'check_dependencies',
])

const HIGH_FREQ_WS_TYPES = new Set([
  'heartbeat',
  'rooms_updated',
  'room_updated',
  'system_stats',
  'mse_segment',
  'mse_init',
  'preview_frame',
  'preview_phase',
  'continuous_analysis_status',
  'export_progress',
  'analysis_progress',
  'get_rooms',
  'get_system_stats',
  'get_continuous_analysis_status',
  'request_mse_init',
])

export function shouldQueueWhenDisconnected(type: string): boolean {
  return DISCONNECTED_QUEUEABLE_TYPES.has(type)
}

export function isHighFrequencyWsType(type: string): boolean {
  return HIGH_FREQ_WS_TYPES.has(type)
}

const isDev = Boolean((import.meta as unknown as { env?: { DEV?: boolean } }).env?.DEV)

/** DEV 日志用：深拷贝并截断大字段，避免污染 console / 拖垮主线程。 */
function truncateLogData(data: unknown): unknown {
  const logData = JSON.parse(JSON.stringify(data || {}))
  if (typeof logData === 'object' && logData !== null) {
    for (const key of Object.keys(logData as Record<string, unknown>)) {
      const value = (logData as Record<string, unknown>)[key]
      if (typeof value === 'string' && value.length > 200) {
        ;(logData as Record<string, unknown>)[key] = `<string length=${value.length}>`
      } else if (Array.isArray(value) && value.length > 10) {
        ;(logData as Record<string, unknown>)[key] = `<array length=${value.length}>`
      }
    }
  }
  return logData
}

/** 将 WebSocket 载荷规范为 UTF-8 文本（兼容误发为二进制帧的 JSON）。 */
export function normalizeWebSocketPayload(data: unknown): string | Promise<string> {
  if (typeof data === 'string') {
    return data
  }
  if (data instanceof ArrayBuffer) {
    return new TextDecoder('utf-8').decode(data)
  }
  if (ArrayBuffer.isView(data)) {
    const view = data as ArrayBufferView
    return new TextDecoder('utf-8').decode(
      new Uint8Array(view.buffer, view.byteOffset, view.byteLength),
    )
  }
  if (typeof Blob !== 'undefined' && data instanceof Blob) {
    return data.text()
  }
  throw new TypeError(`Unsupported WebSocket payload type: ${Object.prototype.toString.call(data)}`)
}

/**
 * WebSocket 客户端：管理单条 WebSocket 连接的生命周期与消息分发。
 *
 * 职责：
 * - 通过 {@link connect} 建立连接，支持传入固定 URL 或从环境变量 / Electron API 动态解析。
 * - 通过 {@link on} 注册事件处理器，按消息类型分发；同一事件支持多个订阅者。
 * - 通过 {@link send} 发送消息，断连时自动入队，重连成功后批量 flush。
 * - 通过 {@link disconnect} 主动关闭连接并抑制自动重连。
 * - 通过 {@link reconnect} 手动重置重连计数器后重新发起连接。
 *
 * 设计要点：
 * - 幂等连接：多次调用 connect() 仅创建一条物理连接，pending Promise 复用防止并发竞争。
 * - 消息队列：断连期间消息缓存于 {@link messageQueue}（上限 100 条），重连成功后按序发送。
 * - 指数退避重连：失败后延迟从 1s 递增至 15s 封顶，最多尝试 20 次后停止并通知 UI。
 * - 手动关闭标志：disconnect() 设置 manualClose=true，避免 onclose 误触发自动重连。
 *
 * @remarks
 * 实例以单例形式导出（{@link wsClient}），整个应用共享一个 WebSocket 连接。
 */
export class WebSocketClient {
  private ws: WebSocket | null = null
  private url: string | null
  private resolvingUrl: Promise<string> | null = null
  private handlers: Map<string, Set<MessageHandler>> = new Map()
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private isConnected = false
  // 手动关闭标志，true 时 onclose 不触发重连
  private manualClose = false
  // 重连尝试次数，用于计算指数退避延迟
  private reconnectAttempts = 0
  // 最大重连次数上限，超出后停止重连，避免后端不可用时永久重连
  private readonly maxReconnectAttempts = 20
  // 断连期间的消息队列（上限 100）
  private messageQueue: string[] = []
  private readonly maxQueueSize = 100
  // 正在进行的连接 Promise：避免多处 useWebSocket() 同时调 connect()
  // 互相 close 对方刚建的连接，导致「WebSocket is closed before the connection
  // is established」的断连循环（表现为房间卡片在「占位符↔预览区」之间抽动）。
  private pendingConnect: Promise<void> | null = null
  // P3-2: 后端心跳检测
  private lastHeartbeat = 0
  private heartbeatCheckTimer: ReturnType<typeof setInterval> | null = null
  private readonly usesDynamicBackendUrl: boolean

  constructor(url: string | null = null) {
    this.url = url
    this.usesDynamicBackendUrl = url === null
  }

  private resolveUrl(): Promise<string> {
    if (this.url && !this.usesDynamicBackendUrl) {
      return Promise.resolve(this.url)
    }

    if (!this.resolvingUrl) {
      const env = (import.meta as unknown as { env?: WebSocketEnv }).env ?? {}
      const electronAPI = typeof window !== 'undefined'
        ? window.electronAPI as BackendElectronApi | undefined
        : undefined

      this.resolvingUrl = resolveWebSocketUrl(env, electronAPI)
        .then((url) => {
          // Electron 后端端口由启动时动态分配。不要缓存默认兜底地址，
          // 后端尚未就绪时的下一次重连必须重新向主进程查询真实端口。
          if (!this.usesDynamicBackendUrl) this.url = url
          return url
        })
        .finally(() => {
          this.resolvingUrl = null
        })
    }

    return this.resolvingUrl
  }

  /**
   * 建立 WebSocket 连接（幂等）。
   *
   * 连接流程：
   * 1. 若已有 OPEN 连接，直接返回。
   * 2. 若正在 CONNECTING 中，复用同一个 pending Promise，避免并发 connect() 互相 close。
   * 3. 解析目标 URL（传入 URL 或从 env/Electron API 动态获取）。
   * 4. 创建 WebSocket，注册 onopen / onmessage / onclose / onerror 回调。
   *
   * onopen 时：重置重连计数器，emit('connected')，flush 消息队列。
   * onmessage 时：JSON 解析为 {@link WSMessage}，按 type 分发到对应 handler。
   * onclose 时：emit('disconnected')；若非手动关闭，则启动指数退避重连。
   * onerror 时：reject 当前 Promise，并触发重连。
   *
   * @returns 连接建立的 Promise，失败时 reject 底层错误。
   */
  connect(): Promise<void> {
    // 重置手动关闭标志
    this.manualClose = false

    // 幂等守卫一：只有完成首帧认证后才算可复用的 OPEN 连接。
    // 物理连接已 OPEN 但认证 Token 尚未取回时，继续复用 pendingConnect，
    // 避免调用方误以为已经可以发送业务消息。
    if (this.isConnected && this.ws && this.ws.readyState === WebSocket.OPEN) {
      return Promise.resolve()
    }
    // 幂等守卫二：正在连接中（CONNECTING），复用同一个 Promise，避免并发
    // connect() 互相 close 对方刚建的连接。
    if (this.pendingConnect) {
      return this.pendingConnect
    }

    // 关闭已有但非 OPEN 的残留连接（CONNECTING/CLOSING/CLOSED），避免实例泄漏。
    // 注意：这里只在不是 OPEN 时清理，且用 manualClose 标志阻止 onclose 重连，
    // 避免清理动作触发 scheduleReconnect 与新建连接打架。
    if (this.ws) {
      this.ws.onclose = null
      this.ws.onerror = null
      this.ws.close()
      this.ws = null
    }

    this.pendingConnect = this.resolveUrl()
      .then((url) => new Promise<void>((resolve, reject) => {
        // D-9: 15 秒连接超时，防止 pendingConnect 永不 resolve
        const connectTimeout = setTimeout(() => {
          reject(new Error('WebSocket connect timeout (15s)'))
        }, 15000)

        const socket = new WebSocket(url)
        this.ws = socket
        // 二进制帧以 ArrayBuffer 同步解码，避免默认 Blob 触发 "[object Blob]" JSON 解析失败
        socket.binaryType = 'arraybuffer'

        socket.onopen = async () => {
          console.log('WebSocket connected, sending auth...')
          // 握手后首帧认证：发送 auth 消息（Token 不再通过 URL 传递）
          try {
            const electronAPI = typeof window !== 'undefined'
              ? (window.electronAPI as BackendElectronApi | undefined)
              : undefined
            const token = electronAPI?.getBackendWsToken
              ? await electronAPI.getBackendWsToken()
              : null

            if (!token?.trim()) {
              throw new Error('Backend WebSocket auth token is unavailable')
            }
            // IPC 取 Token 期间连接可能已被关闭或被另一条连接替换。
            if (this.ws !== socket || socket.readyState !== WebSocket.OPEN || this.manualClose) {
              throw new Error('WebSocket closed before authentication')
            }

            // 必须严格保证 auth 是连接上的第一帧。只有发送成功后才允许
            // connected 回调、定时任务和离线队列发送任何业务消息。
            socket.send(JSON.stringify({ type: 'auth', token }))
            this.isConnected = true
            this.reconnectAttempts = 0
            this.pendingConnect = null
            this.flushQueue()
            // P3-2: 启动心跳检测
            this._startHeartbeatCheck()
            this.emit('connected', null)
            clearTimeout(connectTimeout)
            resolve()
          } catch (error) {
            clearTimeout(connectTimeout)
            this.isConnected = false
            this.pendingConnect = null
            console.error('WebSocket authentication failed:', error)
            reject(error)
            if (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING) {
              socket.close()
            }
          }
        }

        socket.onmessage = (event) => {
          const handleText = (text: string) => {
            try {
              const message: { type: string; data: unknown } = JSON.parse(text)
              // P3-2: 更新心跳时间
              if (message.type === 'heartbeat') {
                this.lastHeartbeat = Date.now()
              }
              if (isDev) {
                if (!isHighFrequencyWsType(message.type)) {
                  console.log(`[WebSocket] Received message type=${message.type}, data=`, truncateLogData(message.data))
                }
              }
              this.emit(message.type, message.data)
            } catch (err) {
              console.error('Failed to parse WebSocket message:', err)
            }
          }

          try {
            // MSE 二进制帧优先：magic=MSE，避免把 fMP4 当 UTF-8 JSON
            if (event.data instanceof ArrayBuffer) {
              const mse = tryParseMseBinaryFrame(event.data)
              if (mse) {
                this.emit(mse.type, { room_id: mse.roomId, data: mse.payload })
                return
              }
            }
            const normalized = normalizeWebSocketPayload(event.data)
            if (typeof normalized === 'string') {
              handleText(normalized)
            } else {
              normalized.then(handleText).catch((err) => {
                console.error('Failed to read WebSocket binary message:', err)
              })
            }
          } catch (err) {
            console.error('Failed to normalize WebSocket message:', err)
          }
        }

        socket.onclose = () => {
          clearTimeout(connectTimeout)
          console.log('WebSocket disconnected')
          this.isConnected = false
          this.pendingConnect = null
          // 断连期间停止心跳检测，避免旧 interval 在断连 15s 后误报 backend_crashed
          this._stopHeartbeatCheck()
          this.emit('disconnected', null)
          // 手动关闭时不重连
          if (!this.manualClose) {
            this.scheduleReconnect()
          }
        }

        socket.onerror = (error) => {
          clearTimeout(connectTimeout)
          console.error('WebSocket error:', error)
          reject(error)
        }
      }))
      .catch((error) => {
        this.pendingConnect = null
        this.isConnected = false
        if (!this.manualClose) {
          this.scheduleReconnect()
        }
        throw error
      })

    return this.pendingConnect
  }

  private scheduleReconnect() {
    if (this.reconnectTimer) {
      return
    }
    // 超过最大重连次数，停止重连，通知 UI 显示"后端不可用"
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      console.error(`Max reconnect attempts (${this.maxReconnectAttempts}) reached, giving up`)
      this.emit('reconnect_failed', null)
      return
    }
    // 通知外部进入重连中状态（M12）
    this.emit('reconnecting', null)
    // 启动阶段快速探测，之后再进入指数退避，兼顾首屏速度与长期稳定性。
    const startupDelays = [150, 300, 600, 1000]
    const delay = this.reconnectAttempts < startupDelays.length
      ? startupDelays[this.reconnectAttempts]
      : Math.min(2000 * Math.pow(2, this.reconnectAttempts - startupDelays.length), 15000)
    this.reconnectAttempts++
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null
      console.log(`Attempting to reconnect (${this.reconnectAttempts}/${this.maxReconnectAttempts}, delay=${delay}ms)...`)
      this.connect().catch(() => {})
    }, delay)
  }

  // P3-2: 后端心跳检测
  private _startHeartbeatCheck(): void {
    // 先停旧 interval：每次重连成功都会调用本方法，直接覆盖引用会泄漏
    // 旧 interval（断连→重连循环后累积多个 5s 定时器，且断连期间会重复 emit）
    this._stopHeartbeatCheck()
    this.lastHeartbeat = Date.now()
    this.heartbeatCheckTimer = setInterval(() => {
      if (Date.now() - this.lastHeartbeat > 15000) {
        // 超过 15 秒无心跳，认为后端异常
        console.warn('[WebSocket] Backend heartbeat timeout')
        this.emit('backend_crashed', null)
      }
    }, 5000)
  }

  private _stopHeartbeatCheck(): void {
    if (this.heartbeatCheckTimer !== null) {
      clearInterval(this.heartbeatCheckTimer)
      this.heartbeatCheckTimer = null
    }
  }

  // 重连成功后将队列中暂存的消息依次发送
  private flushQueue() {
    while (this.messageQueue.length > 0) {
      const msg = this.messageQueue.shift()!
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.ws.send(msg)
      } else {
        // 连接又断了，放回队列头部等待下次重连
        this.messageQueue.unshift(msg)
        break
      }
    }
  }

  /**
   * 发送消息。若连接未就绪，消息会进入断连队列等待重连后 flush。
   *
   * 队列上限为 {@link maxQueueSize}（100 条），超出时丢弃最旧消息，保留最新。
   * 当 {@link ws} 处于 OPEN 状态时直接通过 WebSocket.send() 发出。
   *
   * @param type - 消息类型标识，用于 on() 路由分发
   * @param data - 消息载荷
   * @returns 已发送或已入队返回 true；断连且不可入队时返回 false
   */
  send(type: string, data: unknown): boolean {
    if (isDev) {
      if (!isHighFrequencyWsType(type) && type === 'align_preview_audio') {
        console.log(`[WebSocket] Sending message type=${type} (PCM base64 audio payload)`)
      } else if (!isHighFrequencyWsType(type)) {
        console.log(`[WebSocket] Sending message type=${type}, data=`, truncateLogData(data))
      }
    }

    const message = { type, data }
    const payload = JSON.stringify(message)
    // WebSocket.OPEN 仅表示 TCP/WebSocket 握手完成；首帧 Token 认证完成前，
    // 业务消息仍必须排队，否则会抢占后端要求的 auth 第一帧。
    if (!this.isConnected || !this.ws || this.ws.readyState !== WebSocket.OPEN) {
      if (!shouldQueueWhenDisconnected(type)) {
        console.warn(`[WebSocket] Dropping stale message while disconnected: ${type}`)
        return false
      }
      // 断连时入队，超出上限丢弃最旧的消息
      if (this.messageQueue.length >= this.maxQueueSize) {
        this.messageQueue.shift()
      }
      this.messageQueue.push(payload)
      console.warn('WebSocket not connected, queuing message')
      return true
    }
    this.ws.send(payload)
    return true
  }

  /**
   * 注册事件处理器。
   *
   * @param event - 事件名称
   * @param handler - 回调函数，接收消息 data
   * @returns 取消订阅函数，调用后移除该 handler
   */
  on<T extends WSMessageType>(event: T, handler: (data: WSPayloadMap[T]) => void): () => void {
    return this._on(event, handler as MessageHandler)
  }

  private _on(event: string, handler: MessageHandler): () => void {
    if (!this.handlers.has(event)) {
      this.handlers.set(event, new Set())
    }
    this.handlers.get(event)!.add(handler)

    // 注册 connected 事件时若已连接，立即同步触发一次，
    // 避免组件重新挂载后错过历史 onopen 事件而长期显示「连接中」。
    if (event === 'connected' && this.isConnected) {
      handler(null)
    }

    // 返回取消订阅函数
    return () => {
      this.handlers.get(event)?.delete(handler)
    }
  }

  private emit(event: string, data: any): void {
    this.handlers.get(event)?.forEach(handler => handler(data))
  }

  /**
   * 主动断开连接，并抑制后续自动重连。
   *
   * 操作：
   * 1. 设置 manualClose=true，使 onclose 回调跳过 scheduleReconnect。
   * 2. 清除重连定时器。
   * 3. 移除 WebSocket 事件监听后 close()，释放底层 TCP 连接。
   * 4. 清空 pendingConnect，重置连接状态。
   */
  disconnect(): void {
    this.manualClose = true
    this._stopHeartbeatCheck()  // P3-2: 清理心跳检测
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    if (this.ws) {
      this.ws.onclose = null
      this.ws.close()
      this.ws = null
    }
    this.pendingConnect = null
    this.messageQueue = []
    this.isConnected = false
  }

  /**
   * 手动重连：重置重连计数器后立即调用 connect()。
   *
   * 供 UI 在收到 `reconnect_failed` 事件后，用户手动点击"重试"时使用。
   * 会清除可能存在的退避定时器，将 reconnectAttempts 归零后发起新连接。
   *
   * @returns 连接建立的 Promise
   */
  reconnect(): Promise<void> {
    this.reconnectAttempts = 0
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    return this.connect()
  }

  get connected(): boolean {
    return this.isConnected
  }
}

export const wsClient = new WebSocketClient()
