import { memo, useMemo, useState, useEffect, useRef } from 'react'
import { Space, Button, Tooltip, Select } from 'antd'
import {
  StepBackwardOutlined,
  PlayCircleOutlined,
  PauseCircleOutlined,
  StepForwardOutlined,
  ScissorOutlined,
  AimOutlined,
  SyncOutlined,
  ThunderboltOutlined,
  ZoomInOutlined,
  ZoomOutOutlined,
  CompressOutlined,
} from '@ant-design/icons'
import { RoomSession, ClipSegment, TimelineHighlightBand } from '@/types'
import type { TimelineAlignStatus } from '@/utils/timelineCoords'
import { panTimelineWindowStart } from '@/utils/timelineCoords'
import { Timeline } from '@/components/Timeline'
import { formatTime } from '@/utils/time'
import { PLAYBACK_RATE_STEPS, type PlaybackRate } from '@/hooks/useKeyboardShortcuts'

export interface TimelineViewModel {
  duration: number
  currentTime: number
  windowStart: number
  markIn: number | null
  markOut: number | null
  clips: { start: number; end: number; color?: string }[]
  highlights?: TimelineHighlightBand[]
  waveformPeaks?: number[]
}

interface ControlBarProps {
  room: RoomSession | undefined
  multiSelectCount?: number
  loopPreview?: boolean
  clips?: ClipSegment[]
  previewPos?: number
  /** 跟随直播沿（窗口贴右）；用户 scrub 后为 false，左缘可回到 0:00:00 */
  followLive?: boolean
  /** 拖拽 scrub 中：冻结 windowStart */
  isScrubbing?: boolean
  frozenWindowStart?: number | null
  alignStatus?: TimelineAlignStatus
  timelineView?: TimelineViewModel | null
  onSeek: (time: number) => void
  onScrubStart?: (windowStart: number) => void
  onScrubEnd?: (finalTime?: number) => void
  onPlayPause: () => void
  onSeekBack: () => void
  onSeekFwd: () => void
  onMarkIn: () => void
  onMarkOut: () => void
  onAddClip: () => void
  onToggleLoop?: () => void
  onGoLive?: () => void
  playbackRate?: PlaybackRate
  onPlaybackRateChange?: (rate: PlaybackRate) => void
  zoomLevel?: number
  onZoomChange?: (zoom: number) => void
  onMarkerDrag?: (type: 'in' | 'out', time: number) => void
  onMarkerDragEnd?: (type: 'in' | 'out', time: number) => void
  onDeleteMarker?: (type: 'in' | 'out') => void
  onHighlightClick?: (highlight: TimelineHighlightBand) => void
  /** 本地拖拽 marker 的即时显示值 */
  localDragMark?: { type: 'in' | 'out'; time: number } | null
  /** 精修中选区（绝对时间，含 windowStart 偏移前的全局秒） */
  activeRefine?: { start: number; end: number } | null
  /** @deprecated 不再用于 windowStart；保留以兼容调用方 props */
  recordedDurationHint?: number
}

/**
 * ControlBar 自定义比较器：room 对象引用每次 rooms_updated 都会变，
 * 但只有影响控制栏渲染的字段变化时才需要重新渲染。
 */
