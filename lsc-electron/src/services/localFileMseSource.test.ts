import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { clearLocalFileIndexCache, LocalFileMseSource, estimateByteOffset, findFragmentBoundary } from './localFileMseSource'
import type { MsePlayer } from './mediaSourcePlayer'

function u32(value: number): Uint8Array {
  return new Uint8Array([(value >>> 24) & 0xff, (value >>> 16) & 0xff, (value >>> 8) & 0xff, value & 0xff])
}

function u64(value: number): Uint8Array {
  return new Uint8Array([...u32(Math.floor(value / 4294967296)), ...u32(value >>> 0)])
}

function box(type: string, payload: Uint8Array): Uint8Array {
  const out = new Uint8Array(8 + payload.length)
  out.set(u32(out.length), 0)
  out.set([...type].map((c) => c.charCodeAt(0)), 4)
  out.set(payload, 8)
  return out
}

function concat(...parts: Uint8Array[]): Uint8Array {
  const total = parts.reduce((n, p) => n + p.length, 0)
  const out = new Uint8Array(total)
  let cursor = 0
  for (const p of parts) {
    out.set(p, cursor)
    cursor += p.length
  }
  return out
}

const TIMESCALE = 90000
/** 模拟真实直播录制：文件内起始 PTS 很大（1000s），故轴偏移为 -1000 */
const BASE_SEC = 1000
const MDAT_BYTES = 1000

function buildFile(fragmentCount: number, mdatBytes: number = MDAT_BYTES): Uint8Array {
  const ftyp = box('ftyp', new Uint8Array([0x69, 0x73, 0x6f, 0x6d, 0, 0, 2, 0]))
  const tkhd = box('tkhd', concat(new Uint8Array([0, 0, 0, 0]), u32(0), u32(0), u32(1), u32(0), u32(0), u32(0), u32(0), u32(0)))
  const mdhd = box('mdhd', concat(new Uint8Array([0, 0, 0, 0]), u32(0), u32(0), u32(TIMESCALE), u32(0), new Uint8Array([0x55, 0xc4, 0, 0])))
  const hdlr = box('hdlr', concat(new Uint8Array([0, 0, 0, 0]), u32(0), new Uint8Array([0x76, 0x69, 0x64, 0x65]), new Uint8Array(12)))
  const moov = box('moov', box('trak', concat(tkhd, box('mdia', concat(mdhd, hdlr)))))
  const parts: Uint8Array[] = [ftyp, moov]
  for (let n = 0; n < fragmentCount; n += 1) {
    const ticks = (BASE_SEC + n) * TIMESCALE
    const tfhd = box('tfhd', concat(new Uint8Array([0, 0, 0, 0]), u32(1)))
    const tfdt = box('tfdt', concat(new Uint8Array([1, 0, 0, 0]), u64(ticks)))
    parts.push(box('moof', box('traf', concat(tfhd, tfdt))))
    parts.push(box('mdat', new Uint8Array(mdatBytes)))
  }
  return concat(...parts)
}

/**
 * 与 {@link buildFile} 同构，但 mdat 填**非零**伪随机字节。
 *
 * 真实录像的 mdat 是压缩码流；零填充会让"落在 mdat 中间"被解析成一个
 * size=0 的盒子吞掉整块缓冲，随后**偶然**与新读入的 fragment 对齐而自愈，
 * 于是测不出真机的"卡在等待更多数据"（2026-09-15 现场即此形态）。
 * 字节恒非零 ⇒ 任意 4 字节窗口的 box size 都远大于缓冲，解析必然停在
 * 「不完整，等待更多数据」，不会自愈。
 */
