import { MsePlayer } from './mediaSourcePlayer'
import {
  Fmp4BoxSplitter,
  readAscii,
  readBoxHeader,
  type Fmp4MediaSegment,
} from './fmp4Box'
import { isLocalMediaAvailable, localMediaInfo, localMediaRead } from './localMediaReader'

/**
 * 本地录制文件 → MSE 数据源（方案 A 核心）。
 *
 * 顺序读取本地 fMP4 文件字节 → 切成 init/media 段 → 喂给 MsePlayer：
 * - 无 FFmpeg 进程、无 WebSocket 往返、无 review 会话/epoch/名额；
 * - 录制中文件持续增长时按 {@link LocalFileMseSourceOptions.pollIntervalMs} 追增；
 * - 用 `moof/tfdt` 建立「时间 → 字节偏移」索引，按平均码率外推做**有界定位**。
 *
 * 三条硬性保证（都是线上故障换来的）：
 * 1. **任何一次会话都必须先拿到并喂入 init 段**（否则 MSE 永远不出画，表现为"轴切了但画面还是直播"）；
 *    命中缓存索引时用缓存的 init 字节恢复解析器，而不是跳过。
 * 2. **定位失败不得死等**：目标超出已写入范围时，到 EOF 后退化为从最近可读位置起播并上报
 *    `targetClamped`，而不是永远不喂数据。
 * 3. **首帧停滞必须有诊断出口**：超时看门狗只认"停滞"（期间无任何读盘/入队进展），
 *    远目标低速扫描属于进展、必须顺延等待（2026-09-13 真机：固定 8s 死线把
 *    码率欠冲后的合法定位判死，播放头钉死只能手动重试）。
 */

export interface LocalFileMseSourceOptions {
  path: string
  player: MsePlayer
  /** 目标起播位置（**录制轴秒**）；缺省 = 文件头 */
  startAxisSec?: number
  /**
   * 已知轴偏移（`recordingAxis = fileTime + axisOffsetSec`，通常为负）。
   * 与实测首帧不一致（>1s）时以实测为准。
   */
  axisOffsetHintSec?: number
  chunkBytes?: number
  /** 前向缓冲目标（秒）：只喂到 currentTime + lookahead，避免 MSE pending 溢出丢帧 */
  lookaheadSec?: number
  pollIntervalMs?: number
  /** 文件是否仍在写入（录制中） */
  follow?: boolean
  /**
   * 首帧停滞超时（ms）：期间无任何读盘/入队**进展**且仍未喂入 media 段则报错退出。
   * 有进展（读盘推进/索引增长/入队变化）则顺延——远目标的有界定位欠冲后需要
   * 顺序扫描追赶，属正常进展，不得按墙钟判死。
   */
  firstFrameTimeoutMs?: number
  onFirstMedia?: () => void
  onIndex?: (info: { firstPtsSec: number; axisOffsetSec: number; targetClamped?: boolean }) => void
  onEnd?: () => void
  onError?: (error: string) => void
}

const DEFAULT_CHUNK_BYTES = 4 * 1024 * 1024
const DEFAULT_LOOKAHEAD_SEC = 12
const DEFAULT_POLL_MS = 500
const DEFAULT_FIRST_FRAME_TIMEOUT_MS = 8000
/** 定位时向前搜索的最大窗口（字节）：8Mbps≈1MB/s，16MB≈16s，覆盖多个 fragment */
const SEEK_BACK_WINDOW_BYTES = 16 * 1024 * 1024
/** follow 模式下文件短暂消失（归档改名）的容忍次数 */
const MAX_MISSING_RETRIES = 12
/**
 * 出画前队列修剪：目标之前超过该秒数的已排队段直接丢弃。
 * 远目标欠冲后的顺序扫描若不修剪，会把沿途所有段堆进队列
 * （2026-09-13 实测 ~236MB 驻留 JS 堆）。段可从文件重读，丢弃安全。
 */