function areControlBarPropsEqual(prev: ControlBarProps, next: ControlBarProps): boolean {
  if (prev.multiSelectCount !== next.multiSelectCount) return false
  if (prev.loopPreview !== next.loopPreview) return false
  if (prev.clips !== next.clips) return false
  if (prev.onSeek !== next.onSeek) return false
  if (prev.onPlayPause !== next.onPlayPause) return false
  if (prev.onSeekBack !== next.onSeekBack) return false
  if (prev.onSeekFwd !== next.onSeekFwd) return false
  if (prev.onMarkIn !== next.onMarkIn) return false
  if (prev.onMarkOut !== next.onMarkOut) return false
  if (prev.onAddClip !== next.onAddClip) return false
  if (prev.onToggleLoop !== next.onToggleLoop) return false
  if (prev.previewPos !== next.previewPos) return false
  if (prev.followLive !== next.followLive) return false
  if (prev.isScrubbing !== next.isScrubbing) return false
  if (prev.frozenWindowStart !== next.frozenWindowStart) return false
  if (prev.zoomLevel !== next.zoomLevel) return false
  if (prev.onZoomChange !== next.onZoomChange) return false
  if (prev.onGoLive !== next.onGoLive) return false
  if (prev.onScrubStart !== next.onScrubStart) return false
  if (prev.onScrubEnd !== next.onScrubEnd) return false
  if (prev.playbackRate !== next.playbackRate) return false
  if (prev.onPlaybackRateChange !== next.onPlaybackRateChange) return false
  if (prev.onMarkerDrag !== next.onMarkerDrag) return false
  if (prev.onMarkerDragEnd !== next.onMarkerDragEnd) return false
  if (prev.onDeleteMarker !== next.onDeleteMarker) return false
  if (prev.alignStatus !== next.alignStatus) return false
  if (prev.timelineView !== next.timelineView) return false
  if (prev.onHighlightClick !== next.onHighlightClick) return false
  if (prev.localDragMark !== next.localDragMark) return false
  if (prev.activeRefine !== next.activeRefine) return false
  if (prev.recordedDurationHint !== next.recordedDurationHint) return false

  const a = prev.room
  const b = next.room
  if (a === b) return true
  if (!a || !b) return a === b
  return (
    a.room_id === b.room_id &&
    a.preview_enabled === b.preview_enabled &&
    a.preview_paused === b.preview_paused &&
    a.is_recording === b.is_recording &&
    a.record_started_at === b.record_started_at &&
    a.mark_in === b.mark_in &&
    a.mark_out === b.mark_out &&
    a.record_output_path === b.record_output_path
  )
}

