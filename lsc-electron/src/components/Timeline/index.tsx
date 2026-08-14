import { useRef, useState, useCallback, useMemo, useEffect } from 'react'
import type { TimelineHighlightBand } from '@/types'
import { subscribeDisplayPlayhead } from '@/utils/playheadStore'
import './Timeline.css'

export interface TimelineClip {
  start: number
  end: number
  color?: string
  /** 稳定唯一标识（round_key/clip_id），用于 React key，避免越窗 clip 塌缩成重复 key */
  uid?: string
}

interface TimelineProps {
  duration: number
  currentTime: number
  markIn: number | null
  markOut: number | null
  onSeek: (time: number) => void
  /** 开始 scrub 时传入当前绝对 windowStart，供父级冻结坐标系 */
  onScrubStart?: (windowStart: number) => void
  onScrubEnd?: (finalTime?: number) => void
  onMarkIn: () => void
  onMarkOut: () => void
  onMarkerDrag?: (type: 'in' | 'out', time: number) => void
  onMarkerDragEnd?: (type: 'in' | 'out', time: number) => void
  onDeleteMarker?: (type: 'in' | 'out') => void
  buffered?: number
  clips?: TimelineClip[]
  highlights?: TimelineHighlightBand[]
  waveformPeaks?: number[]
  onHighlightClick?: (highlight: TimelineHighlightBand) => void
  height?: number
  zoomLevel?: number
  onZoomChange?: (zoom: number) => void
  windowStart?: number
  /** 精修区间硬色带 */
  activeRefine?: { start: number; end: number } | null
  /** DVR 可回看窗口左边界（绝对秒），紫标位置 */
  dvrStart?: number | null
  /** @deprecated 保留兼容；紫标已改用 dvrStart */
  recordedEnd?: number | null
  /** 跟随直播沿：播放头钉右；与 ControlBar displayCurrent 一致 */
  followLive?: boolean
  /** 父级 scrub 中（冻结窗）；订阅直写时跳过以免打架 */
  isScrubbing?: boolean
  /** 分析扫描进度（0~1）：已分析时长 / 总时长，用于背景进度条 */
  analysisProgress?: number
  /** 当前扫描范围 [start, end]（绝对秒），用于扫描范围指示 */
  scanRange?: [number, number] | null
}

const DEFAULT_CLIP_COLOR = 'rgba(63, 131, 248, 0.72)'
const SNAP_THRESHOLD = 0.85
const TICK_INTERVALS = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800, 3600, 7200, 14400, 21600, 43200]
/** 主刻度优先落在这些「关键阶段」上（秒）：1m / 2m / 5m / 10m / 15m / 30m / 1h … */
const MAJOR_LANDMARKS = [60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 14400, 21600, 43200]

function formatTime(seconds: number): string {
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = Math.floor(seconds % 60)
  return `${h.toString().padStart(2, '0')}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`
}

export function formatTickTime(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds + 1e-6))
  if (total >= 3600) {
    const h = Math.floor(total / 3600)
    const m = Math.floor((total % 3600) / 60)
    const s = total % 60
    if (m === 0 && s === 0) return `${h}h`
    if (s === 0) return `${h}h${m}m`
    return `${h}h${m}m${s}s`
  }
  if (total >= 60) {
    const m = Math.floor(total / 60)
    const s = total % 60
    return s > 0 ? `${m}m${s}s` : `${m}m`
  }
  return `${total}s`
}

export function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value))
}

export function chooseTickInterval(duration: number, zoom: number, targetSpacingPx: number, trackWidthPx: number, visibleWidthPx: number): number {
  const visibleDuration = duration / zoom
  // 使用可见区域宽度计算秒/像素，而非 trackWidthPx（放大后 trackWidthPx 是 zoom 倍宽度）
  const effectiveWidth = Math.max(visibleWidthPx || trackWidthPx / zoom, 1)
  const secondsPerPixel = visibleDuration / effectiveWidth
  const rawInterval = targetSpacingPx * secondsPerPixel
  for (const interval of TICK_INTERVALS) {
    if (interval >= rawInterval) return interval
  }
  return 3600
}

