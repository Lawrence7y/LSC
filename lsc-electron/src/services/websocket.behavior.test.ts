import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  describeWebSocketError,
  isHighFrequencyWsType,
  normalizeWebSocketPayload,
  shouldQueueWhenDisconnected,
  WebSocketClient,
} from './websocket'
import { tryParseMseBinaryFrame } from '@/utils/mseBinary'

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

describe('WS 断连队列策略', () => {
  it('幂等读操作断连时入队', () => {
    expect(shouldQueueWhenDisconnected('get_rooms')).toBe(true)
    expect(shouldQueueWhenDisconnected('get_settings')).toBe(true)
    expect(shouldQueueWhenDisconnected('get_system_stats')).toBe(true)
    expect(shouldQueueWhenDisconnected('check_dependencies')).toBe(true)
  })

  it('写操作 / MSE 握手不得入队（避免重连后重放开录或重放 init）', () => {
    expect(shouldQueueWhenDisconnected('start_recording')).toBe(false)
    expect(shouldQueueWhenDisconnected('request_mse_init')).toBe(false)
    expect(shouldQueueWhenDisconnected('export_clip')).toBe(false)
    expect(shouldQueueWhenDisconnected('save_settings')).toBe(false)
  })

  it('高频类型只影响 DEV 日志采样，不影响队列判定', () => {
    expect(isHighFrequencyWsType('heartbeat')).toBe(true)
    expect(isHighFrequencyWsType('mse_segment')).toBe(true)
    expect(isHighFrequencyWsType('rooms_updated')).toBe(true)
    expect(isHighFrequencyWsType('start_recording')).toBe(false)
    // request_mse_init 是握手，不是高频心跳
    expect(isHighFrequencyWsType('request_mse_init')).toBe(true)
  })

  it('断连时不可入队的类型 send 返回 false，调用方据此提示用户', async () => {
    globalThis.WebSocket = MockWebSocket as unknown as typeof WebSocket
    Object.defineProperty(window, 'electronAPI', {
      configurable: true,
      value: { getBackendWsToken: () => Promise.resolve('t') },
    })
    const client = new WebSocketClient('ws://127.0.0.1:1')
    // 未连接且不在认证排队阶段：写操作直接拒绝
    expect(client.send('start_recording', { room_id: 'r' })).toBe(false)
    expect(client.send('request_mse_init', { room_id: 'r' })).toBe(false)
    // 可入队的读操作返回 true（进发件箱）
    expect(client.send('get_rooms', {})).toBe(true)
  })
})

describe('normalizeWebSocketPayload：二进制帧先解码再 JSON', () => {
  it('字符串原样返回', async () => {
    expect(await normalizeWebSocketPayload('{"a":1}')).toBe('{"a":1}')
  })

  it('ArrayBuffer / TypedArray 用 UTF-8 解码（直接 JSON.parse 会得到 "[object Blob]"）', async () => {
    const text = JSON.stringify({ type: 'mse_init', data: { room_id: 'r1' } })
    const bytes = new TextEncoder().encode(text)
    expect(await normalizeWebSocketPayload(bytes.buffer.slice(0))).toBe(text)
    expect(await normalizeWebSocketPayload(bytes)).toBe(text)
    expect(await normalizeWebSocketPayload(new Uint8Array(bytes))).toBe(text)
  })

  it('Blob 走 .text() 异步解码', async () => {
    const text = '{"type":"mse_segment"}'
    expect(await normalizeWebSocketPayload(new Blob([text]))).toBe(text)
  })

  it('不支持的载荷类型抛 TypeError，而不是静默产出坏 JSON', () => {
    expect(() => normalizeWebSocketPayload(123 as unknown)).toThrow(TypeError)
  })
})

