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
import { RoomSession, ClipSegment, TimelineHighlightBand, ContinuousAnalysisStatus, TimelineProgressSummary } from '@/types'
import type { TimelineAlignStatus } from '@/utils/timelineCoords'
import { computeRecordedDurationHint, isNoDvrPreviewMode, isRecordingReviewMode, resolveLiveContentSpan, resolveRecordingReviewSpan, summarizeTimelineProgress } from '@/utils/timelineCoords'
import { computeTimelineWindow } from '@/utils/timelineWindow'
import { Timeline } from '@/components/Timeline'
import { formatTime } from '@/utils/time'
import { PLAYBACK_RATE_STEPS, type PlaybackRate } from '@/hooks/useKeyboardShortcuts'
import {
  readDisplayPlayhead,
  readLiveEdgeDisplay,
  retainClockLoop,
  subscribeClock,
  writeLiveEdgeBase,
} from '@/utils/playheadStore'

export interface TimelineViewModel {
  duration: number
  currentTime: number
  windowStart: number
  markIn: number | null
  markOut: number | null
  clips: { start: number; end: number; color?: string; uid?: string }[]
  highlights?: TimelineHighlightBand[]
  waveformPeaks?: number[]
  contentEnd?: number
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
  /** DVR 可回看窗口左边界（绝对秒）；Task 3 接入 bufStart */
  dvrStart?: number | null
  /** 当前控制栏所处轴（用于三轴标注展示） */
  axis?: TimelineProgressSummary['axis']
  /** 持续分析状态（用于展示分析轴进度） */
  continuousStatus?: ContinuousAnalysisStatus | null
  /** 分析扫描进度（0~1）：已分析时长 / 总时长 */
  analysisProgress?: number
  /** 当前扫描范围 [start, end]（绝对秒） */
  scanRange?: [number, number] | null
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
  if (prev.dvrStart !== next.dvrStart) return false
  if (prev.axis !== next.axis) return false
  if (prev.continuousStatus !== next.continuousStatus) return false
  if (prev.analysisProgress !== next.analysisProgress) return false
  if (prev.scanRange !== next.scanRange) return false

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
    a.record_output_path === b.record_output_path &&
    a.preview_mode === b.preview_mode
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
  recordedDurationHint = 0,
  dvrStart = null,
  axis = 'preview',
  continuousStatus = null,
  analysisProgress,
  scanRange = null,
}: ControlBarProps) {
  void _alignStatus
  const isRecordingReview = isRecordingReviewMode(room?.preview_mode)
  const goLiveDisabled = isNoDvrPreviewMode(room?.preview_mode)
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

  // 可视窗跟内容走；不设默认时长；光标贴内容右端（像原生预览进度条）
  const contentEdgeRef = useRef(1)
  const contentEdgeRoomRef = useRef<string | null>(null)
  const localTimeline = useMemo(() => {
    const roomId = room?.room_id ?? null
    if (contentEdgeRoomRef.current !== roomId) {
      contentEdgeRoomRef.current = roomId
      contentEdgeRef.current = 1
    }
    let cur = 0
    // 仅用预览轴时间；无预览 / 回看 / 非 Live 时允许录制全长与切片撑右沿
    let axisProgress = 0
    if (room?.mark_out !== null && room?.mark_out !== undefined && room.mark_out > 0) {
      axisProgress = room.mark_out
    }
    if (room?.mark_in != null && room.mark_in > axisProgress) {
      axisProgress = room.mark_in
    }
    if (previewPos > axisProgress) {
      axisProgress = previewPos
    }
    if (activeRefine && activeRefine.end > axisProgress) {
      axisProgress = activeRefine.end
    }
    if (activeRefine && activeRefine.start > axisProgress) {
      axisProgress = activeRefine.start
    }
    const roomClips = roomId
      ? clips.filter(c => (!c.room_id || c.room_id === roomId) && c.end > c.start)
      : []
    const recordedHint = computeRecordedDurationHint(room, recordedDurationHint)
    const reviewSpan = isRecordingReview
      ? resolveRecordingReviewSpan(previewPos, recordedHint, null, room?.mark_in, room?.mark_out)
      : 0
    const elapsed = resolveLiveContentSpan({
      axisProgress: Math.max(axisProgress, reviewSpan),
      clipEnds: roomClips.map(c => c.end),
      recordedHint,
      previewEnabled: Boolean(room?.preview_enabled),
      recordingReview: isRecordingReview,
      followLive,
    })
    // 右沿只增不减：回看时不得随 previewPos 收缩
    const rawEnd = Math.max(elapsed, previewPos, 0)
    const contentEnd = Math.max(contentEdgeRef.current, rawEnd, 1)
    contentEdgeRef.current = contentEnd
    const win = computeTimelineWindow({
      contentEnd,
      zoomLevel,
      followLive,
      scrubbing: isScrubbing,
      frozenWindowStart,
      playhead: Math.max(0, previewPos),
      prevWindowStart: frozenWindowStart ?? 0,
      refining:
        activeRefine && activeRefine.end > activeRefine.start
          ? { start: activeRefine.start, end: activeRefine.end }
          : null,
    })
    const ws = win.windowStart
    const dur = win.duration
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
    return { duration: dur, currentTime: cur, windowStart: ws, contentEnd }
  }, [
    room?.room_id, room?.mark_out, room?.mark_in, room?.preview_mode, room?.preview_enabled,
    room?.is_recording, room?.record_started_at,
    previewPos, tick, activeRefine, followLive, isScrubbing, frozenWindowStart,
    recordedDurationHint, isRecordingReview, clips, zoomLevel,
  ])

  const { duration, currentTime, windowStart } = timelineView ?? localTimeline
  const contentEndAbs = timelineView?.contentEnd ?? localTimeline.contentEnd

  // Live 右沿采样 → rAF 插值；时钟文案直写 DOM
  useEffect(() => {
    writeLiveEdgeBase(contentEndAbs)
  }, [contentEndAbs])

  const timeLabelRef = useRef<HTMLSpanElement>(null)
  useEffect(() => {
    const release = retainClockLoop()
    const unsub = subscribeClock(() => {
      const el = timeLabelRef.current
      if (!el) return
      // Live：右沿插值；回看/非 Live：读 playheadStore（每帧采样），禁止读 React props（500ms 才变）
      const t = (followLive && !isScrubbing)
        ? readLiveEdgeDisplay(true)
        : readDisplayPlayhead()
      el.textContent = formatTime(Math.max(0, t))
    })
    return () => {
      unsub()
      release()
    }
  }, [followLive, isScrubbing])
  // Timeline 内时间一律相对 windowStart；轨长 = 可视窗长度（无默认垫高）
  // 缩放时左缘 = windowStart（片段最左），未缩放短内容时 ws=0 即 0:00:00
  const trackDuration = Math.max(1, duration - windowStart)

  // 三轴进度摘要（仅用于 UI 展示，不改变三轴换算规则）
  const progressSummary = useMemo(() => {
    const previewPosition = timelineView ? timelineView.currentTime : currentTime
    return summarizeTimelineProgress({
      previewPosition,
      room,
      continuousRecorded: recordedDurationHint,
      continuousStatus,
      axis,
    })
  }, [timelineView, currentTime, room, recordedDurationHint, continuousStatus, axis])

  // 轴标签文案
  const axisLabel = useMemo(() => {
    switch (axis) {
      case 'common': return '公共轴'
      case 'recording_review': return '录制回看轴'
      default: return '预览轴'
    }
  }, [axis])

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
        uid: c.uid,
      }))
    }
    return clips
      .filter(c => c.room_id === room?.room_id && c.end > c.start)
      .map(c => ({
        start: Math.max(0, c.start - windowStart),
        end: Math.max(0, c.end - windowStart),
        uid: c.round_key ?? c.clip_id ?? '',
      }))
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
        padding: '8px 24px',
        background: 'var(--bg-secondary)',
        borderTop: '1px solid var(--border-default)',
        display: 'flex',
        flexDirection: 'column',
        gap: 8,
        flexShrink: 0,
        zIndex: 20,
      }}>
      {multiSelectCount > 0 && (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: 8 }}>
          <div style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 6,
            padding: '2px 10px',
            background: 'rgba(77, 196, 191, 0.08)',
            borderRadius: 4,
            fontSize: 11,
            color: 'var(--brand-400, #4DC4BF)',
            fontWeight: 500,
          }}>
            <span style={{ display: 'inline-block', width: 5, height: 5, borderRadius: '50%', background: 'var(--brand-400, #4DC4BF)' }} />
            {multiSelectCount} 房间 · 全局控制
          </div>
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
        dvrStart={dvrStart ?? null}
        followLive={followLive}
        isScrubbing={isScrubbing}
        height={64}
        zoomLevel={zoomLevel}
        onZoomChange={onZoomChange}
        analysisProgress={analysisProgress}
        scanRange={scanRange}
      />

      {/* 控制按钮 */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        marginTop: 4,
        flexWrap: 'wrap',
        gap: 8,
        rowGap: 6,
      }}>
        {/* 左侧：播放控制 + 选区操作 */}
        <Space size={2} wrap>
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

        {/* 中间：三轴语义化时间码（预览·录制·分析三轴独立标注） */}
        <Space size={2} align="center" style={{ flexShrink: 1, minWidth: 0 }}>
          {/* 轴标签 */}
          <span style={{
            fontSize: 10,
            fontWeight: 600,
            color: 'var(--brand-400)',
            whiteSpace: 'nowrap',
            marginRight: 4,
          }}>
            {axisLabel}
          </span>
          {/* 当前播放位置（单一时间读数） */}
          <Tooltip title="当前播放位置（预览流）">
            <span
              ref={timeLabelRef}
              style={{
              fontFamily: 'var(--font-mono)',
              fontSize: 14,
              color: 'var(--text-primary)',
              whiteSpace: 'nowrap',
            }}>
              {formatTime(progressSummary.previewPosition)}
            </span>
          </Tooltip>
        </Space>

        {/* 右侧：视图控制 + 添加切片 */}
        <Space size={2} wrap>
          {onGoLive && (
            <Tooltip title={goLiveDisabled ? '回看模式不可用' : '跳转到直播最新位置'}>
              <Button
                type="text"
                size="small"
                icon={<ThunderboltOutlined />}
                onClick={onGoLive}
                disabled={isDisabled || goLiveDisabled}
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