const QUEUE_KEEP_BEFORE_SEC = 16

interface IndexEntry {
  tPtsSec: number
  offset: number
}

interface FileIndex {
  entries: IndexEntry[]
  /** 已索引过的 fragment 字节偏移（去重：重复会话重读同一区间不再堆条目） */
  offsets: Set<number>
  /** 已顺序扫描到的文件字节位置 */
  scannedTo: number
  /** 观测到的平均码率（字节/秒），用于外推定位 */
  bytesPerSec: number
  /**
   * init 段（ftyp+moov）字节缓存：让后续会话无需重读文件头即可恢复
   * MsePlayer 的 SourceBuffer 与解析器的轨信息。
   */
  init?: Uint8Array
}

const _indexCache = new Map<string, FileIndex>()

/** 供测试与诊断：清空模块级索引缓存 */
export function clearLocalFileIndexCache(): void {
  _indexCache.clear()
}

function getIndex(path: string): FileIndex {
  const existing = _indexCache.get(path)
  if (existing) return existing
  const fresh: FileIndex = { entries: [], offsets: new Set<number>(), scannedTo: 0, bytesPerSec: 0 }
  _indexCache.set(path, fresh)
  return fresh
}

function noteRate(index: FileIndex): void {
  if (index.entries.length < 2) return
  const first = index.entries[0]
  const last = index.entries[index.entries.length - 1]
  const dt = last.tPtsSec - first.tPtsSec
  const db = last.offset - first.offset
  // 0.2s：高码率 + 1s 分片时头部一块常只有 1-2 个 fragment，阈值过严会让
  // bytesPerSec 恒为 0，外推退化为文件头（2026-09-13 真机 `定位 -> 字节 0`）
  if (dt > 0.2 && db > 0) index.bytesPerSec = db / dt
}

/** 按索引 + 平均码率把目标时间外推为字节位置（纯函数） */
export function estimateByteOffset(index: { entries: IndexEntry[]; bytesPerSec: number }, targetSec: number): number {
  if (index.entries.length === 0) return 0
  let best = index.entries[0]
  for (const entry of index.entries) {
    if (entry.tPtsSec <= targetSec + 1e-3) best = entry
  }
  if (best.tPtsSec > targetSec) return best.offset
  if (index.bytesPerSec <= 0) return best.offset
  return Math.floor(best.offset + (targetSec - best.tPtsSec) * index.bytesPerSec)
}

/** 索引里的时间空洞容忍度（秒）：超过则说明目标不在已扫描区间内，需要外推定位 */
export const INDEX_GAP_TOLERANCE_SEC = 30

/**
 * 找出"包含目标时间"的已索引 fragment —— 仅在索引确实**连续覆盖**目标时才返回。
 *
 * 索引会在多次 seek 后出现空洞（如先扫了 1000~1060，又扫了 3000~3040）：
 * 此时 1500 的后继条目是 3000，说明 1500 落在未扫描区，必须交给外推定位，
 * 否则会误跳到 1060 并从那里顺序重读（线上表现为"轴切了但画面长时间还是直播"）。
 */
export function findCoveringFragment(
  entries: IndexEntry[],
  targetSec: number,
  gapToleranceSec = INDEX_GAP_TOLERANCE_SEC,
): IndexEntry | null {
  let pick = -1
  for (let i = 0; i < entries.length; i += 1) {
    if (entries[i].tPtsSec <= targetSec + 1e-3) pick = i
    else break
  }
  if (pick < 0) return null
  const next = entries[pick + 1]
  if (!next) return null
  if (next.tPtsSec - targetSec > gapToleranceSec) return null
  return entries[pick]
}

