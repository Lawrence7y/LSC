import { type WSPayloadMap, type WSMessageType } from '@/types'
import { resolveWebSocketUrl, type BackendElectronApi, type WebSocketEnv } from './websocketUrl'

type MessageHandler<T = unknown> = (data: T) => void

const DISCONNECTED_QUEUEABLE_TYPES = new Set([
  'get_rooms',
  'get_settings',
  'get_system_stats',
  'check_dependencies',
])

export function shouldQueueWhenDisconnected(type: string): boolean {
  return DISCONNECTED_QUEUEABLE_TYPES.has(type)
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
class WebSocketClient {
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

  constructor(url: string | null = null) {
    this.url = url
  }

  private resolveUrl(): Promise<string> {
    if (this.url) {
      return Promise.resolve(this.url)
    }

    if (!this.resolvingUrl) {
      const env = (import.meta as unknown as { env?: WebSocketEnv }).env ?? {}
      const electronAPI = typeof window !== 'undefined'
        ? window.electronAPI as BackendElectronApi | undefined
        : undefined

      this.resolvingUrl = resolveWebSocketUrl(env, electronAPI)
        .then((url) => {
          this.url = url
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

    // 幂等守卫一：已有连接且处于 OPEN，直接复用，不关旧连接、不新建。
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
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

        this.ws = new WebSocket(url)

        this.ws.onopen = () => {
          clearTimeout(connectTimeout)
          console.log('WebSocket connected')
          this.isConnected = true
          this.reconnectAttempts = 0
          this.emit('connected', null)
          // 重连成功后 flush 队列
          this.flushQueue()
          this.pendingConnect = null
          resolve()
        }

        this.ws.onmessage = (event) => {
          try {
            const message: { type: string; data: unknown } = JSON.parse(event.data)
            if (message.type === 'mse_segment' || message.type === 'mse_init' || message.type === 'preview_frame') {
              if ((import.meta as unknown as { env?: { DEV?: boolean } }).env?.DEV) {
                console.log(`[WebSocket] Received message type=${message.type} (length: ${event.data.length})`)
              }
            } else {
              const logData = JSON.parse(JSON.stringify(message.data || {}))
              if (typeof logData === 'object' && logData !== null) {
                for (const key of Object.keys(logData)) {
                  if (typeof logData[key] === 'string' && logData[key].length > 200) {
                    logData[key] = `<string length=${logData[key].length}>`
                  } else if (Array.isArray(logData[key]) && logData[key].length > 10) {
                    logData[key] = `<array length=${logData[key].length}>`
                  }
                }
              }
              console.log(`[WebSocket] Received message type=${message.type}, data=`, logData)
            }
            this.emit(message.type, message.data)
          } catch (err) {
            console.error('Failed to parse WebSocket message:', err)
          }
        }

        this.ws.onclose = () => {
          clearTimeout(connectTimeout)
          console.log('WebSocket disconnected')
          this.isConnected = false
          this.pendingConnect = null
          this.emit('disconnected', null)
          // 手动关闭时不重连
          if (!this.manualClose) {
            this.scheduleReconnect()
          }
        }

        this.ws.onerror = (error) => {
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
      clearTimeout(this.reconnectTimer)
    }
    // 超过最大重连次数，停止重连，通知 UI 显示"后端不可用"
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      console.error(`Max reconnect attempts (${this.maxReconnectAttempts}) reached, giving up`)
      this.emit('reconnect_failed', null)
      return
    }
    // 通知外部进入重连中状态（M12）
    this.emit('reconnecting', null)
    // 指数退避：1s→2s→4s→8s→15s 封顶
    const delay = Math.min(1000 * Math.pow(2, this.reconnectAttempts), 15000)
    this.reconnectAttempts++
    this.reconnectTimer = setTimeout(() => {
      console.log(`Attempting to reconnect (${this.reconnectAttempts}/${this.maxReconnectAttempts}, delay=${delay}ms)...`)
      this.connect().catch(() => {})
    }, delay)
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
   */
  send(type: string, data: unknown): void {
    if (type === 'align_preview_audio') {
      console.log(`[WebSocket] Sending message type=${type} (PCM base64 audio payload)`)
    } else {
      const logData = JSON.parse(JSON.stringify(data || {}))
      if (typeof logData === 'object' && logData !== null) {
        for (const key of Object.keys(logData)) {
          if (typeof logData[key] === 'string' && logData[key].length > 200) {
            logData[key] = `<string length=${logData[key].length}>`
          } else if (Array.isArray(logData[key]) && logData[key].length > 10) {
            logData[key] = `<array length=${logData[key].length}>`
          }
        }
      }
      console.log(`[WebSocket] Sending message type=${type}, data=`, logData)
    }

    const message = { type, data }
    const payload = JSON.stringify(message)
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      if (!shouldQueueWhenDisconnected(type)) {
        console.warn(`[WebSocket] Dropping stale message while disconnected: ${type}`)
        return
      }
      // 断连时入队，超出上限丢弃最旧的消息
      if (this.messageQueue.length >= this.maxQueueSize) {
        this.messageQueue.shift()
      }
      this.messageQueue.push(payload)
      console.warn('WebSocket not connected, queuing message')
      return
    }
    this.ws.send(payload)
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
