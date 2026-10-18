import { describe, expect, it, vi } from 'vitest'
import { MseCacheStore } from './mseCacheStore'

const KB = 1024
function buf(bytes: number): ArrayBuffer {
  return new ArrayBuffer(bytes)
}

describe('MseCacheStore TTL', () => {
  it('恰好到点不淘汰，超过才淘汰（避免边界抖动）', () => {
    const store = new MseCacheStore({ ttlMs: 1000 })
    store.cacheInit('r1', buf(4), 0)
    store.pruneExpired(1000)
    expect(store.getInit('r1')).not.toBeNull()
    store.pruneExpired(1001)
    expect(store.getInit('r1')).toBeNull()
  })

  it('段缓存过期时字节计数同步回退', () => {
    const store = new MseCacheStore({ ttlMs: 1000 })
    store.cacheSegment('r1', buf(KB), 0)
    store.cacheSegment('r1', buf(KB), 0)
    expect(store.stats().segmentBytes).toBe(2 * KB)
    store.pruneExpired(1001)
    expect(store.stats()).toEqual({ initRooms: 0, segmentRooms: 0, segmentBytes: 0 })
  })

  it('默认 TTL 是 5 分钟', () => {
    expect(new MseCacheStore().getTtlMs()).toBe(5 * 60 * 1000)
  })
})

describe('MseCacheStore 单房间段数上限', () => {
  it('超过 10 段丢最旧的，并保留到达顺序', () => {
    const store = new MseCacheStore({ segmentMaxPerRoom: 3 })
    const first = buf(KB)
    store.cacheSegment('r1', first, 1)
    store.cacheSegment('r1', buf(KB), 2)
    store.cacheSegment('r1', buf(KB), 3)
    store.cacheSegment('r1', buf(KB), 4)
    expect(store.stats().segmentBytes).toBe(3 * KB)  // 丢弃最旧那段时字节计数同步回退
    const drained = store.drainSegments('r1')
    expect(drained).toHaveLength(3)
    expect(drained[0]).not.toBe(first)   // 最旧的那段已被丢弃
    expect(store.stats().segmentBytes).toBe(0)
  })

  it('drain 之后同房间再取为空，且不会重复扣字节', () => {
    const store = new MseCacheStore()
    store.cacheSegment('r1', buf(KB), 1)
    expect(store.drainSegments('r1')).toHaveLength(1)
    expect(store.drainSegments('r1')).toEqual([])
    expect(store.stats().segmentBytes).toBe(0)
  })
})

describe('MseCacheStore 跨房间总字节上限', () => {
  it('超总量时淘汰"最久未更新"的房间，保留当前正在收流的那间', () => {
    const store = new MseCacheStore({ segmentMaxBytes: 4 * KB })
    store.cacheSegment('A', buf(2 * KB), 10)
    store.cacheSegment('B', buf(2 * KB), 20)
    expect(store.stats().segmentBytes).toBe(4 * KB)
    store.cacheSegment('A', buf(2 * KB), 30)   // 6KB > 4KB ⇒ 该淘汰 B（t=20 比 A 的上一次 t=10 新）
    expect(store.stats().segmentBytes).toBe(4 * KB)
    expect(store.drainSegments('B')).toEqual([])
    expect(store.drainSegments('A')).toHaveLength(2)
  })

  it('淘汰路径不会把字节计数压成负数', () => {
    const store = new MseCacheStore({ segmentMaxBytes: KB })
    store.cacheSegment('A', buf(4 * KB), 1)
    const stats = store.stats()
    expect(stats.segmentBytes).toBeGreaterThanOrEqual(0)
    expect(stats.segmentRooms).toBeLessThanOrEqual(1)
  })
})

describe('MseCacheStore 房间数上限与清理回调', () => {
  it('init 缓存最多 N 个房间，超了淘汰最旧的', () => {
    const store = new MseCacheStore({ initMaxRooms: 2 })
    store.cacheInit('r1', buf(4), 10)
    store.cacheInit('r2', buf(4), 20)
    store.cacheInit('r3', buf(4), 30)
    expect(store.stats().initRooms).toBe(2)
    expect(store.getInit('r1')).toBeNull()
    expect(store.getInit('r3')).not.toBeNull()
  })

  it('显式 clearRoom 与过期回收都会回调 onRoomCleared；只清 init 不回调', () => {
    const cleared = vi.fn()
    const store = new MseCacheStore({ ttlMs: 100, onRoomCleared: cleared })
    store.cacheInit('r1', buf(4), 0)
    store.cacheSegment('r1', buf(KB), 0)
    store.clearRoom('r1')
    expect(cleared).toHaveBeenCalledWith('r1')
    expect(store.stats()).toEqual({ initRooms: 0, segmentRooms: 0, segmentBytes: 0 })

    cleared.mockClear()
    store.cacheInit('r2', buf(4), 0)
    store.dropInit('r2')
    expect(cleared).not.toHaveBeenCalled()

    cleared.mockClear()
    store.cacheInit('r3', buf(4), 0)
    store.pruneExpired(101)
    expect(cleared).toHaveBeenCalledWith('r3')
  })

  it('清一个不存在的房间是幂等的', () => {
    const store = new MseCacheStore()
    expect(() => {
      store.clearRoom('nope')
      store.clearRoom('nope')
      store.drainSegments('nope')
    }).not.toThrow()
    expect(store.getInit('nope')).toBeNull()
  })
})
