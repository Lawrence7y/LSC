/**
 * fMP4（fragmented MP4）顶层 box 解析与切分。
 *
 * 用途：把**本地录制文件**的字节流切成
 *   - init 段：`ftyp` + `moov`（喂 MsePlayer.feedInit）
 *   - media 段：`moof` + `mdat`（喂 MsePlayer.feedMedia）
 * 从而在没有任何 FFmpeg 转码进程的前提下，把本地文件当直播流一样送进 MSE。
 *
 * 本模块是纯函数/纯类，无 IO、无浏览器依赖，可直接单测。
 */

const EMPTY = new Uint8Array(0)

/** 顶层 box 中属于「跳过」的元数据盒（不参与 MSE 追加）。 */
const SKIP_BOXES = new Set(['sidx', 'free', 'skip', 'mfra', 'prft', 'emsg', 'wide', 'uuid'])

const CONTAINER_BOXES = new Set(['moov', 'trak', 'mdia', 'minf', 'stbl', 'edts', 'mvex', 'moof', 'traf', 'mfra', 'udta'])

export interface Fmp4BoxHeader {
  type: string
  /** box 起始偏移（相对传入缓冲） */
  start: number
  /** box 结束偏移（不含） */
  end: number
  /** 头长度（8 或 16） */
  headerSize: number
}

export function readAscii(bytes: Uint8Array, at: number, len: number): string {
  let out = ''
  for (let i = 0; i < len; i += 1) {
    const c = bytes[at + i]
    if (c === undefined || c === 0) break
    out += String.fromCharCode(c)
  }
  return out
}

export function readUint32(bytes: Uint8Array, at: number): number {
  return (
    ((bytes[at] << 24) >>> 0) +
    (bytes[at + 1] << 16) +
    (bytes[at + 2] << 8) +
    bytes[at + 3]
  ) >>> 0
}

export function readUint64(bytes: Uint8Array, at: number): number {
  const hi = readUint32(bytes, at)
  const lo = readUint32(bytes, at + 4)
  return hi * 4294967296 + lo
}

/** 读取一个完整 box 头；数据不足时返回 null。size=0（延伸至 EOF）返回 size=0 的头部，由调用方决定。 */
export function readBoxHeader(bytes: Uint8Array, at: number): Fmp4BoxHeader | null {
  if (at + 8 > bytes.length) return null
  let size = readUint32(bytes, at)
  const type = readAscii(bytes, at + 4, 4)
  let headerSize = 8
  if (size === 1) {
    if (at + 16 > bytes.length) return null
    size = readUint64(bytes, at + 8)
    headerSize = 16
  }
  if (size !== 0 && size < headerSize) return null
  return { type, start: at, end: size === 0 ? bytes.length : at + size, headerSize }
}

/** 列出 [from, to) 范围内的顶层 box（遇到不完整 box 即停止）。 */
export function listBoxes(bytes: Uint8Array, from = 0, to = bytes.length): Fmp4BoxHeader[] {
  const out: Fmp4BoxHeader[] = []
  let at = from
  while (at + 8 <= to) {
    const header = readBoxHeader(bytes, at)
    if (!header || header.end > to) break
    out.push(header)
    if (header.end <= at) break
    at = header.end
  }
  return out
}

/** 深度优先查找第一个指定类型的 box（会进入已知容器盒）。 */
export function findBox(bytes: Uint8Array, type: string): Uint8Array | null {
  const boxes = listBoxes(bytes)
  for (const box of boxes) {
    if (box.type === type) return bytes.subarray(box.start, box.end)
    if (CONTAINER_BOXES.has(box.type)) {
      const inner = findBox(bytes.subarray(box.start + box.headerSize, box.end), type)
      if (inner) return inner
    }
  }
  return null
}

export interface Fmp4TrackInfo {
  trackId: number
  timescale: number
}