/** 在缓冲窗口内寻找一个合法的 fragment 起点（moof/styp 起始且 box 链自洽） */
export function findFragmentBoundary(buffer: Uint8Array, baseOffset: number): number | null {
  const candidates: number[] = []
  // type 字段位于 box 起点 +4，故候选起点为 i-4
  for (let i = 4; i + 4 <= buffer.length; i += 1) {
    const type = readAscii(buffer, i, 4)
    if (type === 'moof' || type === 'styp') candidates.push(i - 4)
  }
  for (let i = candidates.length - 1; i >= 0; i -= 1) {
    const pos = candidates[i]
    if (isValidFragmentChain(buffer, pos)) return baseOffset + pos
  }
  return null
}

/** 校验 pos 起是一条自洽的 box 链（尺寸合法 + 出现 moof） */
function isValidFragmentChain(buffer: Uint8Array, pos: number): boolean {
  let at = pos
  let sawMoof = false
  for (let i = 0; i < 4; i += 1) {
    const header = readBoxHeader(buffer, at)
    if (!header) return false
    const size = header.end - header.start
    if (size < 8 || size > 64 * 1024 * 1024) return false
    const isSegmentBox =
      header.type === 'moof' || header.type === 'mdat' || header.type === 'styp'
    if (!isSegmentBox) return false
    if (header.type === 'moof') sawMoof = true
    at = header.end
  }
  return sawMoof
}

export class LocalFileMseSource {
  private readonly _path: string
  private readonly _player: MsePlayer
  private readonly _targetAxisSec: number | null
  private readonly _axisOffsetHint: number | null
  private readonly _chunkBytes: number
  private readonly _lookaheadSec: number
  private readonly _pollMs: number
  private readonly _follow: boolean
  private readonly _firstFrameTimeoutMs: number
  private readonly _onFirstMedia?: () => void
  private readonly _onIndex?: (info: { firstPtsSec: number; axisOffsetSec: number; targetClamped?: boolean }) => void
  private readonly _onEnd?: () => void
  private readonly _onError?: (error: string) => void

  private readonly _splitter = new Fmp4BoxSplitter()
  private readonly _index: FileIndex
  private _readOffset = 0
  private _queue: Fmp4MediaSegment[] = []
  private _alive = false
  private _busy = false
  private _timer: ReturnType<typeof setTimeout> | null = null
  private _firstFrameTimer: ReturnType<typeof setTimeout> | null = null
  private _missingCount = 0
  private _sawMedia = false
  private _located = false
  private _ended = false
  private _seekedOnce = false
  private _axisOffset: number | null = null
  private _failed = false
  /** 本趟是否已读到写入游标（EOF）：决定"目标越界"时是继续等还是退化起播 */
  private _reachedWriteCursor = false
  private _targetClamped = false
  /** 首帧停滞看门狗：每次读盘/入队进展 +1；看门狗只在票数不动的窗口期判死 */
  private _progressTicket = 0
  private _lastSeenProgress = 0

  constructor(options: LocalFileMseSourceOptions) {
    this._path = options.path
    this._player = options.player
    this._targetAxisSec = options.startAxisSec == null ? null : Math.max(0, options.startAxisSec)
    this._axisOffsetHint = options.axisOffsetHintSec ?? null
    this._chunkBytes = Math.max(64 * 1024, options.chunkBytes ?? DEFAULT_CHUNK_BYTES)
    this._lookaheadSec = Math.max(2, options.lookaheadSec ?? DEFAULT_LOOKAHEAD_SEC)
    this._pollMs = Math.max(100, options.pollIntervalMs ?? DEFAULT_POLL_MS)
    this._follow = options.follow !== false
    this._firstFrameTimeoutMs = Math.max(200, options.firstFrameTimeoutMs ?? DEFAULT_FIRST_FRAME_TIMEOUT_MS)
    this._onFirstMedia = options.onFirstMedia
    this._onIndex = options.onIndex
    this._onEnd = options.onEnd
    this._onError = options.onError

    this._index = getIndex(this._path)
    if (Number.isNaN(this._targetAxisSec ?? 0)) {
      // NaN 目标会让所有比较恒假：直接拒绝，避免静默不出画
      this._targetAxisSec = null
    }
  }