function buildFilledFile(fragmentCount: number, mdatBytes: number): Uint8Array {
  const ftyp = box('ftyp', new Uint8Array([0x69, 0x73, 0x6f, 0x6d, 0, 0, 2, 0]))
  const tkhd = box('tkhd', concat(new Uint8Array([0, 0, 0, 0]), u32(0), u32(0), u32(1), u32(0), u32(0), u32(0), u32(0), u32(0)))
  const mdhd = box('mdhd', concat(new Uint8Array([0, 0, 0, 0]), u32(0), u32(0), u32(TIMESCALE), u32(0), new Uint8Array([0x55, 0xc4, 0, 0])))
  const hdlr = box('hdlr', concat(new Uint8Array([0, 0, 0, 0]), u32(0), new Uint8Array([0x76, 0x69, 0x64, 0x65]), new Uint8Array(12)))
  const moov = box('moov', box('trak', concat(tkhd, box('mdia', concat(mdhd, hdlr)))))
  const template = new Uint8Array(4096)
  let seed = 0x2545f491
  for (let i = 0; i < template.length; i += 1) {
    seed = (seed * 1664525 + 1013904223) >>> 0
    template[i] = (seed >>> 24) || 0x5a
  }
  const parts: Uint8Array[] = [ftyp, moov]
  for (let n = 0; n < fragmentCount; n += 1) {
    const ticks = (BASE_SEC + n) * TIMESCALE
    const tfhd = box('tfhd', concat(new Uint8Array([0, 0, 0, 0]), u32(1)))
    const tfdt = box('tfdt', concat(new Uint8Array([1, 0, 0, 0]), u64(ticks)))
    parts.push(box('moof', box('traf', concat(tfhd, tfdt))))
    const payload = new Uint8Array(mdatBytes)
    for (let at = 0; at < payload.length; at += template.length) {
      payload.set(template.subarray(0, Math.min(template.length, payload.length - at)), at)
    }
    parts.push(box('mdat', payload))
  }
  return concat(...parts)
}

/**
 * 双码率文件：头部 fragment 小（图文/低码率）、尾部大（高码率）。
 * 复现 2026-09-13 真机事故——定位按头部码率外推会大幅欠冲，
 * 随后必须顺序扫描一大段字节才能抵达目标。
 */
function buildTwoRateFile(headCount: number, tailCount: number, tailMdatBytes: number): Uint8Array {
  const ftyp = box('ftyp', new Uint8Array([0x69, 0x73, 0x6f, 0x6d, 0, 0, 2, 0]))
  const tkhd = box('tkhd', concat(new Uint8Array([0, 0, 0, 0]), u32(0), u32(0), u32(1), u32(0), u32(0), u32(0), u32(0), u32(0)))
  const mdhd = box('mdhd', concat(new Uint8Array([0, 0, 0, 0]), u32(0), u32(0), u32(TIMESCALE), u32(0), new Uint8Array([0x55, 0xc4, 0, 0])))
  const hdlr = box('hdlr', concat(new Uint8Array([0, 0, 0, 0]), u32(0), new Uint8Array([0x76, 0x69, 0x64, 0x65]), new Uint8Array(12)))
  const moov = box('moov', box('trak', concat(tkhd, box('mdia', concat(mdhd, hdlr)))))
  const parts: Uint8Array[] = [ftyp, moov]
  const total = headCount + tailCount
  for (let n = 0; n < total; n += 1) {
    const ticks = (BASE_SEC + n) * TIMESCALE
    const tfhd = box('tfhd', concat(new Uint8Array([0, 0, 0, 0]), u32(1)))
    const tfdt = box('tfdt', concat(new Uint8Array([1, 0, 0, 0]), u64(ticks)))
    parts.push(box('moof', box('traf', concat(tfhd, tfdt))))
    const mdat = n < headCount ? MDAT_BYTES : tailMdatBytes
    parts.push(box('mdat', new Uint8Array(mdat)))
  }
  return concat(...parts)
}


interface FakeFile {
  bytes: Uint8Array
  size: number
}