describe('tryParseMseBinaryFrame：MSE 二进制通道', () => {
  function buildV1(kind: 1 | 2, roomId: string, payload: Uint8Array): ArrayBuffer {
    const rid = new TextEncoder().encode(roomId)
    const buf = new Uint8Array(6 + rid.length + payload.length)
    buf[0] = 0x4d; buf[1] = 0x53; buf[2] = 0x45
    buf[3] = kind
    new DataView(buf.buffer).setUint16(4, rid.length, false)
    buf.set(rid, 6)
    buf.set(payload, 6 + rid.length)
    return buf.buffer
  }

  function buildV2(kind: 1 | 2, roomId: string, payload: Uint8Array, review: boolean, streamId = ''): ArrayBuffer {
    const rid = new TextEncoder().encode(roomId)
    const sid = new TextEncoder().encode(streamId)
    const buf = new Uint8Array(6 + sid.length + 2 + rid.length + payload.length)
    buf[0] = 0x4d; buf[1] = 0x53; buf[2] = 0x32
    buf[3] = kind
    buf[4] = review ? 1 : 0
    buf[5] = sid.length
    buf.set(sid, 6)
    const ridOff = 6 + sid.length
    new DataView(buf.buffer).setUint16(ridOff, rid.length, false)
    buf.set(rid, ridOff + 2)
    buf.set(payload, ridOff + 2 + rid.length)
    return buf.buffer
  }

  it('v1 MSE 帧解析出 init / segment 与 roomId', () => {
    const payload = Uint8Array.from([1, 2, 3])
    const init = tryParseMseBinaryFrame(buildV1(1, 'room-a', payload))
    expect(init).toMatchObject({ type: 'mse_init', roomId: 'room-a', channel: 'live' })
    expect(new Uint8Array(init!.payload)).toEqual(payload)

    const seg = tryParseMseBinaryFrame(buildV1(2, 'room-b', payload))
    expect(seg).toMatchObject({ type: 'mse_segment', roomId: 'room-b', channel: 'live' })
  })

  it('v2 MS2 带通道与 streamId', () => {
    const payload = Uint8Array.from([9])
    const live = tryParseMseBinaryFrame(buildV2(2, 'r', payload, false, 's1'))
    expect(live).toMatchObject({ type: 'mse_segment', roomId: 'r', channel: 'live', streamId: 's1' })
    const review = tryParseMseBinaryFrame(buildV2(1, 'r', payload, true, ''))
    expect(review).toMatchObject({ type: 'mse_init', roomId: 'r', channel: 'review', streamId: '' })
  })

  it('非 MSE 魔数 / 截断帧 / 未知 kind 返回 null，调用方回落 JSON 文本', () => {
    expect(tryParseMseBinaryFrame(new Uint8Array([1, 2, 3, 4, 5, 6]).buffer)).toBeNull()
    expect(tryParseMseBinaryFrame(new Uint8Array([0x4d, 0x53]).buffer)).toBeNull()
    // kind=9 未知
    const badKind = buildV1(1, 'r', Uint8Array.from([0]))
    new DataView(badKind).setUint8(3, 9)
    expect(tryParseMseBinaryFrame(badKind)).toBeNull()
  })

  it('房间 id 长度声明超过帧长时拒绝（防 OOB）', () => {
    const rid = new TextEncoder().encode('room')
    const buf = new Uint8Array(6 + rid.length)
    buf[0] = 0x4d; buf[1] = 0x53; buf[2] = 0x45; buf[3] = 2
    new DataView(buf.buffer).setUint16(4, 9999, false)
    buf.set(rid, 6)
    expect(tryParseMseBinaryFrame(buf.buffer)).toBeNull()
  })
})

describe('describeWebSocketError', () => {
  it('输出结构化字段且不序列化 URL', () => {
    const line = describeWebSocketError(
      { type: 'error', message: 'x', code: 1006, reason: 'r', wasClean: false },
      { readyState: 3 },
    )
    expect(line).toContain('event_code=1006')
    expect(line).toContain('ready_state=3')
    expect(line).not.toContain('ws://')
  })
})