  get axisOffsetSec(): number | null {
    return this._axisOffset
  }

  get bytesRead(): number {
    return this._readOffset
  }

  get targetClamped(): boolean {
    return this._targetClamped
  }

  /** 诊断/测试用：当前排队段的总字节数（出画前应被修剪保持有界） */
  get queuedApproxBytes(): number {
    let total = 0
    for (const segment of this._queue) total += segment.bytes.byteLength
    return total
  }

  /** 诊断/测试用：已建立的「时间 → 字节偏移」索引条目数 */
  get indexEntryCount(): number {
    return this._index.entries.length
  }

  start(): void {
    if (this._alive) return
    if (!isLocalMediaAvailable()) {
      this._fail('本机文件读取不可用（当前非 Electron 环境）')
      return
    }
    this._alive = true
    this._armFirstFrameWatchdog()
    void this._pump()
  }

  /**
   * 首帧停滞看门狗：周期检查进展票数。
   * - 出画（`_sawMedia`）→ 撤销；
   * - 票数在变（读盘推进/索引增长/入队变化）→ 顺延一个周期；
   * - 票数不动 → 真停滞，按诊断口径 `_fail`。
   */
  private _armFirstFrameWatchdog(): void {
    this._firstFrameTimer = setTimeout(() => {
      this._firstFrameTimer = null
      if (!this._alive || this._sawMedia) return
      if (this._progressTicket !== this._lastSeenProgress) {
        this._lastSeenProgress = this._progressTicket
        this._armFirstFrameWatchdog()
        return
      }
      this._fail(this._describeStall())
    }, this._firstFrameTimeoutMs)
  }

  dispose(): void {
    this._alive = false
    if (this._timer !== null) {
      clearTimeout(this._timer)
      this._timer = null
    }
    if (this._firstFrameTimer !== null) {
      clearTimeout(this._firstFrameTimer)
      this._firstFrameTimer = null
    }
  }

  private _fail(error: string): void {
    if (this._failed) return
    this._failed = true
    this.dispose()
    // 回看失败必须在日志里留痕：此前只有越界一种情况会打日志，
    // 首帧超时/读盘失败时现场只剩 UI 上一个无信息量的错误条。
    console.warn(`[LocalFileMseSource] 回看失败: path=${this._path} ${error}`)
    this._onError?.(error)
  }

  /** 首帧超时时的诊断文案：把实测范围一并抛出，便于现场定位 */
  private _describeStall(): string {
    const entries = this._index.entries
    const first = entries[0]?.tPtsSec
    const last = entries[entries.length - 1]?.tPtsSec
    const axis = this._axisOffset
    const target = this._targetAxisSec
    const parts = [
      `回看定位超时（${(this._firstFrameTimeoutMs / 1000).toFixed(0)}s 内未出画）`,
      target == null ? '目标=文件头' : `目标=${target.toFixed(1)}s(录制轴)`,
      axis == null ? '轴偏移=未知' : `轴偏移=${axis.toFixed(1)}s`,
      first == null ? '文件时间范围=未知' : `文件时间范围=${first.toFixed(1)}~${(last ?? first).toFixed(1)}s`,
      `队列=${this._queue.length}`,
    ]
    return parts.join('，')
  }

  private _schedule(): void {
    if (!this._alive || this._timer !== null) return
    this._timer = setTimeout(() => {
      this._timer = null
      void this._pump()
    }, this._pollMs)
  }

  /** 出画前的任何读盘/解析进展都顺延首帧停滞看门狗 */
  private _noteProgress(): void {
    if (!this._sawMedia) this._progressTicket += 1
  }