/** 安装假 IPC：以内存数组充当磁盘文件，支持增长 */
function installFakeBridge(file: FakeFile): void {
  const api = {
    localMedia: {
      info: async () => ({ ok: true, size: file.size, mtimeMs: 1 }),
      read: async ({ offset, length }: { path: string; offset: number; length: number }) => {
        const end = Math.min(file.size, offset + length)
        const slice = file.bytes.subarray(offset, end).slice()
        return { ok: true, data: slice, bytesRead: slice.length, size: file.size, eof: end >= file.size }
      },
      allowRoot: async () => ({ ok: true, roots: [] }),
      roots: async () => ({ roots: [] }),
    },
  }
  ;(window as unknown as { electronAPI: unknown }).electronAPI = api
}

interface FakePlayer {
  feedInit: (data: ArrayBuffer) => void
  feedMedia: (data: ArrayBuffer) => void
  seek: (t: number) => void
  markEndOfStream: () => void
  videoElement: { currentTime: number }
  eofCalls: number[]
}

function createFakePlayer(): { player: MsePlayer; fake: FakePlayer; inits: number[]; medias: number[] } {
  const inits: number[] = [0]
  const medias: number[] = []
  const fake: FakePlayer = {
    feedInit: (data) => {
      inits[0] += 1
      expect(data.byteLength).toBeGreaterThan(0)
    },
    feedMedia: (data) => {
      // 解析该段的 tfdt，记录被喂入的媒体时间（模拟真实播放头推进）
      const view = new Uint8Array(data)
      const tfdtAt = findAscii(view, 'tfdt')
      // findAscii 命中的是 type 字段，box 起点 = tfdtAt - 4；v1 的 baseMediaDecodeTime 在 box 起点 +12
      const ticks = tfdtAt >= 0
        ? u32v(view, tfdtAt + 8) * 4294967296 + u32v(view, tfdtAt + 12)
        : 0
      const t = ticks / TIMESCALE
      medias.push(t)
      // 模拟播放头：仅首个分段落入播放位置，其后由测试显式推进（真实播放需耗墙钟时间）
      if (medias.length === 1) fake.videoElement.currentTime = t
    },
    seek: (t) => {
      fake.videoElement.currentTime = t
    },
    markEndOfStream: () => {
      fake.eofCalls.push(1)
    },
    videoElement: { currentTime: 0 },
    eofCalls: [],
  }
  return { player: fake as unknown as MsePlayer, fake, inits, medias }
}

function u32v(bytes: Uint8Array, at: number): number {
  return ((bytes[at] << 24) >>> 0) + (bytes[at + 1] << 16) + (bytes[at + 2] << 8) + bytes[at + 3]
}

function findAscii(bytes: Uint8Array, text: string): number {
  const needle = [...text].map((c) => c.charCodeAt(0))
  for (let i = 0; i + needle.length <= bytes.length; i += 1) {
    let ok = true
    for (let j = 0; j < needle.length; j += 1) {
      if (bytes[i + j] !== needle[j]) {
        ok = false
        break
      }
    }
    if (ok) return i
  }
  return -1
}

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

describe('estimateByteOffset', () => {
  it('按平均码率外推', () => {
    const index = { entries: [{ tPtsSec: 0, offset: 0 }, { tPtsSec: 10, offset: 1000 }], bytesPerSec: 100 }
    expect(estimateByteOffset(index, 20)).toBe(2000)
  })

  it('目标在首个条目之前时取首条目偏移', () => {
    const index = { entries: [{ tPtsSec: 100, offset: 500 }], bytesPerSec: 100 }
    expect(estimateByteOffset(index, 10)).toBe(500)
  })
})

/** 顺序走 box 链，列出每个 fragment 的 moof 起始偏移 */
function fragmentOffsets(bytes: Uint8Array): number[] {
  const offsets: number[] = []
  let at = 0
  while (at + 8 <= bytes.length) {
    const size = u32v(bytes, at)
    const type = String.fromCharCode(bytes[at + 4], bytes[at + 5], bytes[at + 6], bytes[at + 7])
    if (type === 'moof') offsets.push(at)
    if (size < 8 || at + size > bytes.length) break
    at += size
  }
  return offsets
}

