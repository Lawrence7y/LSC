import { afterEach, describe, it, expect, vi } from 'vitest'
import { describeWebSocketError, shouldQueueWhenDisconnected, WebSocketClient, isHighFrequencyWsType } from './websocket'

class MockWebSocket {
  static readonly CONNECTING = 0
  static readonly OPEN = 1
  static readonly CLOSING = 2
  static readonly CLOSED = 3
  static latest: MockWebSocket | null = null

  readonly sent: string[] = []
  readyState = MockWebSocket.CONNECTING
  binaryType = 'blob'
  onopen: (() => void | Promise<void>) | null = null
  onmessage: ((event: MessageEvent) => void) | null = null
  onclose: (() => void) | null = null
  onerror: ((event: Event) => void) | null = null

  constructor(_url: string) {
    MockWebSocket.latest = this
  }

  send(payload: string): void {
    this.sent.push(payload)
  }

  open(): void {
    this.readyState = MockWebSocket.OPEN
    void this.onopen?.()
  }

  close(): void {
    this.readyState = MockWebSocket.CLOSED
    this.onclose?.()
  }
}

const originalWebSocket = globalThis.WebSocket

afterEach(() => {
  vi.restoreAllMocks()
  MockWebSocket.latest = null
  globalThis.WebSocket = originalWebSocket
})

describe('websocket service', () => {
  describe('shouldQueueWhenDisconnected', () => {
    it('should return false for critical operations', () => {
      expect(shouldQueueWhenDisconnected('start_recording')).toBe(false)
      expect(shouldQueueWhenDisconnected('save_settings')).toBe(false)
      expect(shouldQueueWhenDisconnected('export_clip')).toBe(false)
    })

    it('should return true for idempotent read operations', () => {
      expect(shouldQueueWhenDisconnected('get_rooms')).toBe(true)
    })

    it('classifies heartbeat and room ticks as high-frequency', () => {
      expect(isHighFrequencyWsType('heartbeat')).toBe(true)
      expect(isHighFrequencyWsType('rooms_updated')).toBe(true)
      expect(isHighFrequencyWsType('mse_segment')).toBe(true)
      expect(isHighFrequencyWsType('start_recording')).toBe(false)
    })
  })

  describe('exports', () => {
    it('should export send function', () => {
      // Verify the module exports the expected functions
      expect(typeof shouldQueueWhenDisconnected).toBe('function')
    })

    it('formats structured error details without serializing a URL', () => {
      expect(describeWebSocketError(
        { type: 'error', message: '', code: 1006, reason: '', wasClean: false },
        { readyState: 3 },
      )).toContain('event_code=1006')
      expect(describeWebSocketError(new Event('error'), { readyState: 0 }))
        .toContain('ready_state=0')
    })
  })

  it('认证完成前排队业务消息，并保证 auth 是第一帧', async () => {
    let resolveToken!: (token: string) => void
    const tokenPromise = new Promise<string>((resolve) => {
      resolveToken = resolve
    })
    globalThis.WebSocket = MockWebSocket as unknown as typeof WebSocket
    Object.defineProperty(window, 'electronAPI', {
      configurable: true,
      value: {
        getBackendWsToken: () => tokenPromise,
      },
    })

    const client = new WebSocketClient('ws://127.0.0.1:9876')
    const connectPromise = client.connect()
    await Promise.resolve()
    const socket = MockWebSocket.latest!
    socket.open()

    expect(client.send('get_rooms', {})).toBe(true)
    expect(socket.sent).toEqual([])

    resolveToken('test-token')
    await connectPromise

    expect(socket.sent.map((payload) => JSON.parse(payload))).toEqual([
      { type: 'auth', token: 'test-token' },
      { type: 'get_rooms', data: {} },
    ])
    expect(client.connected).toBe(true)
  })

  describe('backend heartbeat watchdog', () => {
    it('僵死 episode 只广播一次 backend_crashed，恢复消息由 backend_revived 复位', () => {
      vi.useFakeTimers()
      try {
        const client = new WebSocketClient(null)
        const crashed = vi.fn()
        const revived = vi.fn()
        client.on('backend_crashed' as never, crashed)
        client.on('backend_revived' as never, revived)

        const anyClient = client as unknown as {
          _startHeartbeatCheck(): void
          _noteBackendAlive(): void
        }
        anyClient._startHeartbeatCheck()

        // 15s 阈值 + 5s 轮询相位：首个触发的 tick 在 20s（15s 整不满足 >）
        vi.advanceTimersByTime(21_000)
        expect(crashed).toHaveBeenCalledTimes(1)
        // 持续僵死：5s 轮询不得重复广播
        vi.advanceTimersByTime(10_000)
        expect(crashed).toHaveBeenCalledTimes(1)

        // 收到任意消息（后端恢复）→ backend_revived 恰好一次，之后不再重复
        anyClient._noteBackendAlive()
        expect(revived).toHaveBeenCalledTimes(1)
        anyClient._noteBackendAlive()
        expect(revived).toHaveBeenCalledTimes(1)
      } finally {
        vi.useRealTimers()
      }
    })
  })
})