export const ControlBar = memo(function ControlBar({
  room,
  multiSelectCount = 0,
  loopPreview = false,
  clips = [],
  previewPos = 0,
  followLive = true,
  isScrubbing = false,
  frozenWindowStart = null,
  onSeek,
  onScrubStart,
  onScrubEnd,
  onPlayPause,
  onSeekBack,
  onSeekFwd,
  onMarkIn,
  onMarkOut,
  onAddClip,
  onToggleLoop,
  onGoLive,
  playbackRate = 1,
  onPlaybackRateChange,
  zoomLevel = 1,
  onZoomChange,
  onMarkerDrag,
  onMarkerDragEnd,
  onDeleteMarker,
  alignStatus: _alignStatus = 'local',
  timelineView = null,
  onHighlightClick,
  localDragMark,
  activeRefine = null,
  recordedDurationHint: _recordedDurationHint = 0,
}: ControlBarProps) {
  void _recordedDurationHint
  void _alignStatus
  // 录制中时每秒刷新一次时间显示，非录制时不触发
  const [tick, setTick] = useState(0)
  useEffect(() => {
    if (!room?.is_recording) return
    const id = setInterval(() => setTick(t => t + 1), 1000)
    return () => clearInterval(id)
  }, [room?.is_recording])

  const hasSelection = useMemo(() => {
    if (timelineView) {
      return timelineView.markIn != null && timelineView.markOut != null && timelineView.markIn < timelineView.markOut
    }
    return room?.mark_in !== null && room?.mark_out !== null && room?.mark_in !== undefined && room?.mark_out !== undefined
      && room.mark_in < room.mark_out
  }, [timelineView, room?.mark_in, room?.mark_out])

  const hasRecordingFile = !!room?.record_output_path
  const canAddClip = hasSelection && hasRecordingFile

  // 播放状态：预览已启用目未暂停时才显示为播放中
  const isPlaying = room ? (room.preview_enabled && !room.preview_paused) : false
  const isDisabled = !room && (multiSelectCount ?? 0) === 0

  // 可视窗跟内容走：不设默认时长；光标贴内容右端（像原生预览进度条）
  const TIMELINE_MAX_WINDOW = 600
  const contentEdgeRef = useRef(1)
  const contentEdgeRoomRef = useRef<string | null>(null)
  const localTimeline = useMemo(() => {
    const roomId = room?.room_id ?? null
    if (contentEdgeRoomRef.current !== roomId) {
      contentEdgeRoomRef.current = roomId
      contentEdgeRef.current = 1
    }
    let cur = 0
    // 仅用预览轴时间（previewPos / mark / refine），禁止混入录制墙钟与 recorded_duration
    let elapsed = 0
    if (room?.mark_out !== null && room?.mark_out !== undefined && room.mark_out > 0) {
      elapsed = room.mark_out
    }
    if (room?.mark_in != null && room.mark_in > elapsed) {
      elapsed = room.mark_in
    }
    if (previewPos > elapsed) {
      elapsed = previewPos
    }
    if (activeRefine && activeRefine.end > elapsed) {
      elapsed = activeRefine.end
    }
    if (activeRefine && activeRefine.start > elapsed) {
      elapsed = activeRefine.start
    }
    // 右沿只增不减：回看时不得随 previewPos 收缩
    const rawEnd = Math.max(elapsed, previewPos, 0)
    const contentEnd = Math.max(contentEdgeRef.current, rawEnd, 1)
    contentEdgeRef.current = contentEnd
    let ws = 0
    let dur = contentEnd
    if (activeRefine && activeRefine.end > activeRefine.start) {
      const mid = (activeRefine.start + activeRefine.end) / 2
      const half = Math.min(TIMELINE_MAX_WINDOW, Math.max(30, (activeRefine.end - activeRefine.start) * 4)) / 2
      ws = Math.max(0, mid - half)
      dur = Math.max(contentEnd, ws + half * 2, 1)
    } else if (contentEnd > TIMELINE_MAX_WINDOW) {
      dur = contentEnd
      if (followLive && !isScrubbing) {
        ws = contentEnd - TIMELINE_MAX_WINDOW
      } else if (isScrubbing && frozenWindowStart != null) {
        ws = frozenWindowStart
      } else {
        // scrub 后仅越界时平移；缩放窗左缘 = ws；短内容 ws=0 即 0:00:00
        const playhead = Math.max(0, previewPos)
        ws = panTimelineWindowStart(
          playhead,
          contentEnd,
          TIMELINE_MAX_WINDOW,
          frozenWindowStart ?? 0,
        )
      }
    }
    if (followLive && !isScrubbing) {
      cur = contentEnd
    } else if (previewPos > 0 || !followLive) {
      cur = Math.max(0, previewPos)
    } else if (room?.mark_in !== null && room?.mark_in !== undefined && room.mark_in > 0) {
      cur = room.mark_in
    } else if (activeRefine) {
      cur = activeRefine.start
    } else {
      cur = contentEnd
    }
    return { duration: dur, currentTime: cur, windowStart: ws }
  }, [
    room?.room_id, room?.mark_out, room?.mark_in,
    previewPos, tick, activeRefine, followLive, isScrubbing, frozenWindowStart,
  ])

  const { duration, currentTime, windowStart } = timelineView ?? localTimeline
  // Timeline 内时间一律相对 windowStart；轨长 = 可视窗长度（无默认垫高）
  // 缩放时左缘 = windowStart（片段最左），未缩放短内容时 ws=0 即 0:00:00
  const trackDuration = Math.max(1, duration - windowStart)

  const displayMarkIn = (() => {
    if (localDragMark?.type === 'in') {
      return Math.max(0, localDragMark.time - windowStart)
    }
    return timelineView
      ? (timelineView.markIn != null ? Math.max(0, timelineView.markIn - windowStart) : null)
      : (room?.mark_in != null ? Math.max(0, room.mark_in - windowStart) : null)
  })()
  const displayMarkOut = (() => {
    if (localDragMark?.type === 'out') {
      return Math.max(0, localDragMark.time - windowStart)
    }
    return timelineView
      ? (timelineView.markOut != null ? Math.max(0, timelineView.markOut - windowStart) : null)
      : (room?.mark_out != null ? Math.max(0, room.mark_out - windowStart) : null)
  })()
  const displayCurrentRaw = timelineView
    ? Math.max(0, timelineView.currentTime - windowStart)
    : Math.max(0, currentTime - windowStart)
  // Live：钉最右；非 Live / 拖拽中：跟真实位置（可回看）
  const displayCurrent = (followLive && !isScrubbing)
    ? trackDuration
    : Math.min(Math.max(0, displayCurrentRaw), trackDuration)

  const roomClips = useMemo(() => {
    if (timelineView) {
      return timelineView.clips.map(c => ({
        start: Math.max(0, c.start - windowStart),
        end: Math.max(0, c.end - windowStart),
        color: c.color,
      }))
    }
    return clips
      .filter(c => c.room_id === room?.room_id && c.end > c.start)
      .map(c => ({ start: Math.max(0, c.start - windowStart), end: Math.max(0, c.end - windowStart) }))
  }, [timelineView, clips, room?.room_id, windowStart])

  const timelineHighlights = useMemo(() => {
    if (!timelineView?.highlights) return []
    return timelineView.highlights.map(h => ({
      ...h,
      start: Math.max(0, h.start - windowStart),
      end: Math.max(0, h.end - windowStart),
    }))
  }, [timelineView?.highlights, windowStart])

  return (
    <div style={{
      padding: '12px 24px',
      background: 'var(--bg-secondary)',
      borderTop: '1px solid var(--border-default)',
      display: 'flex',
      flexDirection: 'column',
      gap: 12,
      flexShrink: 0,
      position: 'sticky',
      bottom: 0,
      zIndex: 20,
    }}>
      {multiSelectCount > 0 && (
        <div style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 8,
          padding: '4px 12px',
          background: 'rgba(0, 122, 255, 0.06)',
          borderRadius: 6,
          fontSize: 12,
          color: 'var(--accent-primary)',
          fontWeight: 500,
          alignSelf: 'flex-start',
        }}>
          <span style={{ display: 'inline-block', width: 6, height: 6, borderRadius: '50%', background: 'var(--accent-primary)' }} />
          {multiSelectCount} 个房间已选中 — 时间线 / 入出点 / 播放控制全局生效
        </div>
      )}
      <Timeline
        duration={trackDuration}
        currentTime={displayCurrent}
        markIn={displayMarkIn}
        markOut={displayMarkOut}
        buffered={displayCurrent}
        clips={roomClips}
        highlights={timelineHighlights}
        waveformPeaks={timelineView?.waveformPeaks}
        onHighlightClick={onHighlightClick
          ? (h) => onHighlightClick({
            ...h,
            start: h.start + windowStart,
            end: h.end + windowStart,
          })
          : undefined}
        windowStart={windowStart}
        onSeek={onSeek}
        onScrubStart={onScrubStart}
        onScrubEnd={onScrubEnd}
        onMarkIn={onMarkIn}
        onMarkOut={onMarkOut}
        onMarkerDrag={onMarkerDrag}
        onMarkerDragEnd={onMarkerDragEnd}
        onDeleteMarker={onDeleteMarker}
        activeRefine={activeRefine}
        height={96}
        zoomLevel={zoomLevel}
        onZoomChange={onZoomChange}
      />

      {/* 控制按钮 */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        marginTop: 4,
      }}>
        {/* 左侧：播放控制 + 选区操作 */}
        <Space size={2}>
          <Tooltip title="后退 10 秒">
            <Button 
              type="text" size="small"
              icon={<StepBackwardOutlined />}
              onClick={onSeekBack}
              disabled={isDisabled}
            />
          </Tooltip>
          
          <Tooltip title={isPlaying ? "暂停" : "播放"}>
            <Button 
              type="text" size="small"
              icon={isPlaying ? <PauseCircleOutlined /> : <PlayCircleOutlined />}
              onClick={onPlayPause}
              disabled={isDisabled}
              style={{ fontSize: 18 }}
            />
          </Tooltip>
          
          <Tooltip title="前进 10 秒">
            <Button 
              type="text" size="small"
              icon={<StepForwardOutlined />}
              onClick={onSeekFwd}
              disabled={isDisabled}
            />
          </Tooltip>

          {onPlaybackRateChange && (
            <Tooltip title="播放速率 (Shift+,/. 或 <> )">
              <Select
                size="small"
                value={playbackRate}
                onChange={(v) => onPlaybackRateChange(v as PlaybackRate)}
                disabled={isDisabled}
                style={{ width: 72 }}
                options={PLAYBACK_RATE_STEPS.map(r => ({ value: r, label: `${r}×` }))}
                popupMatchSelectWidth={false}
              />
            </Tooltip>
          )}

          <Tooltip title="设置入点 (I)">
            <Button 
              type="text" size="small"
              icon={<AimOutlined />}
              onClick={onMarkIn}
              disabled={isDisabled}
              style={{ color: room?.mark_in !== null ? 'var(--state-success)' : undefined }}
            >
              入点
            </Button>
          </Tooltip>
          
          <Tooltip title="设置出点 (O)">
            <Button 
              type="text" size="small"
              icon={<AimOutlined />}
              onClick={onMarkOut}
              disabled={isDisabled}
              style={{ color: room?.mark_out !== null ? 'var(--state-error)' : undefined }}
            >
              出点
            </Button>
          </Tooltip>
        </Space>

        {/* 中间：时间码 */}
        <Space size={2}>
          <span style={{
            fontFamily: 'monospace',
            fontSize: 14,
            color: 'var(--text-primary)',
          }}>
            {formatTime(timelineView ? timelineView.currentTime : currentTime)}
          </span>
          <span style={{ color: 'var(--text-tertiary)' }}>/</span>
          <span style={{
            fontFamily: 'monospace',
            fontSize: 14,
            color: 'var(--text-primary)',
          }}>
            {formatTime(duration)}
          </span>
        </Space>

        {/* 右侧：视图控制 + 添加切片 */}
        <Space size={2}>
          {onGoLive && (
            <Tooltip title="跳转到直播最新位置">
              <Button
                type="text"
                size="small"
                icon={<ThunderboltOutlined />}
                onClick={onGoLive}
                disabled={isDisabled}
              >
                直播
              </Button>
            </Tooltip>
          )}
          <Tooltip title={!hasRecordingFile ? '请先开始录制后再添加切片' : '添加到切片列表'}>
            <Button 
              type="text" size="small"
              icon={<ScissorOutlined />}
              onClick={onAddClip}
              disabled={!room || !canAddClip}
            >
              添加切片
            </Button>
          </Tooltip>
          {onToggleLoop && (
            <Tooltip title={loopPreview ? '停止试听选区' : '试听选区（循环播放入/出点）'}>
              <Button
                type={loopPreview ? 'primary' : 'text'}
                size="small"
                icon={<SyncOutlined spin={loopPreview} />}
                onClick={onToggleLoop}
                disabled={!hasSelection}
              />
            </Tooltip>
          )}
          {onZoomChange && (
            <>
              <Tooltip title="缩小时间线 (Ctrl+滚轮)">
                <Button
                  type="text"
                  size="small"
                  icon={<ZoomOutOutlined />}
                  onClick={() => onZoomChange(Math.max(1, zoomLevel / 1.5))}
                  disabled={zoomLevel <= 1}
                />
              </Tooltip>
              <span style={{ fontSize: 11, color: 'var(--text-tertiary)', minWidth: 32, textAlign: 'center', userSelect: 'none' }}>
                {zoomLevel.toFixed(1)}x
              </span>
              <Tooltip title="放大时间线 (Ctrl+滚轮)">
                <Button
                  type="text"
                  size="small"
                  icon={<ZoomInOutlined />}
                  onClick={() => onZoomChange(Math.min(20, zoomLevel * 1.5))}
                />
              </Tooltip>
              <Tooltip title="重置缩放">
                <Button
                  type="text"
                  size="small"
                  icon={<CompressOutlined />}
                  onClick={() => onZoomChange(1)}
                  disabled={zoomLevel === 1}
                />
              </Tooltip>
            </>
          )}
        </Space>
      </div>
    </div>
  )
}, areControlBarPropsEqual)