/** 从 moov 中解析出视频轨的 track_ID 与媒体 timescale（用于 tfdt 时间换算）。 */
export function parseVideoTrack(moov: Uint8Array): Fmp4TrackInfo | null {
  const traks: Uint8Array[] = []
  // 传入的既可能是完整的 moov box，也可能是其内部载荷
  const outer = listBoxes(moov)
  const moovBox = outer.find((b) => b.type === 'moov')
  const scope = moovBox ? moov.subarray(moovBox.start + moovBox.headerSize, moovBox.end) : moov
  for (const box of listBoxes(scope)) {
    if (box.type === 'trak') traks.push(scope.subarray(box.start, box.end))
  }
  let fallback: Fmp4TrackInfo | null = null
  for (const trak of traks) {
    const hdlr = findBox(trak, 'hdlr')
    const handlerType = hdlr ? readAscii(hdlr, hdlr.length >= 20 ? 16 : 8, 4) : ''
    const mdhd = findBox(trak, 'mdhd')
    const tkhd = findBox(trak, 'tkhd')
    if (!mdhd || !tkhd) continue
    const version = mdhd[8]
    const timescale = version === 1 ? readUint32(mdhd, 28) : readUint32(mdhd, 20)
    const tkhdVersion = tkhd[8]
    const trackId = tkhdVersion === 1 ? readUint32(tkhd, 28) : readUint32(tkhd, 20)
    if (!timescale || !trackId) continue
    const info = { trackId, timescale }
    if (handlerType === 'vide') return info
    if (!fallback) fallback = info
  }
  return fallback
}

/** 从 moof 中读取首个（或指定轨的）tfdt 基准解码时间，换算为秒。 */
export function readTfdtSeconds(moof: Uint8Array, track: Fmp4TrackInfo | null): number | null {
  const outer = listBoxes(moof)
  const moofBox = outer.find((b) => b.type === 'moof') ?? outer[0]
  if (!moofBox) return null
  const scope = moof.subarray(moofBox.start + moofBox.headerSize, moofBox.end)
  let firstValue: { value: number; trackId: number } | null = null
  for (const box of listBoxes(scope)) {
    if (box.type !== 'traf') continue
    const traf = scope.subarray(box.start, box.end)
    const tfhd = findBox(traf, 'tfhd')
    const tfTrackId = tfhd ? readUint32(tfhd, 12) : 0
    const tfdt = findBox(traf, 'tfdt')
    if (!tfdt) continue
    const version = tfdt[8]
    const value = version === 1 ? readUint64(tfdt, 12) : readUint32(tfdt, 12)
    if (track && tfTrackId === track.trackId) return value / track.timescale
    if (!firstValue) firstValue = { value, trackId: tfTrackId }
  }
  if (!firstValue) return null
  // 未匹配到视频轨时退化为首个 traf（通常视频 traf 在前）
  return track ? firstValue.value / track.timescale : firstValue.value
}

export interface Fmp4MediaSegment {
  bytes: Uint8Array
  /** 该 media 段在文件中的起始字节偏移（用于建立 seek 索引） */
  offset: number
  /** 该段起点时间（原始文件时间轴秒）；无法解析 tfdt 时为 null */
  tPtsSec: number | null
}

export interface Fmp4PushResult {
  /** 本次新完成的 init 段（ftyp+moov），无则为 null */
  init: Uint8Array | null
  /** 本次新完成的 media 段 */
  segments: Fmp4MediaSegment[]
  /** 视频轨信息（init 就绪后可读） */
  track: Fmp4TrackInfo | null
}

/**
 * 增量式 fMP4 切分器：`push()` 任意长度字节块，输出可喂 MSE 的 init/media 段。
 */
export class Fmp4BoxSplitter {
  private _buf: Uint8Array = EMPTY
  /** 已解析消费的字节总数（相对流起点），用于计算 media 段偏移 */
  private _consumed = 0
  private _held: Uint8Array[] = []
  private _heldStart = -1
  private _ftypParts: Uint8Array[] = []
  private _init: Uint8Array | null = null
  private _track: Fmp4TrackInfo | null = null

  get init(): Uint8Array | null {
    return this._init
  }

  get track(): Fmp4TrackInfo | null {
    return this._track
  }