describe('findFragmentBoundary', () => {
  it('返回窗口内最后一个 box 链自洽的 fragment 起点', () => {
    const file = buildFile(6)
    const offsets = fragmentOffsets(file)
    expect(offsets).toHaveLength(6)
    // 最后一条自洽的 4-box 链（moof,mdat,moof,mdat）需要"当前 + 下一个"fragment 都存在
    expect(findFragmentBoundary(file, 0)).toBe(offsets[4])
  })

  it('从窗口中部开始时只认窗口内自洽的位置', () => {
    const file = buildFile(6)
    const offsets = fragmentOffsets(file)
    const base = offsets[2] + 5
    const boundary = findFragmentBoundary(file.subarray(base), base)
    expect(boundary).toBe(offsets[4])
  })
})

describe('LocalFileMseSource', () => {
  beforeEach(() => clearLocalFileIndexCache())
  afterEach(() => {
    ;(window as unknown as { electronAPI?: unknown }).electronAPI = undefined
  })

  it('从头部起播时喂入 init 与所有分段，并发布负轴偏移', async () => {
    const file: FakeFile = { bytes: buildFile(40), size: 0 }
    file.size = file.bytes.length
    installFakeBridge(file)
    const { player, inits, medias } = createFakePlayer()
    let axisOffset: number | null = null
    const source = new LocalFileMseSource({
      path: 'D:/rec/a.mp4',
      player,
      chunkBytes: 65536,
      lookaheadSec: 6,
      pollIntervalMs: 50,
      follow: true,
      onIndex: (info) => {
        axisOffset = info.axisOffsetSec
      },
    })
    source.start()
    await wait(300)
    source.dispose()

    expect(inits[0]).toBe(1)
    expect(axisOffset).toBeCloseTo(-BASE_SEC, 6)
    expect(medias[0]).toBeCloseTo(BASE_SEC, 6)
    // 前瞻限制：不会一次把 40 个分段全部喂入
    expect(medias.length).toBeGreaterThan(1)
    expect(medias.length).toBeLessThanOrEqual(8)
  })

  it('目标轴时间落在头部之外时，有界定位到包含目标的分段起播', async () => {
    const file: FakeFile = { bytes: buildFile(3000), size: 0 }
    file.size = file.bytes.length
    installFakeBridge(file)
    const { player, medias } = createFakePlayer()
    const source = new LocalFileMseSource({
      path: 'D:/rec/b.mp4',
      player,
      startAxisSec: 2000,
      chunkBytes: 65536,
      lookaheadSec: 4,
      pollIntervalMs: 50,
      follow: false,
    })
    source.start()
    await wait(600)
    source.dispose()

    expect(medias.length).toBeGreaterThan(0)
    // 轴偏移 -1000 → 文件时间 = 3000 ± 一个分段
    const first = medias[0]
    expect(first).toBeGreaterThanOrEqual(BASE_SEC + 1999)
    expect(first).toBeLessThanOrEqual(BASE_SEC + 2000)
  })

  it('录制中文件增长时持续追增', async () => {
    const initial = buildFile(6)
    const file: FakeFile = { bytes: initial, size: initial.length }
    installFakeBridge(file)
    const { player, medias } = createFakePlayer()
    const source = new LocalFileMseSource({
      path: 'D:/rec/c.mp4',
      player,
      chunkBytes: 65536,
      lookaheadSec: 2,
      pollIntervalMs: 40,
      follow: true,
    })
    source.start()
    await wait(200)
    const before = medias.length
    // 追加更多分段，模拟录制进程继续写入；同时推进播放头，使前瞻窗口重新打开
    const grown = buildFile(20)
    file.bytes = grown
    file.size = grown.length
    ;(player.videoElement as { currentTime: number }).currentTime = BASE_SEC + 3
    await wait(300)
    source.dispose()
    expect(medias.length).toBeGreaterThan(before)
  })

  it('文件到末尾且 follow=false 时触发 onEnd，并通知播放器 EOF（缓冲不再增长是正常结束）', async () => {
    const bytes = buildFile(4)
    const file: FakeFile = { bytes, size: bytes.length }
    installFakeBridge(file)
    const { player, fake } = createFakePlayer()
    let ended = false
    const source = new LocalFileMseSource({
      path: 'D:/rec/d.mp4',
      player,
      chunkBytes: 65536,
      lookaheadSec: 2,
      pollIntervalMs: 30,
      follow: false,
      onEnd: () => {
        ended = true
      },
    })
    source.start()
    // 等首个分段喂入（它会设定播放位置），再推进播放头 —— 真实播放需耗墙钟时间
    await wait(60)
    ;(player.videoElement as { currentTime: number }).currentTime = BASE_SEC + 30
    await wait(250)
    source.dispose()
    expect(ended).toBe(true)
    // 必须通知播放器：否则 MsePlayer 的 8s 数据饥饿判定会把"读完文件"当成
    // 「直播流连接中断」，或强制 seek 回缓冲起点重播。
    expect(fake.eofCalls.length).toBe(1)
  })

  it('读取不可用时通过 onError 报错而不是抛异常', async () => {
    ;(window as unknown as { electronAPI?: unknown }).electronAPI = undefined
    const { player } = createFakePlayer()
    let error = ''
    const source = new LocalFileMseSource({ path: 'D:/rec/e.mp4', player, onError: (e) => (error = e) })
    source.start()
    await wait(20)
    expect(error).toContain('不可用')
  })
})

