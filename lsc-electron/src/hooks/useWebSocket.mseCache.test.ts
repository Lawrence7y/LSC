import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import {
  _cacheMseInit,
  _cacheMseSegment,
  clearMseRoomCache,
  drainPendingMseSegments,
  getMseInitCache,
} from './useWebSocket'

function bufferOf(mb: number): ArrayBuffer {
  return new ArrayBuffer(mb * 1024 * 1024)
}

describe('MSE cache bounds', () => {
  beforeEach(() => {
    for (let i = 0; i < 40; i++) {
      clearMseRoomCache(`room-${i}`)
    }
  })

  afterEach(() => {
    for (let i = 0; i < 40; i++) {
      clearMseRoomCache(`room-${i}`)
    }
  })

  it('caps per-room segment count', () => {
    const roomId = 'room-caps-segments'
    for (let i = 0; i < 30; i++) {
      _cacheMseSegment(roomId, bufferOf(1))
    }
    const pending = drainPendingMseSegments(roomId)
    expect(pending.length).toBe(10)
  })

  it('caps total cached bytes and evicts oldest rooms', () => {
    for (let i = 0; i < 20; i++) {
      _cacheMseSegment(`room-${i}`, bufferOf(5))
    }
    let total = 0
    for (let i = 0; i < 20; i++) {
      total += drainPendingMseSegments(`room-${i}`).reduce((sum, b) => sum + b.byteLength, 0)
    }
    expect(total).toBeLessThanOrEqual(64 * 1024 * 1024)
    expect(getMseInitCache('room-0')).toBeNull()
  })

  it('caps number of cached rooms', () => {
    for (let i = 0; i < 30; i++) {
      _cacheMseInit(`room-${i}`, bufferOf(1))
    }
    let kept = 0
    for (let i = 0; i < 30; i++) {
      if (getMseInitCache(`room-${i}`)) kept++
    }
    expect(kept).toBeLessThanOrEqual(20)
  })
})