  /**
   * 出画前修剪队列：丢弃目标之前超过 {@link QUEUE_KEEP_BEFORE_SEC} 的过时段
   * （始终保留最后 2 段，供 `_canStartFeeding` 的「目标前一段」起播语义）。
   * 目标未知时不动队列。
   */
  private _trimQueueBeforeTarget(): void {
    if (this._sawMedia || this._queue.length <= 2) return
    const target = this._targetFileSec()
    if (target == null) return
    const keepFrom = target - QUEUE_KEEP_BEFORE_SEC
    let cut = -1
    for (let i = 0; i < this._queue.length - 2; i += 1) {
      const t = this._queue[i].tPtsSec
      if (t != null && t >= keepFrom) {
        cut = i
        break
      }
    }
    if (cut > 0) this._queue = this._queue.slice(cut)
  }

  /**
   * 定位前索引暖机：不足 2 个条目时继续顺序加读（至多 8 块），
   * 让 `noteRate` 能估出码率——否则外推退化为文件头，远目标全靠顺序扫描。
   */
  private async _ensureEnoughIndex(size: number): Promise<void> {
    let attempts = 0
    while (this._alive && this._index.entries.length < 2 && attempts < 8 && this._readOffset < size) {
      const len = Math.min(this._chunkBytes * 4, size - this._readOffset)
      const chunk = await localMediaRead(this._path, this._readOffset, len)
      if (!this._alive || !chunk.ok || chunk.bytesRead <= 0) return
      this._readOffset += chunk.bytesRead
      this._index.scannedTo = this._readOffset
      this._consume(chunk.data ?? new Uint8Array(0))
      this._noteProgress()
      attempts += 1
    }
  }

  /** 主循环：确保 init → 定位 → 顺序读取 → 切分入队 → 按前瞻喂入 → 追增等待 */
  private async _pump(): Promise<void> {
    if (!this._alive || this._busy) return
    this._busy = true
    this._reachedWriteCursor = false
    try {
      let info = await localMediaInfo(this._path)
      if (!info.ok) {
        if (this._follow && this._missingCount < MAX_MISSING_RETRIES) {
          this._missingCount += 1
          return
        }
        this._fail(info.error || '录制文件不可读')
        return
      }
      this._missingCount = 0
      // 1) 保证 init 段就绪（缓存命中用缓存字节恢复，冷启动读文件头）
      await this._ensureInit(info.size)
      if (!this._located) {
        // 定位前把索引暖到 ≥2 个条目：不足时码率不可估，外推会盲跳文件头
        // （2026-09-13 真机两次 `定位: 目标 xxx -> 字节 0` 即此症状）
        await this._ensureEnoughIndex(info.size)
        await this._locate(info.size)
        this._located = true
        this._drainQueue()
      }
      for (;;) {
        if (!this._alive) return
        info = await localMediaInfo(this._path)
        if (!info.ok) {
          if (this._follow && this._missingCount < MAX_MISSING_RETRIES) {
            this._missingCount += 1
            return
          }
          this._fail(info.error || '录制文件不可读')
          return
        }
        const size = info.size
        if (this._readOffset >= size) {
          this._reachedWriteCursor = true
          // 追增等待期间也要继续排空队列：播放头前进后前瞻窗口会重新打开
          this._drainQueue()
          if (!this._follow && this._queue.length === 0 && this._splitter.pendingBytes === 0) {
            this._finish()
          }
          return
        }
        const length = Math.min(
          // 出画前用 4× 块加速定位追赶：远目标欠冲后的顺序扫描是首帧等待的
          // 主要耗时（2026-09-13 真机：300s 级 gap 在 1× 块下要 20s+）。
          this._sawMedia ? this._chunkBytes : this._chunkBytes * 4,
          size - this._readOffset,
        )
        const chunk = await localMediaRead(this._path, this._readOffset, length)
        if (!this._alive) return
        if (!chunk.ok) {
          this._fail(chunk.error || '录制文件读取失败')
          return
        }
        if (chunk.bytesRead <= 0) return
        this._readOffset += chunk.bytesRead
        this._index.scannedTo = this._readOffset
        this._consume(chunk.data ?? new Uint8Array(0))
        this._noteProgress()
        this._trimQueueBeforeTarget()
        this._drainQueue()
        if (this._queue.length > 0 && this._isAheadOfPlayback()) return
      }
    } finally {
      this._busy = false
      if (this._alive) this._schedule()
    }
  }