/** 主刻度间隔：落在关键阶段上，约为次刻度 4～8 倍，保证约每窗 3～6 个标签 */
export function chooseMajorInterval(minor: number, visibleDuration: number): number {
  const target = Math.max(minor * 4, visibleDuration / 5)
  for (const c of MAJOR_LANDMARKS) {
    if (c % minor === 0 && c >= target) return c
  }
  for (const c of MAJOR_LANDMARKS) {
    if (c % minor === 0 && c >= minor * 2) return c
  }
  return Math.max(minor * 5, 60)
}

/** 10 分钟 / 整点小时：强化竖线 */
export function isKeyStage(absSec: number): boolean {
  const t = Math.round(absSec)
  if (t <= 0) return false
  return t % 3600 === 0 || t % 600 === 0
}

type SnapType = 'mark' | 'highlight' | 'playhead' | 'tick' | 'record' | 'clip'

interface SnapTarget {
  time: number
  priority: number
  type?: SnapType
}

export function findSnapTarget(
  rawTime: number,
  duration: number,
  markIn: number | null,
  markOut: number | null,
  currentTime: number,
  tickInterval: number,
  highlights: TimelineHighlightBand[] = [],
  opts?: { skipCurrentTime?: boolean; clips?: TimelineClip[]; dvrStartRel?: number | null; recordedEnd?: number | null },
): number {
  const targets: SnapTarget[] = []
  if (markIn !== null) targets.push({ time: markIn, priority: 100, type: 'mark' })
  if (markOut !== null) targets.push({ time: markOut, priority: 100, type: 'mark' })
  for (const h of highlights) {
    targets.push({ time: h.start, priority: 90, type: 'highlight' })
    targets.push({ time: h.end, priority: 90, type: 'highlight' })
  }
  for (const c of opts?.clips ?? []) {
    targets.push({ time: c.start, priority: 88, type: 'clip' })
    targets.push({ time: c.end, priority: 88, type: 'clip' })
  }
  if (opts?.dvrStartRel != null) {
    targets.push({ time: opts.dvrStartRel, priority: 85, type: 'record' })
  }
  if (opts?.recordedEnd != null) {
    targets.push({ time: opts.recordedEnd, priority: 85, type: 'record' })
  }
  if (!opts?.skipCurrentTime) {
    targets.push({ time: currentTime, priority: 80, type: 'playhead' })
  }
  for (let t = 0; t <= duration; t += tickInterval) {
    targets.push({ time: t, priority: 50, type: 'tick' })
  }
  targets.sort((a, b) => b.priority - a.priority || Math.abs(a.time - rawTime) - Math.abs(b.time - rawTime))
  for (const target of targets) {
    if (Math.abs(target.time - rawTime) <= SNAP_THRESHOLD) {
      return target.time
    }
  }
  return rawTime
}

