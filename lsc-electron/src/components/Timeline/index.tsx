import { useRef, useState, useCallback, useMemo, useEffect } from 'react'
import './Timeline.css'

export interface TimelineClip {
  start: number
  end: number
  color?: string
}

interface TimelineProps {
  duration: number
  currentTime: number
  markIn: number | null
  markOut: number | null
  onSeek: (time: number) => void
  onMarkIn: () => void
  onMarkOut: () => void
  onMarkerDrag?: (type: 'in' | 'out', time: number) => void
  onDeleteMarker?: (type: 'in' | 'out') => void
  buffered?: number
  clips?: TimelineClip[]
  height?: number
  zoomLevel?: number
  onZoomChange?: (zoom: number) => void
  windowStart?: number
}

const DEFAULT_CLIP_COLOR = 'rgba(52, 199, 89, 0.25)'
const SNAP_THRESHOLD = 0.5
const TICK_INTERVALS = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800, 3600, 7200, 14400, 21600, 43200]

function formatTime(seconds: number): string {
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = Math.floor(seconds % 60)
  return `${h.toString().padStart(2, '0')}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`
}

function formatTickTime(seconds: number): string {
  if (seconds >= 3600) {
    const h = Math.floor(seconds / 3600)
    const m = Math.floor((seconds % 3600) / 60)
    return m > 0 ? `${h}h${m}m` : `${h}h`
  }
  if (seconds >= 60) {
    const m = Math.floor(seconds / 60)
    const s = seconds % 60
    return s > 0 ? `${m}m${s}s` : `${m}m`
  }
  return `${seconds}s`
}

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value))
}