  /**
   * 确保 init 段已喂入并在解析器中恢复轨信息。
   *
   * 命中缓存索引时**不能跳过**这一步：MsePlayer 需要 init 段才会创建可用
   * SourceBuffer，否则后续 media 段全部作废（线上表现为"轴切了但画面仍是直播"）。
   */
  private async _ensureInit(size: number): Promise<void> {
    // 用方法调用而非属性判断：避免 TS 把 this._splitter.init 的可空类型
    // 沿函数体收窄成 never（_consume 会在中途赋值，CFA 看不到）。
    if (this._hasInit()) return
    const cachedInit = this._index.init
    if (cachedInit && cachedInit.byteLength > 0) {
      const result = this._splitter.push(cachedInit.slice())
      if (result.init) this._feedInit(result.init)
      this._splitter.resetPosition(this._readOffset)
      this._publishAxisOffset()
      return
    }
    const headLen = Math.min(this._chunkBytes, Math.max(0, size))
    if (headLen <= 0) return
    const head = await localMediaRead(this._path, 0, headLen)
    if (!this._alive || !head.ok || head.bytesRead <= 0) return
    // 已有索引（缓存命中）时，这次头部读取只为拿 init/轨信息：
    // 既不入队也不建索引。否则 `_consume` 会把"时间更早"的条目按时间排序插到
    // 队列前面，再想按数量回滚就会误删原本需要的条目（旧实现用
    // `entries.slice(0, entriesBefore)` 截尾，可能把定位目标所在的条目删掉）。
    const reuseIndexed = this._index.entries.length > 0
    this._consume(head.data ?? new Uint8Array(0), { indexEntries: !reuseIndexed })
    if (this._readOffset === 0) this._readOffset = head.bytesRead
    const initBytes = this._splitter.init
    if (initBytes) {
      this._index.init = initBytes.slice()
      if (this._splitter.pendingBytes === 0) this._index.scannedTo = Math.max(this._index.scannedTo, head.bytesRead)
    }
  }

  private _hasInit(): boolean {
    return Boolean(this._splitter.init || this._splitter.track)
  }

  private _feedInit(init: Uint8Array): void {
    this._player.feedInit(
      init.buffer.slice(init.byteOffset, init.byteOffset + init.byteLength) as ArrayBuffer,
    )
  }

  /**
   * 切分一块字节并登记索引。
   *
   * `indexEntries=false`（缓存命中的头部复读）时只喂 init、不排队也不建索引：
   * 该区间已在索引里，重复登记只会污染顺序。
   */
  private _consume(bytes: Uint8Array, opts?: { indexEntries?: boolean }): void {
    const indexEntries = opts?.indexEntries !== false
    this._noteProgress()
    const result = this._splitter.push(bytes)
    if (result.init) this._feedInit(result.init)
    let appendedOutOfOrder = false
    for (const segment of result.segments) {
      if (!indexEntries) continue
      this._queue.push(segment)
      if (this._index.offsets.has(segment.offset)) continue
      this._index.offsets.add(segment.offset)
      const tPtsSec = segment.tPtsSec ?? this._lastKnownTime()
      const last = this._index.entries[this._index.entries.length - 1]
      if (last && tPtsSec < last.tPtsSec) appendedOutOfOrder = true
      this._index.entries.push({ tPtsSec, offset: segment.offset })
    }
    // 多次 seek 会往后追加"时间更早"的区间：保持按时间有序，
    // 否则落点选择与跨度外推会在空洞处停下并选错 fragment。
    if (appendedOutOfOrder) this._index.entries.sort((a, b) => a.tPtsSec - b.tPtsSec)
    noteRate(this._index)
    this._publishAxisOffset()
  }

