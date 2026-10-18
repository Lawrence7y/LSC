/**
 * MSE init / media 段缓存的**有界存储**（2026-09-19 从 `hooks/useWebSocket.ts` 的模块级
 * 全局字典搬出，债务报告 §11.29）。
 *
 * 为什么单独成模块：这里的每条判据都会决定"内存会不会长住"和"新房间能不能拿到 init 段"：
 * - TTL：5 分钟未更新的房间条目要能被丢掉；
 * - 单房间段数上限 10（约 5 秒）；
 * - **跨房间总字节上限**：超了要淘汰"最久未更新"的房间，而不是当前正在收流的那间；
 * - 字节计数必须随 drop / drain / clear 同步回退，且不得变成负数
 *   （负数会让总字节上限**永久失效**）。
 * 原来这些判据只有两条文本守卫守着（断言 `_MSE_CACHE_TTL_MS`、`delete _mseSegmentCache[roomId]`
 * 这些名字还在源码里）—— 把上限判断写成 `<` 而不是 `>` 它也照样绿。
 *
 * 时间从参数注入（`now`），因此测试不需要 mock `Date.now`。
 */

const DEFAULT_INIT_MAX_ROOMS = 20
const DEFAULT_SEGMENT_MAX_PER_ROOM = 10
const DEFAULT_SEGMENT_MAX_BYTES = 64 * 1024 * 1024
const DEFAULT_TTL_MS = 5 * 60 * 1000

export interface MseCacheStoreOptions {
  initMaxRooms?: number
  segmentMaxPerRoom?: number
  segmentMaxBytes?: number
  ttlMs?: number
  /**
   * 某房间的条目被清（过期 / 淘汰 / 显式关闭）时回调。
   * `useWebSocket` 用它顺带清掉 watchdog / 重试计数等旁路状态，
   * 避免"缓存已清、旁路状态还在"导致新 epoch 继承旧失败的次数上限。
   */
  onRoomCleared?: (roomId: string) => void
}

export interface MseCacheStats {
  initRooms: number
  segmentRooms: number
  segmentBytes: number
}

export class MseCacheStore {
  private readonly initBuffers = new Map<string, ArrayBuffer>()
  private readonly initAt = new Map<string, number>()
  private readonly segments = new Map<string, ArrayBuffer[]>()
  private readonly segmentAt = new Map<string, number>()
  private segmentBytes = 0

  private readonly initMaxRooms: number
  private readonly segmentMaxPerRoom: number
  private readonly segmentMaxBytes: number
  private readonly ttlMs: number
  private readonly onRoomCleared?: (roomId: string) => void

  constructor(opts: MseCacheStoreOptions = {}) {
    this.initMaxRooms = opts.initMaxRooms ?? DEFAULT_INIT_MAX_ROOMS
    this.segmentMaxPerRoom = opts.segmentMaxPerRoom ?? DEFAULT_SEGMENT_MAX_PER_ROOM
    this.segmentMaxBytes = opts.segmentMaxBytes ?? DEFAULT_SEGMENT_MAX_BYTES
    this.ttlMs = opts.ttlMs ?? DEFAULT_TTL_MS
    this.onRoomCleared = opts.onRoomCleared
  }

  getTtlMs(): number {
    return this.ttlMs
  }

  cacheInit(roomId: string, buffer: ArrayBuffer, now: number): void {
    this.pruneExpired(now)
    this.initBuffers.set(roomId, buffer)
    this.initAt.set(roomId, now)
    while (this.initBuffers.size > this.initMaxRooms) {
      const oldest = this.oldestRoom(this.initAt)
      if (!oldest) break
      this.dropInit(oldest)
    }
  }

  getInit(roomId: string): ArrayBuffer | null {
    return this.initBuffers.get(roomId) ?? null
  }

