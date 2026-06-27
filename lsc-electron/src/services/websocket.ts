import { WSMessage } from '@/types'
import { resolveWebSocketUrl, type BackendElectronApi, type WebSocketEnv } from './websocketUrl'

type MessageHandler = (data: any) => void

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
        this.ws = new WebSocket(url)

        this.ws.onopen = () => {
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
            const message: WSMessage = JSON.parse(event.data)
            this.emit(message.type, message.data)
          } catch (err) {
            console.error('Failed to parse WebSocket message:', err)
          }
        }

        this.ws.onclose = () => {
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

  send(type: string, data: any): void {
    const message: WSMessage = { type, data }
    const payload = JSON.stringify(message)
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
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

  on(event: string, handler: MessageHandler): () => void {
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
    this.isConnected = false
  }

  /**
   * 手动重连：重置重连计数器后发起连接。
   * 供 UI 在 reconnect_failed 后手动重试使用。
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