export function Timeline({
  duration,
  currentTime,
  markIn,
  markOut,
  onSeek,
  onScrubStart,
  onScrubEnd,
  onMarkIn,
  onMarkOut,
  onMarkerDrag,
  onMarkerDragEnd,
  onDeleteMarker,
  buffered = 0,
  clips = [],
  highlights = [],
  waveformPeaks: _waveformPeaks = [],
  onHighlightClick,
  height = 60,
  zoomLevel = 1,
  onZoomChange,
  windowStart = 0,
  activeRefine,
  dvrStart = null,
  recordedEnd = null,
  followLive = false,
  isScrubbing = false,
  analysisProgress,
  scanRange = null,
}: TimelineProps) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const trackRef = useRef<HTMLDivElement>(null)
  const playheadRef = useRef<HTMLDivElement>(null)
  const progressFillRef = useRef<HTMLDivElement>(null)
  const [, setIsDragging] = useState(false)
  const [, setDraggingMarker] = useState<'in' | 'out' | null>(null)
  /** 拖拽中本地乐观播放头（相对 windowStart），避免等父级重渲染才动 */
  const [dragTime, setDragTime] = useState<number | null>(null)
  const dragTimeRef = useRef<number | null>(null)
  const [hoverTime, setHoverTime] = useState<number | null>(null)
  const [trackWidth, setTrackWidth] = useState(800)
  const [scrollWidth, setScrollWidth] = useState(800)
  const rafRef = useRef<number | null>(null)
  const pendingTimeRef = useRef<number | null>(null)
  const isDraggingRef = useRef(false)
  const draggingMarkerRef = useRef<'in' | 'out' | null>(null)
  /** scrub 中本地光标；松手再正式 seek */
  const lastPreviewSeekTimeRef = useRef<number | null>(null)
  /** 同步挂在 window 上的监听，避免等 useEffect 才注册导致拖不动 */
  const windowDragCleanupRef = useRef<(() => void) | null>(null)
  const [snapFlash, setSnapFlash] = useState<{ time: number; type: SnapType | 'in' | 'out' } | null>(null)
  const snapFlashTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const ws = windowStart
  const dvrStartRef = useRef<number | null>(null)
  const recordedEndRef = useRef<number | null>(null)
  const followLiveRef = useRef(followLive)
  const isScrubbingPropRef = useRef(isScrubbing)
  const effectiveDurationRef = useRef(1)

  // 最新回调进 ref，window 监听始终读最新闭包
  const getTimeFromXRef = useRef<(clientX: number) => number>(() => 0)
  const snapTimeRef = useRef<(raw: number, skipCurrent?: boolean) => number>((t) => t)
  const onSeekRef = useRef(onSeek)
  const onMarkerDragRef = useRef(onMarkerDrag)
  const onMarkerDragEndRef = useRef(onMarkerDragEnd)
  const onScrubEndRef = useRef(onScrubEnd)
  const wsRef = useRef(ws)
  const triggerSnapFlashRef = useRef<(time: number, type: SnapType | 'in' | 'out' | 'playhead') => void>(() => {})

  const zoom = zoomLevel
  const effectiveDuration = useMemo(() => Math.max(duration || 1, 1), [duration])
  effectiveDurationRef.current = effectiveDuration
  followLiveRef.current = followLive
  isScrubbingPropRef.current = isScrubbing
  dragTimeRef.current = dragTime
  wsRef.current = ws

  useEffect(() => {
    const track = trackRef.current
    const scroll = scrollRef.current
    if (!track) return
    const updateWidth = () => {
      setTrackWidth(track.clientWidth)
      if (scroll) setScrollWidth(scroll.clientWidth)
    }
    updateWidth()
    const observer = new ResizeObserver(updateWidth)
    observer.observe(track)
    if (scroll) observer.observe(scroll)
    return () => observer.disconnect()
  }, [])

  // 卸载时清掉可能残留的 window 监听 + snapFlash 定时器
  useEffect(() => {
    return () => {
      windowDragCleanupRef.current?.()
      windowDragCleanupRef.current = null
      if (snapFlashTimer.current) {
        clearTimeout(snapFlashTimer.current)
        snapFlashTimer.current = null
      }
    }
  }, [])

  const tickInterval = useMemo(
    () => chooseTickInterval(effectiveDuration, zoom, 120, trackWidth, scrollWidth),
    [effectiveDuration, zoom, trackWidth, scrollWidth]
  )

  const getTimeFromX = useCallback((clientX: number): number => {
    const track = trackRef.current
    if (!track) return 0
    const rect = track.getBoundingClientRect()
    const ratio = clamp((clientX - rect.left) / rect.width, 0, 1)
    return ratio * effectiveDuration
  }, [effectiveDuration])

  const dvrStartRel = dvrStart !== null ? dvrStart - ws : null

  const snapTime = useCallback((rawTime: number, skipCurrentTime = false) => {
    return findSnapTarget(
      rawTime,
      effectiveDuration,
      markIn,
      markOut,
      currentTime,
      tickInterval,
      highlights,
      { skipCurrentTime, clips, dvrStartRel, recordedEnd: recordedEnd != null ? recordedEnd - ws : null },
    )
  }, [effectiveDuration, markIn, markOut, currentTime, tickInterval, highlights, clips, dvrStartRel, recordedEnd, ws])

  const triggerSnapFlash = useCallback((time: number, type: SnapType | 'in' | 'out' | 'playhead') => {
    setSnapFlash({ time, type })
    if (snapFlashTimer.current) clearTimeout(snapFlashTimer.current)
    snapFlashTimer.current = setTimeout(() => {
      setSnapFlash(null)
      snapFlashTimer.current = null
    }, 180)
  }, [])

  getTimeFromXRef.current = getTimeFromX
  snapTimeRef.current = snapTime
  onSeekRef.current = onSeek
  onMarkerDragRef.current = onMarkerDrag
  onMarkerDragEndRef.current = onMarkerDragEnd
  onScrubEndRef.current = onScrubEnd
  wsRef.current = ws
  dvrStartRef.current = dvrStart
  recordedEndRef.current = recordedEnd
  triggerSnapFlashRef.current = triggerSnapFlash

  const clampToDvrStart = useCallback((relTime: number, absWs: number): number => {
    const dvrStartAbs = dvrStartRef.current
    if (dvrStartAbs == null) return relTime
    const dvrStartRel = dvrStartAbs - absWs
    return relTime < dvrStartRel ? dvrStartRel : relTime
  }, [])

  const applyPointerTime = useCallback((clientX: number, seekPlayhead: boolean) => {
    const time = getTimeFromXRef.current(clientX)
    pendingTimeRef.current = time
    setHoverTime(time)
    if (rafRef.current !== null) return
    rafRef.current = requestAnimationFrame(() => {
      rafRef.current = null
      const t = pendingTimeRef.current
      if (t === null) return
      const marker = draggingMarkerRef.current
      const absWs = wsRef.current
      if (marker && onMarkerDragRef.current) {
        const snapped = snapTimeRef.current(t, false)
        if (Math.abs(snapped - t) > 0.01) {
          triggerSnapFlashRef.current(snapped, marker)
        }
        onMarkerDragRef.current(marker, snapped + absWs)
        return
      }
      if (seekPlayhead && isDraggingRef.current) {
        // scrub 中不磁吸旧播放头；光标只走本地 dragTime，不中途 onSeek（避免父级缩轨/video 抢回最右）
        let snapped = snapTimeRef.current(t, true)
        snapped = clampToDvrStart(snapped, absWs)
        if (Math.abs(snapped - t) > 0.01) {
          triggerSnapFlashRef.current(snapped, 'playhead')
        }
        setDragTime(snapped)
        lastPreviewSeekTimeRef.current = snapped + absWs
      }
    })
  }, [])

  const endPointerDrag = useCallback(() => {
    windowDragCleanupRef.current?.()
    windowDragCleanupRef.current = null

    if (draggingMarkerRef.current && onMarkerDragEndRef.current) {
      const t = pendingTimeRef.current
      if (t !== null) {
        const snapped = snapTimeRef.current(t, false)
        onMarkerDragEndRef.current(draggingMarkerRef.current, snapped + wsRef.current)
      }
    }
    const wasScrubbing = isDraggingRef.current
    const finalRel = pendingTimeRef.current
    isDraggingRef.current = false
    draggingMarkerRef.current = null
    setIsDragging(false)
    setDraggingMarker(null)
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current)
      rafRef.current = null
    }
    if (wasScrubbing) {
      let finalAbs: number | undefined
      if (finalRel !== null) {
        const snapped = clampToDvrStart(snapTimeRef.current(finalRel, true), wsRef.current)
        finalAbs = snapped + wsRef.current
        lastPreviewSeekTimeRef.current = finalAbs
      }
      // 松手由父级 onScrubEnd 正式落点（此时 scrubbing ref 仍为 true，避免先 onSeek 走 quiet）
      onScrubEndRef.current?.(finalAbs)
      setDragTime(null)
    } else {
      setDragTime(null)
    }
  }, [])

  const attachWindowDragListeners = useCallback(() => {
    windowDragCleanupRef.current?.()
    const onMove = (e: MouseEvent) => {
      applyPointerTime(e.clientX, isDraggingRef.current)
    }
    const onUp = () => {
      endPointerDrag()
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    windowDragCleanupRef.current = () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
  }, [applyPointerTime, endPointerDrag])

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    if (draggingMarkerRef.current) return
    const time = getTimeFromX(e.clientX)
    if (e.shiftKey) {
      e.preventDefault()
      onMarkIn()
      return
    }
    if (e.ctrlKey || e.metaKey) {
      e.preventDefault()
      onMarkOut()
      return
    }
    e.preventDefault()
    isDraggingRef.current = true
    setIsDragging(true)
    // 同步挂监听，不要等 useEffect
    attachWindowDragListeners()
    onScrubStart?.(ws)
    const snapped = clampToDvrStart(snapTime(time, true), ws)
    if (Math.abs(snapped - time) > 0.01) {
      triggerSnapFlash(snapped, 'playhead')
    }
    setDragTime(snapped)
    pendingTimeRef.current = snapped
    // 按下只动本地光标；正式 seek 在松手 onScrubEnd
    lastPreviewSeekTimeRef.current = snapped + ws
  }, [getTimeFromX, onMarkIn, onMarkOut, onScrubStart, snapTime, clampToDvrStart, ws, triggerSnapFlash, attachWindowDragListeners])

  const handleMouseMove = useCallback((e: React.MouseEvent) => {
    // 非拖拽时只更新 hover；拖拽由 window listener 处理
    if (isDraggingRef.current || draggingMarkerRef.current) return
    setHoverTime(getTimeFromX(e.clientX))
  }, [getTimeFromX])

  const handleMouseLeave = useCallback(() => {
    // 拖拽中不清 isDragging（由 window mouseup 结束）
    if (isDraggingRef.current || draggingMarkerRef.current) return
    setHoverTime(null)
  }, [])

  useEffect(() => {
    const scrollContainer = scrollRef.current
    if (!scrollContainer) return
    const handleWheel = (e: WheelEvent) => {
      if (e.ctrlKey || e.metaKey) {
        e.preventDefault()
        const delta = e.deltaY > 0 ? 0.8 : 1.25
        onZoomChange?.(clamp(zoom * delta, 1, 20))
      }
    }
    scrollContainer.addEventListener('wheel', handleWheel, { passive: false })
    return () => scrollContainer.removeEventListener('wheel', handleWheel)
  }, [zoom, onZoomChange])

  const handleMarkerMouseDown = useCallback((e: React.MouseEvent, type: 'in' | 'out') => {
    e.stopPropagation()
    e.preventDefault()
    draggingMarkerRef.current = type
    setDraggingMarker(type)
    attachWindowDragListeners()
  }, [attachWindowDragListeners])

  const displayPlayhead = dragTime != null ? dragTime : currentTime
  const progressPct = clamp((displayPlayhead / effectiveDuration) * 100, 0, 100)
  const bufferedPct = clamp((buffered / effectiveDuration) * 100, 0, 100)

  const applyPlayheadPct = useCallback((pct: number) => {
    const clampedPct = clamp(pct, 0, 100)
    if (playheadRef.current) playheadRef.current.style.left = `${clampedPct}%`
    if (progressFillRef.current) progressFillRef.current.style.width = `${clampedPct}%`
  }, [])

  // 拖拽中走本地 dragTime；非拖拽由 subscribeDisplayPlayhead 直写。
  // 禁止用低频 React progressPct 回写，否则每 500ms 会把 ~60fps 播放头拽回滞后位置。
  useEffect(() => {
    if (dragTime == null) return
    applyPlayheadPct(progressPct)
  }, [progressPct, dragTime, applyPlayheadPct])

  // 播放头高频通道：display 轴绝对时间 → 直写 DOM（60fps），不触发 React render
  useEffect(() => {
    return subscribeDisplayPlayhead((abs) => {
      if (dragTimeRef.current != null || isDraggingRef.current) return
      const dur = effectiveDurationRef.current
      const windowS = wsRef.current
      let rel: number
      if (followLiveRef.current && !isScrubbingPropRef.current) {
        rel = dur
      } else {
        rel = Math.min(Math.max(0, abs - windowS), dur)
      }
      applyPlayheadPct((rel / Math.max(dur, 1e-6)) * 100)
    })
  }, [applyPlayheadPct])

  // 切房 / 窗长变化时用 props 做一次冷启动对齐（仅非拖拽）
  useEffect(() => {
    if (dragTime != null) return
    if (followLive && !isScrubbing) {
      applyPlayheadPct(100)
      return
    }
    applyPlayheadPct(progressPct)
    // 刻意不依赖 progressPct：避免 500ms React 更新反复覆盖 rAF
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [followLive, isScrubbing, effectiveDuration, ws, applyPlayheadPct, dragTime])

  const selection = useMemo(() => {
    if (markIn === null || markOut === null) return null
    const left = clamp((markIn / effectiveDuration) * 100, 0, 100)
    const width = clamp(((markOut - markIn) / effectiveDuration) * 100, 0, 100 - left)
    return { left, width }
  }, [markIn, markOut, effectiveDuration])

  const markerInPct = markIn !== null ? clamp((markIn / effectiveDuration) * 100, 0, 100) : null
  const markerOutPct = markOut !== null ? clamp((markOut / effectiveDuration) * 100, 0, 100) : null
  const hoverPct = hoverTime !== null ? clamp((hoverTime / effectiveDuration) * 100, 0, 100) : null
  const dvrStartPct = dvrStart != null ? clamp(((dvrStart - ws) / effectiveDuration) * 100, 0, 100) : null

  const tickStep = Math.max(1, Math.round(tickInterval))
  const quantizedWs = Math.floor(ws / tickStep) * tickStep
  // 绝对刻度集合按量化窗缓存；相对 left 每帧用真实 ws 换算，避免 followLive 每秒重建+sort
  const tickAbs = useMemo(() => {
    const result: { abs: number; isMajor: boolean; isKey: boolean }[] = []
    const step = Math.max(1, Math.round(tickInterval))
    const majorInterval = chooseMajorInterval(step, effectiveDuration)
    const absStart = quantizedWs
    const absEnd = quantizedWs + effectiveDuration + step
    let abs = Math.ceil((absStart + 1e-9) / step) * step
    if (absStart <= 1e-9) abs = 0
    for (; abs <= absEnd + 1e-6; abs += step) {
      const absSec = Math.round(abs)
      const isMajor = absSec > 0 && absSec % majorInterval === 0
      result.push({
        abs: absSec,
        isMajor,
        isKey: isKeyStage(absSec),
      })
    }
    for (const landmark of MAJOR_LANDMARKS) {
      if (!isKeyStage(landmark)) continue
      if (landmark <= absStart || landmark >= absEnd) continue
      if (landmark % 3600 !== 0 && effectiveDuration > 2400) continue
      if (result.some((t) => t.abs === landmark)) continue
      result.push({
        abs: landmark,
        isMajor: true,
        isKey: true,
      })
    }
    result.sort((a, b) => a.abs - b.abs)
    return result
  }, [effectiveDuration, tickInterval, quantizedWs])

  const ticks = useMemo(() => {
    return tickAbs
      .map(({ abs, isMajor, isKey }) => ({
        time: abs - ws,
        abs,
        isMajor,
        isKey,
      }))
      .filter((t) => t.time >= -1e-6 && t.time <= effectiveDuration + 1e-6)
  }, [tickAbs, ws, effectiveDuration])

  const innerWidth = `${zoom * 100}%`

  // 波形已停用（档 B：去波形渲染）

  return (
    <div
      className="lsc-timeline"
      style={{ height }}
    >
      <div
        className="lsc-timeline__scroll"
        ref={scrollRef}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseLeave={handleMouseLeave}
      >
        <div
          className="lsc-timeline__inner"
          style={{ width: innerWidth, position: 'relative', height: '100%' }}
        >
          <div className="lsc-timeline__ruler">
            <span className="lsc-timeline__endpoint-time is-start">
              {formatTickTime(ws)}
            </span>
            {ticks.map(({ time, abs, isMajor, isKey }) => {
              const pct = (time / effectiveDuration) * 100
              const nearStart = pct < 2
              const nearEnd = pct > 98
              // 两端由精确窗口时间单独标注，临近端点的主刻度不重复显示。
              const showLabel = (isMajor || isKey) && !nearStart && !nearEnd
              return (
                <div
                  key={abs}
                  className={[
                    'lsc-timeline__tick',
                    isMajor || isKey ? 'major' : 'minor',
                    isKey ? 'lsc-timeline__tick--key' : '',
                  ].filter(Boolean).join(' ')}
                  style={{ left: `${pct}%` }}
                >
                  {showLabel && (
                    <span className="lsc-timeline__tick-label">
                      {formatTickTime(abs)}
                    </span>
                  )}
                </div>
              )
            })}
            <span className="lsc-timeline__endpoint-time is-end">
              {formatTickTime(ws + effectiveDuration)}
            </span>
          </div>

          <div
            className={`lsc-timeline__track${snapFlash ? ' lsc-timeline__track--snap' : ''}`}
            ref={trackRef}
          >
            {/* 波形已去除（档 B） */}
            <div className="lsc-timeline__buffered" style={{ width: `${bufferedPct}%` }} />
            <div
              ref={progressFillRef}
              className={`lsc-timeline__progress${snapFlash ? ' lsc-timeline__progress--snap' : ''}`}
              style={{ width: `${progressPct}%` }}
            />

            {/* 分析扫描进度背景条（P0: 分析扫描进度可视化） */}
            {analysisProgress != null && analysisProgress > 0 && (
              <div
                className="lsc-timeline__analysis-progress"
                style={{ width: `${clamp(analysisProgress * 100, 0, 100)}%` }}
                title={`已分析 ${Math.round(analysisProgress * 100)}%`}
              />
            )}

            {/* 当前扫描范围指示（P1: 扫描范围指示） */}
            {scanRange && scanRange[1] > scanRange[0] && (() => {
              const srLeft = clamp((scanRange[0] / effectiveDuration) * 100, 0, 100)
              const srWidth = clamp(((scanRange[1] - scanRange[0]) / effectiveDuration) * 100, 0, 100 - srLeft)
              return (
                <div
                  className="lsc-timeline__scan-range"
                  style={{ left: `${srLeft}%`, width: `${srWidth}%` }}
                  title={`扫描范围 ${formatTickTime(scanRange[0])}–${formatTickTime(scanRange[1])}`}
                />
              )
            })()}

            {highlights.map((h) => {
              const left = clamp((h.start / effectiveDuration) * 100, 0, 100)
              const width = clamp(((h.end - h.start) / effectiveDuration) * 100, 0, 100 - left)
              const dur = h.end - h.start
              const isAudioPending = h.confirm_status === 'audio_pending'
              const isPending = h.confirm_status === 'pending' || h.confirm_status === 'refining'
              const statusLabel = h.confirm_status === 'audio_pending' ? '音频待复核'
                : h.confirm_status === 'pending' ? '待确认'
                : h.confirm_status === 'refining' ? '调整中'
                : h.confirm_status === 'user_confirmed' ? '已确认'
                : h.confirm_status === 'ocr_confirmed' ? 'AI可导'
                : h.confirm_status === 'vision_confirmed' ? '视觉确认'
                : ''
              const precisionLabel = h.boundary_precision === 'audio_approximate' ? '音频粗定位' : ''
              const title = [
                h.reason || h.label || 'AI 高光',
                h.score != null ? `score ${h.score.toFixed(2)}` : '',
                `${formatTickTime(h.start)}–${formatTickTime(h.end)}（${formatTickTime(dur)}）`,
                statusLabel,
                precisionLabel,
              ].filter(Boolean).join(' · ')
              // 切片/高光条不渲染文字（纯色带），信息走 hover title
              // 样式类：audio_pending 使用橙色虚线，pending 使用蓝色虚线，confirmed 使用实色
              const highlightClass = isAudioPending ? 'lsc-timeline__highlight--audio-pending'
                : isPending ? 'lsc-timeline__highlight--pending'
                : 'lsc-timeline__highlight--confirmed'
              return (
                <div
                  key={h.id}
                  className={`lsc-timeline__highlight ${highlightClass}`}
                  style={{ left: `${left}%`, width: `${width}%` }}
                  title={title}
                  onClick={(e) => {
                    e.stopPropagation()
                    onHighlightClick?.(h)
                  }}
                />
              )
            })}

            {selection && (
              <div
                className="lsc-timeline__selection"
                style={{ left: `${selection.left}%`, width: `${selection.width}%` }}
              />
            )}

            {/* 精修区间硬色带 */}
            {activeRefine && (() => {
              const rLeft = clamp(((activeRefine.start - ws) / effectiveDuration) * 100, 0, 100)
              const rWidth = clamp(((activeRefine.end - activeRefine.start) / effectiveDuration) * 100, 0, 100 - rLeft)
              return (
                <div
                  className="lsc-timeline__refine-band"
                  style={{ left: `${rLeft}%`, width: `${rWidth}%` }}
                />
              )
            })()}

            {clips.map((clip) => {
              // 完全越窗的切片不渲染：clamp 后坐标为 (0,0)，既无意义也会与其它越窗切片产生重复 key
              if (clip.end <= 0) return null
              const left = clamp((clip.start / effectiveDuration) * 100, 0, 100)
              const width = clamp(((clip.end - clip.start) / effectiveDuration) * 100, 0, 100 - left)
              const clipKey = clip.uid || (clip.color ? `${clip.start}-${clip.end}-${clip.color}` : `${clip.start}-${clip.end}`)
              return (
                <div
                  key={clipKey}
                  className="lsc-timeline__clip"
                  style={{
                    left: `${left}%`,
                    width: `${width}%`,
                    background: clip.color || DEFAULT_CLIP_COLOR,
                  }}
                />
              )
            })}

            {dvrStartPct !== null && (
              <div
                className={`lsc-timeline__record-end${
                  snapFlash?.type === 'record' ? ' lsc-timeline__record-end--snap' : ''
                }`}
                style={{ left: `${dvrStartPct}%` }}
                title={`DVR 左边界 ${formatTime(dvrStart!)}：约实时−2分钟，左侧回跟播，右侧可回看`}
              />
            )}

            {markerInPct !== null && (
              <div
                className={`lsc-timeline__marker lsc-timeline__marker--in ${
                  snapFlash?.type === 'in' ? 'lsc-timeline__marker--snap' : ''
                }`}
                style={{ left: `${markerInPct}%` }}
                onMouseDown={(e) => handleMarkerMouseDown(e, 'in')}
                onContextMenu={(e) => {
                  e.preventDefault()
                  e.stopPropagation()
                  onDeleteMarker?.('in')
                }}
              >
                <span className="lsc-timeline__marker-label">入 {markIn !== null ? formatTime(markIn + ws) : ''}</span>
              </div>
            )}

            {markerOutPct !== null && (
              <div
                className={`lsc-timeline__marker lsc-timeline__marker--out ${
                  snapFlash?.type === 'out' ? 'lsc-timeline__marker--snap' : ''
                }`}
                style={{ left: `${markerOutPct}%` }}
                onMouseDown={(e) => handleMarkerMouseDown(e, 'out')}
                onContextMenu={(e) => {
                  e.preventDefault()
                  e.stopPropagation()
                  onDeleteMarker?.('out')
                }}
              >
                <span className="lsc-timeline__marker-label">出 {markOut !== null ? formatTime(markOut + ws) : ''}</span>
              </div>
            )}

            <div
              ref={playheadRef}
              className={`lsc-timeline__playhead ${
                snapFlash?.type === 'playhead' ? 'lsc-timeline__playhead--snap' : ''
              }${dragTime != null ? ' lsc-timeline__playhead--dragging' : ''}`}
              style={{ left: `${progressPct}%` }}
            />

            {hoverPct !== null && (
              <div className="lsc-timeline__tooltip" style={{ left: `${hoverPct}%` }}>
                {formatTime(hoverTime! + ws)}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