  cacheSegment(roomId: string, buffer: ArrayBuffer, now: number): void {
    this.pruneExpired(now)
    const arr = this.segments.get(roomId) ?? []
    arr.push(buffer)
    this.segments.set(roomId, arr)
    this.segmentBytes += buffer.byteLength
    this.segmentAt.set(roomId, now)
    // 单房间超量：丢最旧的几段
    while (arr.length > this.segmentMaxPerRoom) {
      const removed = arr.shift()
      if (removed) this.segmentBytes = clampNonNegative(this.segmentBytes - removed.byteLength)
    }
    // 跨房间总量超限：淘汰"最久未更新"的房间（当前房间刚写过时间戳，天然排最后）。
    // 候选必须**真的还有段** —— 否则 clearRoom 什么也没释放，这个 while 不会终止。
    while (this.segmentBytes > this.segmentMaxBytes) {
      const candidate = [...this.segmentAt.keys()].filter(
        roomId => (this.segments.get(roomId)?.length ?? 0) > 0,
      )
      if (candidate.length === 0) break
      let oldest = candidate[0]
      for (const roomId of candidate) {
        if ((this.segmentAt.get(roomId) ?? 0) < (this.segmentAt.get(oldest) ?? 0)) oldest = roomId
      }
      this.clearRoom(oldest, { keepInit: true })
    }
  }

  /** 取出并清空该房间的 media 段（按到达顺序），供 player 注册时回放。 */
  drainSegments(roomId: string): ArrayBuffer[] {
    const arr = this.segments.get(roomId)
    if (!arr || arr.length === 0) return []
    this.segments.delete(roomId)
    this.segmentAt.delete(roomId)
    this.segmentBytes = clampNonNegative(
      this.segmentBytes - arr.reduce((sum, item) => sum + item.byteLength, 0),
    )
    return arr
  }

  /** 关掉预览 / 移除房间：该房间的所有缓存条目与旁路状态一起清。 */
  clearRoom(roomId: string, opts: { keepInit?: boolean } = {}): void {
    this.dropInit(roomId)
    const arr = this.segments.get(roomId)
    if (arr) {
      this.segmentBytes = clampNonNegative(
        this.segmentBytes - arr.reduce((sum, item) => sum + item.byteLength, 0),
      )
    }
    this.segments.delete(roomId)
    this.segmentAt.delete(roomId)
    if (opts.keepInit !== true) this.onRoomCleared?.(roomId)
  }

  /** 供 useWebSocket 在"只清 init"的路径上使用。 */
  dropInit(roomId: string): void {
    this.initBuffers.delete(roomId)
    this.initAt.delete(roomId)
  }

  /** TTL 过期回收：init 与 segment 两套时间戳各自判，段被淘汰时同步回退字节。 */
  pruneExpired(now: number): void {
    for (const [roomId, stampedAt] of [...this.initAt.entries()]) {
      if (now - stampedAt > this.ttlMs) {
        this.initBuffers.delete(roomId)
        this.initAt.delete(roomId)
        this.onRoomCleared?.(roomId)
      }
    }
    for (const [roomId, stampedAt] of [...this.segmentAt.entries()]) {
      if (now - stampedAt > this.ttlMs) {
        const arr = this.segments.get(roomId)
        if (arr) {
          this.segmentBytes = clampNonNegative(
            this.segmentBytes - arr.reduce((sum, item) => sum + item.byteLength, 0),
          )
        }
        this.segments.delete(roomId)
        this.segmentAt.delete(roomId)
      }
    }
  }

  stats(): MseCacheStats {
    return {
      initRooms: this.initBuffers.size,
      segmentRooms: this.segments.size,
      segmentBytes: this.segmentBytes,
    }
  }

  private oldestRoom(times: Map<string, number>): string | undefined {
    let oldest: string | undefined
    let oldestAt = Number.POSITIVE_INFINITY
    for (const [roomId, stampedAt] of times.entries()) {
      if (stampedAt < oldestAt) {
        oldest = roomId
        oldestAt = stampedAt
      }
    }
    return oldest
  }
}

function clampNonNegative(value: number): number {
  return value < 0 ? 0 : value
}
