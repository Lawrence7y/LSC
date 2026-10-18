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

  it('断连期间发件箱已满：拒绝最新一条并返回 false，不得挤掉更早那条', async () => {
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
    // 上限压到 2：三条就能覆盖"刚好满"与"再满一条"
    ;(client as unknown as { maxQueueSize: number }).maxQueueSize = 2

    expect(client.send('get_rooms', { n: 1 })).toBe(true)
    expect(client.send('get_settings', {})).toBe(true)
    // 满了：第三条必须返回 false（调用方据此弹 DISCONNECTED_SEND_WARNING），
    // 而不是静默把第一条挤掉却仍然返回 true
    expect(client.send('get_rooms', { n: 3 })).toBe(false)

    resolveToken('test-token')
    await connectPromise

    expect(socket.sent.map((payload) => JSON.parse(payload).type)).toEqual([
      'auth',
      'get_rooms',
      'get_settings',
    ])
    expect(JSON.parse(socket.sent[1]).data).toEqual({ n: 1 })
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

  describe('广播序列号缺号检测（CLAUDE.md §2.1）', () => {
    const driveSeq = (
      client: WebSocketClient,
      seq: number,
      coalesced?: number,
    ) => {
      const anyClient = client as unknown as {
        _noteBroadcastSeq(type: string, data: unknown): void
      }
      anyClient._noteBroadcastSeq(
        'clip_completed',
        coalesced === undefined ? { _seq: seq } : { _seq: seq, _coalesced: coalesced },
      )
    }

    const getRoomsCalls = () => {
      const socket = MockWebSocket.latest!
      return socket.sent.filter((payload) => JSON.parse(payload).type === 'get_rooms').length
    }

    const connectedClient = async () => {
      let resolveToken!: (token: string) => void
      const tokenPromise = new Promise<string>((resolve) => {
        resolveToken = resolve
      })
      globalThis.WebSocket = MockWebSocket as unknown as typeof WebSocket
      Object.defineProperty(window, 'electronAPI', {
        configurable: true,
        value: { getBackendWsToken: () => tokenPromise },
      })
      const client = new WebSocketClient('ws://127.0.0.1:9876')
      const connectPromise = client.connect()
      await Promise.resolve()
      const socket = MockWebSocket.latest!
      socket.open()
      resolveToken('test-token')
      await connectPromise
      return { client, socket }
    }

    it('连续序列号不触发全量同步', async () => {
      const { client } = await connectedClient()
      driveSeq(client, 1)
      driveSeq(client, 2)
      driveSeq(client, 3)
      expect(getRoomsCalls()).toBe(0)
    })

    it('无法解释的正向缺口触发 get_rooms 全量同步', async () => {
      const { client } = await connectedClient()
      driveSeq(client, 1)
      driveSeq(client, 4) // 2、3 无任何合并记账 ⇒ 判定真丢关键消息
      expect(getRoomsCalls()).toBe(1)
    })

    it('设计内塌号（_coalesced 记账）不得谎报缺号', async () => {
      const { client } = await connectedClient()
      driveSeq(client, 2, 1) // 幸存者 seq=2，合并掉 1 条（seq 1）
      driveSeq(client, 5, 2) // 5 之前有 3、4 两条合并塌号
      expect(getRoomsCalls()).toBe(0)
    })

    it('预算耗尽的超额缺口仍然报警（塌号解释不了全部缺口）', async () => {
      const { client } = await connectedClient()
      driveSeq(client, 2, 1) // 预算解释 seq 1
      driveSeq(client, 6, 0) // 3、4、5 中只有 1 条可解释 ⇒ 剩 2 条真丢
      expect(getRoomsCalls()).toBe(1)
    })

    it('合并幸存者乱序到达（seq 回落）不得报警', async () => {
      const { client } = await connectedClient()
      driveSeq(client, 5)
      driveSeq(client, 3) // 另一桶的幸存者后到，seq 更小：last-value 合并的常态
      driveSeq(client, 6)
      expect(getRoomsCalls()).toBe(0)
    })

    it('不带 _seq 的消息（rooms_updated 等可丢弃遥测）不参与检测', async () => {
      const { client } = await connectedClient()
      const anyClient = client as unknown as {
        _noteBroadcastSeq(type: string, data: unknown): void
      }
      anyClient._noteBroadcastSeq('rooms_updated', { rooms: [] })
      anyClient._noteBroadcastSeq('heartbeat', { ts: 1 })
      driveSeq(client, 7)
      expect(getRoomsCalls()).toBe(0)
    })

    it('重连后序列号从 0 重新发号不误报（认证成功即复位）', async () => {
      const { client, socket } = await connectedClient()
      driveSeq(client, 100)
      expect(getRoomsCalls()).toBe(0)

      // 后端重启：新连接、新发号
      socket.close()
      await new Promise((resolve) => setTimeout(resolve, 0))
      const socket2 = MockWebSocket.latest!
      socket2.open()
      await Promise.resolve()
      await Promise.resolve()
      // 复位后 1、3 是"自 1 起跳 1"的真缺口 ⇒ 应报警；若无复位（last 仍是
      // 100），1 和 3 都会被当成乱序幸存者而永不报警
      driveSeq(client, 1)
      driveSeq(client, 3)
      expect(getRoomsCalls()).toBe(1)
    })
  })

  describe('事件分发逐 handler 隔离（2026-09-24 修复）', () => {
    const connectedClient = async () => {
      let resolveToken!: (token: string) => void
      const tokenPromise = new Promise<string>((resolve) => {
        resolveToken = resolve
      })
      globalThis.WebSocket = MockWebSocket as unknown as typeof WebSocket
      Object.defineProperty(window, 'electronAPI', {
        configurable: true,
        value: { getBackendWsToken: () => tokenPromise },
      })
      const client = new WebSocketClient('ws://127.0.0.1:9876')
      const connectPromise = client.connect()
      await Promise.resolve()
      const socket = MockWebSocket.latest!
      socket.open()
      resolveToken('test-token')
      await connectPromise
      return { client, socket }
    }

    it('一个订阅者抛异常不得掐断同类型其余监听者', async () => {
      const { client, socket } = await connectedClient()
      const received: unknown[] = []
      client.on('rooms_updated', () => {
        throw new Error('boom from first subscriber')
      })
      client.on('rooms_updated', (data) => received.push(data))

      const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
      socket.onmessage!({ data: JSON.stringify({ type: 'rooms_updated', data: { rooms: [] } }) } as MessageEvent)

      expect(received).toEqual([{ rooms: [] }])
      expect(errorSpy).toHaveBeenCalledWith(
        expect.stringContaining('handler failed for event=rooms_updated'),
        expect.any(Error),
      )
    })

    it('非 JSON 帧报解析失败，不得误伤分发路径', async () => {
      const { socket } = await connectedClient()
      const received: unknown[] = []
      const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})

      socket.onmessage!({ data: '<html>not json</html>' } as MessageEvent)

      expect(errorSpy).toHaveBeenCalledWith(
        'Failed to parse WebSocket message:',
        expect.anything(),
      )
      expect(received).toEqual([])
    })
  })
})