  private _lastKnownTime(): number {
    const last = this._index.entries[this._index.entries.length - 1]
    return last ? last.tPtsSec : 0
  }

  private _publishAxisOffset(): void {
    if (this._axisOffset != null) return
    if (this._index.entries.length === 0) return
    const firstPtsSec = this._index.entries[0].tPtsSec
    const derived = -firstPtsSec
    const hint = this._axisOffsetHint
    // 提示偏移与实测首帧不符（>1s）说明不是同一录制文件，以实测为准
    this._axisOffset = hint != null && Math.abs(hint - derived) < 1 ? hint : derived
    this._onIndex?.({ firstPtsSec, axisOffsetSec: this._axisOffset, targetClamped: this._targetClamped })
  }

  /** 首次定位：确保轴偏移可用后，把读游标有界定位到目标位置 */
  private async _locate(size: number): Promise<void> {
    this._publishAxisOffset()
    const target = this._targetAxisSec
    if (target == null || this._index.entries.length === 0) return
    const offset = this._axisOffset
    if (offset == null) return
    const targetFileSec = target - offset
    if (!Number.isFinite(targetFileSec)) return
    const firstEntry = this._index.entries[0]
    if (targetFileSec <= firstEntry.tPtsSec) {
      // 早于文件首帧：从文件头起播
      this._jumpTo(firstEntry.offset)
      return
    }
    // 目标被索引连续覆盖时**直接跳到包含目标的 fragment**；有空洞则走外推定位。
    // （若在空洞处误跳，命中缓存的回看会从更早的 fragment 顺序重读，出画极慢。）
    const covering = findCoveringFragment(this._index.entries, targetFileSec)
    if (covering) {
      this._jumpTo(covering.offset)
      return
    }
    await this._seekToFileTime(targetFileSec, size)
  }

  /** 把读游标跳到指定字节位置（清空队列与解析器余量，偏移基准同步） */
  private _jumpTo(offset: number): void {
    this._readOffset = Math.max(0, offset)
    this._index.scannedTo = this._readOffset
    this._splitter.resetPosition(this._readOffset)
    this._queue = []
  }

  /** 有界定位：按平均码率外推 → 窗口内回退到合法 fragment 边界 → 从该处继续顺序读取 */
  private async _seekToFileTime(targetFileSec: number, size: number): Promise<void> {
    if (this._index.entries.length === 0) return
    const covering = findCoveringFragment(this._index.entries, targetFileSec)
    const covered = covering != null
    if (covering) {
      this._readOffset = covering.offset
    } else {
      const estimate = estimateByteOffset(this._index, targetFileSec)
      // 窗口右端必须收敛在"外推位置"处：继续向右扩会落到文件末尾，
      // 使回看起点大幅晚于目标时间（实测偏晚 ~1000s）。
      const windowEnd = Math.min(size, Math.max(0, estimate))
      const windowStart = Math.max(0, windowEnd - SEEK_BACK_WINDOW_BYTES)
      const windowLen = Math.max(0, windowEnd - windowStart)
      let start = windowStart
      if (windowLen > 0) {
        const win = await localMediaRead(this._path, windowStart, windowLen)
        if (this._alive && win.ok && win.data) {
          const boundary = findFragmentBoundary(win.data, windowStart)
          if (boundary != null) start = boundary
        }
      }
      this._readOffset = Math.max(0, start)
    }
    console.info(
      `[LocalFileMseSource] 定位: 目标(文件轴) ${targetFileSec.toFixed(1)}s -> 字节 ${this._readOffset} / 文件 ${size}B，covered=${covered}`,
    )
    this._jumpTo(this._readOffset)
  }

  /** 把队列按「起播点 + 前向缓冲窗口」喂入 MSE */
  private _drainQueue(): void {
    while (this._queue.length > 0) {
      if (!this._sawMedia && !this._canStartFeeding()) return
      const segment = this._queue[0]
      if (this._sawMedia && !this._withinLookahead(segment)) return
      this._queue.shift()
      this._feed(segment)
    }
  }