function chooseTickInterval(duration: number, zoom: number, targetSpacingPx: number, trackWidthPx: number, visibleWidthPx: number): number {
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

interface SnapTarget {
  time: number
  priority: number
}

function findSnapTarget(
  rawTime: number,
  duration: number,
  markIn: number | null,
  markOut: number | null,
  currentTime: number,
  tickInterval: number,
): number {
  const targets: SnapTarget[] = []
  if (markIn !== null) targets.push({ time: markIn, priority: 100 })
  if (markOut !== null) targets.push({ time: markOut, priority: 100 })
  targets.push({ time: currentTime, priority: 80 })
  for (let t = 0; t <= duration; t += tickInterval) {
    targets.push({ time: t, priority: 50 })
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
  onMarkIn,
  onMarkOut,
  onMarkerDrag,
  onDeleteMarker,
  buffered = 0,
  clips = [],
  height = 60,
  zoomLevel = 1,
  onZoomChange,
  windowStart = 0,
}: TimelineProps) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const trackRef = useRef<HTMLDivElement>(null)
  const [isDragging, setIsDragging] = useState(false)
  const [draggingMarker, setDraggingMarker] = useState<'in' | 'out' | null>(null)
  const [hoverTime, setHoverTime] = useState<number | null>(null)
  const [trackWidth, setTrackWidth] = useState(800)
  const [scrollWidth, setScrollWidth] = useState(800)
  const rafRef = useRef<number | null>(null)
  const pendingTimeRef = useRef<number | null>(null)
  const ws = windowStart

  const zoom = zoomLevel
  const effectiveDuration = useMemo(() => Math.max(duration || 1, 1), [duration])

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

  const snapTime = useCallback((rawTime: number) => {
    return findSnapTarget(rawTime, effectiveDuration, markIn, markOut, currentTime, tickInterval)
  }, [effectiveDuration, markIn, markOut, currentTime, tickInterval])

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    if (draggingMarker) return
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
    setIsDragging(true)
    const snapped = snapTime(time)
    onSeek(snapped + ws)
  }, [getTimeFromX, onMarkIn, onMarkOut, onSeek, snapTime, draggingMarker, ws])

  const handleMouseMove = useCallback((e: React.MouseEvent) => {
    const time = getTimeFromX(e.clientX)
    setHoverTime(time)
    pendingTimeRef.current = time
    if (rafRef.current !== null) return
    rafRef.current = requestAnimationFrame(() => {
      rafRef.current = null
      const t = pendingTimeRef.current
      if (t === null) return
      if (draggingMarker && onMarkerDrag) {
        const snapped = snapTime(t)
        onMarkerDrag(draggingMarker, snapped + ws)
        return
      }
      if (isDragging) {
        const snapped = snapTime(t)
        onSeek(snapped + ws)
      }
    })
  }, [getTimeFromX, isDragging, onSeek, snapTime, draggingMarker, onMarkerDrag, ws])

  const handleMouseUp = useCallback(() => {
    setIsDragging(false)
    setDraggingMarker(null)
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current)
      rafRef.current = null
    }
  }, [])

  const handleMouseLeave = useCallback(() => {
    setHoverTime(null)
    setIsDragging(false)
    setDraggingMarker(null)
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
    setDraggingMarker(type)
  }, [])

  const progressPct = clamp((currentTime / effectiveDuration) * 100, 0, 100)
  const bufferedPct = clamp((buffered / effectiveDuration) * 100, 0, 100)

  const selection = useMemo(() => {
    if (markIn === null || markOut === null) return null
    const left = clamp((markIn / effectiveDuration) * 100, 0, 100)
    const width = clamp(((markOut - markIn) / effectiveDuration) * 100, 0, 100 - left)
    return { left, width }
  }, [markIn, markOut, effectiveDuration])

  const markerInPct = markIn !== null ? clamp((markIn / effectiveDuration) * 100, 0, 100) : null
  const markerOutPct = markOut !== null ? clamp((markOut / effectiveDuration) * 100, 0, 100) : null
  const hoverPct = hoverTime !== null ? clamp((hoverTime / effectiveDuration) * 100, 0, 100) : null

  const ticks = useMemo(() => {
    const result: { time: number; isMajor: boolean }[] = []
    const majorInterval = tickInterval * 5
    for (let t = 0; t <= effectiveDuration; t += tickInterval) {
      const isMajor = Math.round(t / majorInterval) * majorInterval === Math.round(t)
      result.push({ time: t, isMajor })
    }
    return result
  }, [effectiveDuration, tickInterval])

  const innerWidth = `${zoom * 100}%`

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
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseLeave}
      >
        <div
          className="lsc-timeline__inner"
          style={{ width: innerWidth, position: 'relative', height: '100%' }}
        >
          <div className="lsc-timeline__timecode">
            <span>{formatTime(ws)}</span>
            <span>{formatTime(ws + effectiveDuration)}</span>
          </div>

          <div className="lsc-timeline__ruler">
            {ticks.map(({ time, isMajor }) => {
              const pct = (time / effectiveDuration) * 100
              return (
                <div
                  key={time}
                  className={`lsc-timeline__tick ${isMajor ? 'major' : 'minor'}`}
                  style={{ left: `${pct}%` }}
                >
                  {isMajor && (
                    <span className="lsc-timeline__tick-label">{formatTickTime(time + ws)}</span>
                  )}
                </div>
              )
            })}
          </div>

          <div className="lsc-timeline__track" ref={trackRef}>
            <div className="lsc-timeline__buffered" style={{ width: `${bufferedPct}%` }} />
            <div className="lsc-timeline__progress" style={{ width: `${progressPct}%` }} />

            {selection && (
              <div
                className="lsc-timeline__selection"
                style={{ left: `${selection.left}%`, width: `${selection.width}%` }}
              />
            )}

            {clips.map((clip, index) => {
              const left = clamp((clip.start / effectiveDuration) * 100, 0, 100)
              const width = clamp(((clip.end - clip.start) / effectiveDuration) * 100, 0, 100 - left)
              return (
                <div
                  key={index}
                  className="lsc-timeline__clip"
                  style={{
                    left: `${left}%`,
                    width: `${width}%`,
                    background: clip.color || DEFAULT_CLIP_COLOR,
                  }}
                />
              )
            })}

            {markerInPct !== null && (
              <div
                className="lsc-timeline__marker lsc-timeline__marker--in"
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
                className="lsc-timeline__marker lsc-timeline__marker--out"
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

            <div className="lsc-timeline__playhead" style={{ left: `${progressPct}%` }} />

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