  /** 尚有未消费字节（读取位置应停在此处继续等待增长）。 */
  get pendingBytes(): number {
    return this._buf.length
  }

  push(chunk: Uint8Array): Fmp4PushResult {
    if (chunk.length > 0) {
      const merged = new Uint8Array(this._buf.length + chunk.length)
      merged.set(this._buf, 0)
      merged.set(chunk, this._buf.length)
      this._buf = merged
    }
    let init: Uint8Array | null = null
    const segments: Fmp4MediaSegment[] = []

    let at = 0
    while (at + 8 <= this._buf.length) {
      const header = readBoxHeader(this._buf, at)
      if (!header) break
      if (header.end > this._buf.length) break // 不完整，等待更多数据
      const boxBytes = this._buf.subarray(header.start, header.end)
      const boxStartAbs = this._consumed + header.start
      at = header.end

      if (header.type === 'ftyp') {
        this._ftypParts = [boxBytes.slice()]
        continue
      }
      if (header.type === 'moov') {
        const parts = [...this._ftypParts, boxBytes.slice()]
        const total = parts.reduce((n, p) => n + p.length, 0)
        const out = new Uint8Array(total)
        let cursor = 0
        for (const p of parts) {
          out.set(p, cursor)
          cursor += p.length
        }
        this._init = out
        init = out
        this._track = parseVideoTrack(out)
        continue
      }
      if (header.type === 'styp' || header.type === 'moof') {
        if (this._held.length === 0) this._heldStart = boxStartAbs
        this._held.push(boxBytes.slice())
        continue
      }
      if (header.type === 'mdat') {
        if (this._held.length === 0) this._heldStart = boxStartAbs
        this._held.push(boxBytes.slice())
        const moof = this._held.find((b) => readAscii(b, 4, 4) === 'moof')
        const tPtsSec = moof && this._track ? readTfdtSeconds(moof, this._track) : null
        segments.push({
          bytes: concatBytes(this._held),
          offset: this._heldStart,
          tPtsSec,
        })
        this._held = []
        this._heldStart = -1
        continue
      }
      if (SKIP_BOXES.has(header.type)) {
        continue
      }
      // 未知顶层盒：若正处于 media 段拼装中则一并保留，否则丢弃
      if (this._held.length > 0) this._held.push(boxBytes.slice())
    }

    if (at > 0) {
      this._consumed += at
      this._buf = this._buf.subarray(at).slice()
    }
    return { init, segments, track: this._track }
  }

  /** 文件已到末尾（不再增长）时调用：把 size=0 的尾部 box 视为完整。 */
  flushEnd(): Fmp4PushResult {
    return this.push(EMPTY)
  }

  /**
   * 读位置跳变（回看 seek 到别处）时调用：丢弃未消费字节与半截分段，
   * 保留已就绪的 init 段与轨信息。
   *
   * @param baseOffset 新的读取起点在文件中的绝对偏移 —— 之后产出的分段偏移
   *   以它为基准，索引里始终记录绝对字节位置。
   */
  resetPosition(baseOffset = 0): void {
    this._buf = EMPTY
    this._held = []
    this._heldStart = -1
    this._consumed = baseOffset
  }
}

function concatBytes(parts: Uint8Array[]): Uint8Array {
  const total = parts.reduce((n, p) => n + p.length, 0)
  const out = new Uint8Array(total)
  let cursor = 0
  for (const p of parts) {
    out.set(p, cursor)
    cursor += p.length
  }
  return out
}

/**
 * 在已建索引中挑选回看起点：包含 startSec 的那个 fragment（最大的 t <= startSec）。
 * 索引为空时返回 0。fragment 边界即关键帧，落点误差 < 一个 fragment。
 */
export function pickStartFragmentIndex(index: Array<{ tPtsSec: number | null }>, startSec: number): number {
  let best = 0
  for (let i = 0; i < index.length; i += 1) {
    const t = index[i].tPtsSec
    if (t == null) continue
    if (t <= startSec + 0.0001) best = i
    else break
  }
  return best
}