  /**
   * 未开播时：等到队列中出现越过目标时间的分段，从其前一个分段起播。
   *
   * 目标越界（超出已写入范围）时**不得无限等待**：读到写入游标后仍找不到目标，
   * 就退化为"从最近可读位置起播"并标记 targetClamped。
   */
  private _canStartFeeding(): boolean {
    const target = this._targetFileSec()
    if (target == null) return true
    const idx = this._queue.findIndex((s) => s.tPtsSec != null && s.tPtsSec > target)
    if (idx === -1) {
      if (!this._reachedWriteCursor) return false
      const first = this._queue[0]
      if (!first) return false
      if (!this._targetClamped) {
        this._targetClamped = true
        const available = first.tPtsSec
        console.warn(
          `[LocalFileMseSource] 目标 ${target.toFixed(1)}s(文件轴) 超出已写入范围，退化为从 ${available == null ? '?' : available.toFixed(1)}s 起播`,
        )
        this._publishClamped()
      }
      return true
    }
    const start = Math.max(0, idx - 1)
    if (start > 0) this._queue = this._queue.slice(start)
    return true
  }

  private _publishClamped(): void {
    const first = this._index.entries[0]
    if (!first || this._axisOffset == null) return
    this._onIndex?.({
      firstPtsSec: first.tPtsSec,
      axisOffsetSec: this._axisOffset,
      targetClamped: true,
    })
  }

  private _targetFileSec(): number | null {
    if (this._targetAxisSec == null) return null
    const offset = this._axisOffset ?? this._axisOffsetHint
    if (offset == null) return null
    const value = this._targetAxisSec - offset
    return Number.isFinite(value) ? value : null
  }

  private _feed(segment: Fmp4MediaSegment): void {
    this._player.feedMedia(
      segment.bytes.buffer.slice(
        segment.bytes.byteOffset,
        segment.bytes.byteOffset + segment.bytes.byteLength,
      ) as ArrayBuffer,
    )
    if (!this._sawMedia) {
      this._sawMedia = true
      if (this._firstFrameTimer !== null) {
        clearTimeout(this._firstFrameTimer)
        this._firstFrameTimer = null
      }
      this._onFirstMedia?.()
      this._applyStartSeek()
    }
  }

  /** 首个分段喂入后把播放头落到目标时间（MsePlayer 的文件起点对齐会先落到 bufStart） */
  private _applyStartSeek(): void {
    if (this._seekedOnce) return
    this._seekedOnce = true
    let target = this._targetFileSec()
    if (target == null) return
    const last = this._index.entries[this._index.entries.length - 1]
    if (this._targetClamped && last) target = Math.min(target, last.tPtsSec)
    setTimeout(() => {
      if (!this._alive) return
      try {
        this._player.seek(Math.max(0, target as number))
      } catch {
        /* seek 失败不致命：播放器停在文件起点附近 */
      }
    }, 150)
  }

  private _withinLookahead(segment: Fmp4MediaSegment): boolean {
    const current = this._player.videoElement?.currentTime
    const t = segment.tPtsSec
    if (t == null || current == null) return true
    return t <= current + this._lookaheadSec
  }

  private _isAheadOfPlayback(): boolean {
    const last = this._queue[this._queue.length - 1]
    return Boolean(last && !this._withinLookahead(last))
  }

  private _finish(): void {
    if (this._ended) return
    this._ended = true
    this.dispose()
    // 已完成文件读到末尾：通知播放器"缓冲不再增长是正常结束"，
    // 否则会被卡顿检测判成直播流中断（或回跳重播）。
    this._player.markEndOfStream?.()
    console.info(`[LocalFileMseSource] 回看读到文件末尾: path=${this._path}`)
    this._onEnd?.()
  }
}