describe('LocalFileMseSource 回归：会话缓存与越界目标', () => {
  beforeEach(() => clearLocalFileIndexCache())
  afterEach(() => {
    ;(window as unknown as { electronAPI?: unknown }).electronAPI = undefined
  })

  it('二次进入（命中索引缓存）仍必须喂 init 段并重新定位到新目标', async () => {
    const bytes = buildFile(3000)
    const file: FakeFile = { bytes, size: bytes.length }
    installFakeBridge(file)

    const first = createFakePlayer()
    const s1 = new LocalFileMseSource({
      path: 'D:/rec/warm.mp4',
      player: first.player,
      startAxisSec: 2000,
      chunkBytes: 65536,
      lookaheadSec: 4,
      pollIntervalMs: 50,
      follow: false,
      firstFrameTimeoutMs: 2000,
    })
    s1.start()
    await wait(400)
    s1.dispose()
    expect(first.medias.length).toBeGreaterThan(0)

    // 第二次会话：同路径 → 索引缓存命中，文件头不会再读
    const second = createFakePlayer()
    const s2 = new LocalFileMseSource({
      path: 'D:/rec/warm.mp4',
      player: second.player,
      startAxisSec: 500,
      chunkBytes: 65536,
      lookaheadSec: 4,
      pollIntervalMs: 50,
      follow: false,
      firstFrameTimeoutMs: 2000,
    })
    s2.start()
    await wait(400)
    s2.dispose()

    // 修复前：缓存命中会跳过 init 与定位 → MSE 无 init、画面停在直播
    expect(second.inits[0]).toBe(1)
    expect(second.medias.length).toBeGreaterThan(0)
    const firstFed = second.medias[0]
    expect(firstFed).toBeGreaterThanOrEqual(BASE_SEC + 499)
    expect(firstFed).toBeLessThanOrEqual(BASE_SEC + 500)
  })

  it('目标超出已写入范围时退化起播而不是死等（follow=true 到 EOF 后 clamp）', async () => {
    const bytes = buildFile(60)
    const file: FakeFile = { bytes, size: bytes.length }
    installFakeBridge(file)
    const { player, medias } = createFakePlayer()
    let clamped: boolean | undefined
    const source = new LocalFileMseSource({
      path: 'D:/rec/overshoot.mp4',
      player,
      // 远超文件末尾（文件时间约 1000~1059s）
      startAxisSec: 100000,
      chunkBytes: 65536,
      lookaheadSec: 4,
      pollIntervalMs: 40,
      follow: true,
      firstFrameTimeoutMs: 3000,
      onIndex: (info) => {
        if (info.targetClamped) clamped = true
      },
    })
    source.start()
    await wait(500)
    source.dispose()

    expect(clamped).toBe(true)
    expect(medias.length).toBeGreaterThan(0)
    expect(medias[0]).toBeGreaterThanOrEqual(BASE_SEC)
    expect(medias[0]).toBeLessThanOrEqual(BASE_SEC + 59)
  })

  it('长时间读不到数据时通过 onError 给出可诊断的超时文案', async () => {
    // info 正常但 read 永远返回 0 字节：模拟"轴切了但一帧都喂不进去"
    const api = {
      localMedia: {
        info: async () => ({ ok: true, size: 10 * 1024 * 1024, mtimeMs: 1 }),
        read: async () => ({ ok: true, data: new Uint8Array(0), bytesRead: 0, size: 10 * 1024 * 1024, eof: false }),
        allowRoot: async () => ({ ok: true, roots: [] }),
        roots: async () => ({ roots: [] }),
      },
    }
    ;(window as unknown as { electronAPI: unknown }).electronAPI = api
    const { player } = createFakePlayer()
    let error = ''
    const source = new LocalFileMseSource({
      path: 'D:/rec/stall.mp4',
      player,
      startAxisSec: 30,
      pollIntervalMs: 40,
      follow: true,
      firstFrameTimeoutMs: 300,
      onError: (e) => (error = e),
    })
    source.start()
    await wait(1500)
    source.dispose()
    expect(error).toContain('回看定位超时')
    expect(error).toContain('队列=')
  })

  it('定位期间有持续读盘进展时不得判死（远目标慢扫描必须等到出画）', async () => {
    // 2026-09-13 真机事故：头部低码率（图文）→ 尾部高码率，定位按头部码率外推
    // 大幅欠冲，随后要顺序扫描十几 MB 才到目标；固定 8s 首帧死线把整个回看判死，
    // 播放头钉死只能手动重试。正确语义：有读盘/入队进展就顺延，真停滞才报错。
    const bytes = buildTwoRateFile(1000, 600, 10 * 1024)
    const file: FakeFile = { bytes, size: bytes.length }
    // 每次读盘加 12ms 延迟：扫描速度受限于读节奏，进展"慢但在推进"
    const api = {
      localMedia: {
        info: async () => ({ ok: true, size: file.size, mtimeMs: 1 }),
        read: async ({ offset, length }: { path: string; offset: number; length: number }) => {
          await wait(12)
          const end = Math.min(file.size, offset + length)
          const slice = file.bytes.subarray(offset, end).slice()
          return { ok: true, data: slice, bytesRead: slice.length, size: file.size, eof: end >= file.size }
        },
        allowRoot: async () => ({ ok: true, roots: [] }),
        roots: async () => ({ roots: [] }),
      },
    }
    ;(window as unknown as { electronAPI: unknown }).electronAPI = api
    const { player, medias } = createFakePlayer()
    let error = ''
    const source = new LocalFileMseSource({
      path: 'D:/rec/far.mp4',
      player,
      // 轴偏移 -1000 → 文件时间 2580s：文件覆盖 1000~2600s，目标在尾段深处；
      // 头部码率外推欠冲到 ~1160s(文件轴)，需顺序扫描 ~4.7MB（约 0.9s@12ms/读）才到目标
      startAxisSec: 1580,
      chunkBytes: 16384,
      lookaheadSec: 4,
      pollIntervalMs: 10,
      follow: false,
      firstFrameTimeoutMs: 300,
      onError: (e) => (error = e),
    })
    source.start()
    await wait(4000)
    source.dispose()

    // 改前：300ms 无首帧即 onError('回看定位超时…')，medias 为空
    expect(error).toBe('')
    expect(medias.length).toBeGreaterThan(0)
    expect(medias[0]).toBeGreaterThanOrEqual(BASE_SEC + 1576)
    expect(medias[0]).toBeLessThanOrEqual(BASE_SEC + 1581)
  })

  it('远目标扫描期队列字节有界（等待期丢弃目标之前的过时段）', async () => {
    // 2026-09-13 专项测试残留风险：定位欠冲后的顺序扫描会把沿途已解析段全部
    // 堆进 `_queue`（实测 ~236MB 驻留 JS 堆，内存 78%）——小时级录像 + 更远目标
    // 有渲染进程 OOM 风险。目标之前的段可安全丢弃（文件是持久源，需要时可重读）。
    const bytes = buildTwoRateFile(1000, 600, 10 * 1024)
    const file: FakeFile = { bytes, size: bytes.length }
    const api = {
      localMedia: {
        info: async () => ({ ok: true, size: file.size, mtimeMs: 1 }),
        read: async ({ offset, length }: { path: string; offset: number; length: number }) => {
          const end = Math.min(file.size, offset + length)
          const slice = file.bytes.subarray(offset, end).slice()
          return { ok: true, data: slice, bytesRead: slice.length, size: file.size, eof: end >= file.size }
        },
        allowRoot: async () => ({ ok: true, roots: [] }),
        roots: async () => ({ roots: [] }),
      },
    }
    ;(window as unknown as { electronAPI: unknown }).electronAPI = api
    const { player, medias } = createFakePlayer()
    const source = new LocalFileMseSource({
      path: 'D:/rec/trim.mp4',
      player,
      // 远目标：轴 1580（文件时间 2580s），头部外推欠冲 → 顺序扫描 ~4.7MB
      startAxisSec: 1580,
      chunkBytes: 16384,
      lookaheadSec: 4,
      pollIntervalMs: 10,
      follow: false,
      firstFrameTimeoutMs: 3000,
    })
    source.start()
    await wait(2500)
    source.dispose()

    expect(medias.length).toBeGreaterThan(0)
    // 出画时刻队列里只应保留「目标前一小段 + 前瞻窗口」，不得堆整个扫描区间
    const queuedGetter = (source as unknown as { queuedApproxBytes?: number }).queuedApproxBytes
    expect(typeof queuedGetter).toBe('number')
    expect(queuedGetter as number).toBeLessThan(2 * 1024 * 1024)
  })

  it('定位前索引必须暖到至少 2 个条目（头部读不满时继续加读，禁止盲外推到字节 0）', async () => {
    // 2026-09-13 真机：11:36 与 12:36 两次回看 `定位: 目标 xxx -> 字节 0` ——
    // 头部一块读不满 2 个 fragment 时码率不可估，外推退化为文件头，
    // 之后全靠顺序扫描。正确行为：定位前继续顺序加读直到索引 ≥2 条目。
    const bytes = buildTwoRateFile(400, 300, 512 * 1024)
    const file: FakeFile = { bytes, size: bytes.length }
    const api = {
      localMedia: {
        info: async () => ({ ok: true, size: file.size, mtimeMs: 1 }),
        read: async ({ offset, length }: { path: string; offset: number; length: number }) => {
          const end = Math.min(file.size, offset + length)
          const slice = file.bytes.subarray(offset, end).slice()
          return { ok: true, data: slice, bytesRead: slice.length, size: file.size, eof: end >= file.size }
        },
        allowRoot: async () => ({ ok: true, roots: [] }),
        roots: async () => ({ roots: [] }),
      },
    }
    ;(window as unknown as { electronAPI: unknown }).electronAPI = api
    const { player, medias } = createFakePlayer()
    let error = ''
    const source = new LocalFileMseSource({
      path: 'D:/rec/warm.mp4',
      player,
      startAxisSec: 300,
      chunkBytes: 256 * 1024, // 头部一块不足 2 个大 fragment
      lookaheadSec: 4,
      pollIntervalMs: 10,
      follow: false,
      firstFrameTimeoutMs: 3000,
      onError: (e) => (error = e),
    })
    source.start()
    await wait(2000)
    source.dispose()

    expect(error).toBe('')
    expect(medias.length).toBeGreaterThan(0)
    const entryGetter = (source as unknown as { indexEntryCount?: number }).indexEntryCount
    expect(typeof entryGetter).toBe('number')
    expect(entryGetter as number).toBeGreaterThanOrEqual(2)
    // 定位必须落在目标附近而不是字节 0（文件时间 1300s = 轴 300s）
    expect(medias[0]).toBeGreaterThanOrEqual(BASE_SEC + 298)
  })

  it('定位窗口被 IPC 读上限截断时仍必须落到目标 fragment（2026-09-15 真机事故）', async () => {
    // 现场：SEEK_BACK_WINDOW_BYTES(16MB) 的边界搜索窗口被主进程
    // MAX_READ_LENGTH(8MB) **静默截断**（localMedia.ts 的 parseReadRequest 不报错），
    // 而自洽 box 链需要约一个 fragment 的前看量（实测 moof+mdat ≈ 6.4MB）——
    // findFragmentBoundary 只看得到窗口头部，返回 null 后回退到 windowStart：
    // 那是 mdat 中间的任意字节，切分器无法重同步，一路扫描到文件尾 0 段入队，
    // 2m39s 后被停滞看门狗判死（`目标=396.8s，文件时间范围=0.0~8.4s，队列=0`）。
    // 正确行为：窗口按实际返回长度分多次读完，边界搜索必须看到整窗。
    const CAP = 256 * 1024
    const bytes = buildFilledFile(48, 512 * 1024)
    const file: FakeFile = { bytes, size: bytes.length }
    const api = {
      localMedia: {
        info: async () => ({ ok: true, size: file.size, mtimeMs: 1 }),
        // 复刻主进程语义：length 超上限时截断到上限，其余照常返回
        read: async ({ offset, length }: { path: string; offset: number; length: number }) => {
          const end = Math.min(file.size, offset + Math.min(length, CAP))
          const slice = file.bytes.subarray(offset, end).slice()
          return { ok: true, data: slice, bytesRead: slice.length, size: file.size, eof: end >= file.size }
        },
        allowRoot: async () => ({ ok: true, roots: [] }),
        roots: async () => ({ roots: [] }),
      },
    }
    ;(window as unknown as { electronAPI: unknown }).electronAPI = api
    const { player, medias } = createFakePlayer()
    let error = ''
    const source = new LocalFileMseSource({
      path: 'D:/rec/capped.mp4',
      player,
      // 轴 40s ⇒ 文件时间 1040s（文件覆盖 1000~1047s）。
      // 头部外推 ≈20.5MB > 16MB ⇒ windowStart≈3.7MB 落在 mdat 中间（非边界）。
      startAxisSec: 40,
      lookaheadSec: 4,
      pollIntervalMs: 10,
      follow: false,
      firstFrameTimeoutMs: 3000,
      onError: (e) => (error = e),
    })
    source.start()
    await wait(2000)
    source.dispose()

    // 改前：边界搜索在 256KB 截断缓冲里找不到自洽链 ⇒ 跳 windowStart ⇒ medias 为空
    expect(error).toBe('')
    expect(medias.length).toBeGreaterThan(0)
    // 起播必须落在目标所在 fragment（±2 个 fragment），而不是文件头或窗口起点
    expect(medias[0]).toBeGreaterThanOrEqual(BASE_SEC + 38)
    expect(medias[0]).toBeLessThanOrEqual(BASE_SEC + 41)
  })
})
