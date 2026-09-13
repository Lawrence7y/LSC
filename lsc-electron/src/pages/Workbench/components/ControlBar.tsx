import { memo, useMemo, useState, useEffect, useRef } from 'react'
import { Space, Button, Tooltip, Select, Input } from 'antd'
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
import { computeRecordedDurationHint, isRecordingReviewMode, previewToRecordingLocal, resolveLiveContentSpan, resolveRecordingReviewSpan, summarizeTimelineProgress } from '@/utils/timelineCoords'
import { computeTimelineWindow } from '@/utils/timelineWindow'
import { Timeline, type TimelineBufferedRange } from '@/components/Timeline'
import { formatTime } from '@/utils/time'
import { useI18n } from '@/i18n'
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
  /** 拖拽 scrub 移动时的绝对时间回调（供父级极速画面跟随寻道） */
  onScrubMove?: (time: number) => void
  /** 本地拖拽 marker 的即时显示值 */
  localDragMark?: { type: 'in' | 'out'; time: number } | null
  /** 精修中选区（绝对时间，含 windowStart 偏移前的全局秒） */
  activeRefine?: { start: number; end: number } | null
  /** @deprecated 不再用于 windowStart；保留以兼容调用方 props */
  recordedDurationHint?: number
  /** DVR 可回看窗口左边界（绝对秒）；Task 3 接入 bufStart */
  dvrStart?: number | null
  /** 当前房间 buffered ranges，使用与 timelineView 相同的显示轴。 */
  bufferedRanges?: TimelineBufferedRange[]
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
  if (prev.onScrubMove !== next.onScrubMove) return false
  if (prev.localDragMark !== next.localDragMark) return false
  if (prev.activeRefine !== next.activeRefine) return false
  if (prev.recordedDurationHint !== next.recordedDurationHint) return false
  if (prev.dvrStart !== next.dvrStart) return false
  if (prev.bufferedRanges !== next.bufferedRanges) return false
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
    && a.recording_to_preview_delta === b.recording_to_preview_delta
    && a.preview_clock_epoch_id === b.preview_clock_epoch_id
    && a.recording_id === b.recording_id
    && a.preview_review_start_sec === b.preview_review_start_sec
  )
}

function parseTimecode(text: string): number | null {
  const str = text.trim()
  if (!str) return null
  if (!isNaN(Number(str))) {
    return Math.max(0, Number(str))
  }
  const parts = str.split(':')
  if (parts.length === 2) {
    const m = parseFloat(parts[0])
    const s = parseFloat(parts[1])
    if (!isNaN(m) && !isNaN(s)) return m * 60 + s
  } else if (parts.length === 3) {
    const h = parseFloat(parts[0])
    const m = parseFloat(parts[1])
    const s = parseFloat(parts[2])
    if (!isNaN(h) && !isNaN(m) && !isNaN(s)) return h * 3600 + m * 60 + s
  }
  return null
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
  alignStatus = 'local',
  timelineView = null,
  onHighlightClick,
  onScrubMove,
  localDragMark,
  activeRefine = null,
  recordedDurationHint = 0,
  dvrStart = null,
  bufferedRanges = [],
  axis = 'preview',
  continuousStatus = null,
  analysisProgress,
  scanRange = null,
}: ControlBarProps) {
  const { t } = useI18n()
  const isRecordingReview = isRecordingReviewMode(room?.preview_mode)
  // 本地回看态可以随时回到直播；只有主播离线退化（degraded）才没有实时沿可跳
  const goLiveDisabled = room?.preview_mode === 'degraded' && !room?.is_recording
  const [isEditingTime, setIsEditingTime] = useState(false)
  // 录制中时每秒刷新一次时间显示，非录制时不触发
  const [tick, setTick] = useState(0)
  useEffect(() => {
    if (!room?.is_recording) return
    const id = setInterval(() => setTick(t => t + 1), 1000)
    return () => clearInterval(id)
  }, [room?.is_recording])

  // 单房间录制态时间线以 recording_local 为显示轴；MSE currentTime 仍是
  // preview_local，因此播放头和标记需要转换后再交给 Timeline 渲染。
  const localRecordingAxis = !timelineView && (axis === 'recording' || axis === 'recording_review')
  // 回看轴偏移带符号（本地文件原始 PTS 基座）+ 播放头 → 录制轴
  const reviewStartSec = isRecordingReview
    ? (Number(room?.preview_review_start_sec) || 0)
    : 0
  const localPlayhead = localRecordingAxis
    ? isRecordingReview
      ? previewPos + reviewStartSec
      : (previewToRecordingLocal(room, previewPos) ?? previewPos)
    : previewPos
  const localMarkIn = localRecordingAxis && !isRecordingReview && room?.mark_in != null
    ? (previewToRecordingLocal(room, room.mark_in) ?? room.mark_in)
    : room?.mark_in ?? null
  const localMarkOut = localRecordingAxis && !isRecordingReview && room?.mark_out != null
    ? (previewToRecordingLocal(room, room.mark_out) ?? room.mark_out)
    : room?.mark_out ?? null

  const hasSelection = useMemo(() => {
    if (timelineView) {
      return timelineView.markIn != null && timelineView.markOut != null && timelineView.markIn < timelineView.markOut
    }
    return room?.mark_in !== null && room?.mark_out !== null && room?.mark_in !== undefined && room?.mark_out !== undefined
      && room.mark_in < room.mark_out
  }, [timelineView, room?.mark_in, room?.mark_out])

  const hasRecordingFile = !!room?.record_output_path
  const canAddClip = hasSelection && hasRecordingFile

  // 主按钮禁用时给出确切原因（antd  disabled 按钮不抛鼠标事件，必须包一层才能弹 tooltip）
  const addClipTip = room == null
    ? t('请先选择一个房间')
    : !hasRecordingFile
      ? t('该房间还没有录制文件，先开始录制才能切片') // 请先开始录制
      : !hasSelection
        ? t('先用 I / O 标记入点与出点（或在时间线上拖出选区）')
        : t('将当前选区添加到切片列表')

  // 播放状态：预览已启用目未暂停时才显示为播放中
  const isPlaying = room ? (room.preview_enabled && !room.preview_paused) : false
  const isDisabled = !room && (multiSelectCount ?? 0) === 0

  // 可视窗跟内容走；不设默认时长；光标贴内容右端（像原生预览进度条）
  const contentEdgeRef = useRef(0)
  const contentEdgeRoomRef = useRef<string | null>(null)
  const localTimeline = useMemo(() => {
    const roomId = room?.room_id ?? null
    if (contentEdgeRoomRef.current !== roomId) {
      contentEdgeRoomRef.current = roomId
      contentEdgeRef.current = 0
    }
    let cur = 0
    // 录制情况下以录制轴为准（基准进度为 recordedHint）；无预览 / 回看 / 非 Live 时允许录制全长与切片撑右沿
    const isRecording = Boolean(room?.is_recording)
    const isPreviewActive = Boolean(room?.preview_enabled && room?.is_connected)
    const hasActiveMedia = isRecording || isPreviewActive || isRecordingReview || (localPlayhead > 0)
    const recordedHint = computeRecordedDurationHint(room, recordedDurationHint)
    let axisProgress = 0
    if (isRecording) {
      axisProgress = recordedHint
    }
    if (localMarkOut !== null && localMarkOut !== undefined && localMarkOut > 0) {
      if (localMarkOut > axisProgress) axisProgress = localMarkOut
    }
    if (localMarkIn != null && localMarkIn > axisProgress) {
      axisProgress = localMarkIn
    }
    if (!isRecording && localPlayhead > axisProgress) {
      axisProgress = localPlayhead
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
    const reviewSpan = isRecordingReview
      ? resolveRecordingReviewSpan(localPlayhead, recordedHint, null, room?.mark_in, room?.mark_out)
      : 0
    const elapsed = resolveLiveContentSpan({
      axisProgress: Math.max(axisProgress, reviewSpan),
      clipEnds: roomClips.map(c => c.end),
      recordedHint,
      previewEnabled: Boolean(room?.preview_enabled),
      recordingReview: isRecordingReview,
      followLive,
      isRecording,
    })
    // 右沿只增不减：回看时不得随 previewPos 收缩
    // 若无流活动且无切片/标记，保持为 0，避免启动即自增走表
    const hasStaticMarks = axisProgress > 0 || roomClips.length > 0
    const rawEnd = Math.max(elapsed, isRecording ? recordedHint : localPlayhead, 0)
    const contentEnd = (hasActiveMedia || hasStaticMarks)
      ? Math.max(contentEdgeRef.current, rawEnd, 1)
      : 0
    contentEdgeRef.current = contentEnd
    const win = computeTimelineWindow({
      contentEnd: Math.max(contentEnd, 1),
      zoomLevel,
      followLive,
      scrubbing: isScrubbing,
      frozenWindowStart,
      playhead: Math.max(0, isRecording && followLive && !isScrubbing ? recordedHint : localPlayhead),
      prevWindowStart: frozenWindowStart ?? 0,
      refining:
        activeRefine && activeRefine.end > activeRefine.start
          ? { start: activeRefine.start, end: activeRefine.end }
          : null,
    })
    const ws = win.windowStart
    const dur = win.duration
    if (!hasActiveMedia && !activeRefine && !(localMarkIn != null && localMarkIn > 0)) {
      cur = 0
    } else if (followLive && !isScrubbing) {
      cur = isRecording ? Math.max(contentEnd, recordedHint) : contentEnd
    } else if (localPlayhead > 0 || !followLive) {
      cur = Math.max(0, localPlayhead)
    } else if (localMarkIn !== null && localMarkIn !== undefined && localMarkIn > 0) {
      cur = localMarkIn
    } else if (activeRefine) {
      cur = activeRefine.start
    } else {
      cur = contentEnd
    }
    return { duration: dur, currentTime: cur, windowStart: ws, contentEnd }
  }, [
    room?.room_id, room?.mark_out, room?.mark_in, room?.preview_mode, room?.preview_enabled,
    room?.is_recording, room?.record_started_at, room?.recording_to_preview_delta,
    room?.preview_review_start_sec, reviewStartSec,
    previewPos, localPlayhead, localMarkIn, localMarkOut, localRecordingAxis,
    tick, activeRefine, followLive, isScrubbing, frozenWindowStart,
    recordedDurationHint, isRecordingReview, clips, zoomLevel,
  ])

  const { duration, currentTime, windowStart } = timelineView ?? localTimeline
  const contentEndAbs = timelineView?.contentEnd ?? localTimeline.contentEnd
  const isRoomRecording = Boolean(room?.is_recording)
  const recordedDurationNow = computeRecordedDurationHint(room, recordedDurationHint)

  // Live 右沿采样 → rAF 插值；时钟文案直写 DOM
  useEffect(() => {
    const hasActiveMedia = Boolean(room?.is_recording || (room?.preview_enabled && room?.is_connected && !room?.preview_paused) || isRecordingReview)
    if (hasActiveMedia && contentEndAbs > 0) {
      writeLiveEdgeBase(contentEndAbs)
    } else {
      writeLiveEdgeBase(0)
    }
  }, [contentEndAbs, room?.is_recording, room?.preview_enabled, room?.is_connected, room?.preview_paused, isRecordingReview])

  const timeLabelRef = useRef<HTMLSpanElement>(null)
  useEffect(() => {
    const release = retainClockLoop()
    const unsub = subscribeClock(() => {
      const el = timeLabelRef.current
      if (!el) return
      // 无流活动且无标记时：固定显示 00:00，禁止后台空跑计时
      const hasMedia = Boolean(room?.is_recording || (room?.preview_enabled && room?.is_connected) || isRecordingReview || Boolean(timelineView) || (localPlayhead > 0))
      if (!hasMedia && !activeRefine && !(localMarkIn != null && localMarkIn > 0)) {
        el.textContent = formatTime(0)
        return
      }
      // 录制且跟随直播时以已录制时长为准
      if (isRoomRecording && followLive && !isScrubbing) {
        el.textContent = formatTime(Math.max(0, recordedDurationNow))
        return
      }
      // 暂停态：显示固定暂停位置，不插值走秒
      if (!isRoomRecording && room?.preview_paused) {
        const pausedPos = previewPos > 0 ? previewPos : readDisplayPlayhead()
        const displayPaused = localRecordingAxis
          ? isRecordingReview
            ? pausedPos + reviewStartSec
            : (previewToRecordingLocal(room, pausedPos) ?? pausedPos)
          : pausedPos
        el.textContent = formatTime(Math.max(0, displayPaused))
        return
      }
      // Live：右沿插值；回看/非 Live：读 playheadStore（每帧采样），禁止读 React props（500ms 才变）
      const t = (followLive && !isScrubbing)
        ? readLiveEdgeDisplay(true)
        : readDisplayPlayhead()
      // display playhead / live edge 已经由当前时间线轴写入，不再重复转换。
      el.textContent = formatTime(Math.max(0, t))
    })
    return () => {
      unsub()
      release()
    }
  }, [followLive, isScrubbing, isRoomRecording, recordedDurationNow, room?.preview_enabled, room?.is_connected, room?.preview_paused, room?.mark_in, room?.recording_to_preview_delta, room?.preview_review_start_sec, isRecordingReview, reviewStartSec, localMarkIn, localPlayhead, localRecordingAxis, timelineView, previewPos, activeRefine])
  // Timeline 内时间一律相对 windowStart；轨长 = 可视窗长度（无默认垫高）
  // 缩放时左缘 = windowStart（片段最左），未缩放短内容时 ws=0 即 0:00:00
  const trackDuration = Math.max(1, duration - windowStart)

  // 三轴进度摘要（仅用于 UI 展示，不改变三轴换算规则）
  const progressSummary = useMemo(() => {
    const hasMedia = Boolean(room?.is_recording || (room?.preview_enabled && room?.is_connected) || isRecordingReview || Boolean(timelineView) || (previewPos > 0))
    let previewPosition = 0
    if (!hasMedia && !activeRefine && !(localMarkIn != null && localMarkIn > 0)) {
      previewPosition = 0
    } else if (isRoomRecording && followLive && !isScrubbing) {
      previewPosition = recordedDurationNow
    } else {
      previewPosition = timelineView ? timelineView.currentTime : currentTime
    }
    return summarizeTimelineProgress({
      previewPosition,
      room,
      continuousRecorded: recordedDurationHint,
      continuousStatus,
      axis,
    })
  }, [timelineView, currentTime, room, recordedDurationHint, continuousStatus, axis, isRoomRecording, followLive, isScrubbing, recordedDurationNow, isRecordingReview, previewPos, activeRefine])

  // 参考系标签：区分多房对齐、对齐失效、文件回看、录制中、以及纯预览未对齐
  const alignBadge = useMemo(() => {
    if (alignStatus === 'ready') return { tone: 'ready', text: t('公共轴 · 已对齐') }
    if (alignStatus === 'invalidated') return { tone: 'warn', text: t('预览轴 · 对齐已失效') }
    if (isRecordingReview) return { tone: 'idle', text: t('录制回看轴') }
    if (isRoomRecording) {
      if (multiSelectCount > 1) {
        return { tone: 'recording', text: t('录制轴 · 多房未对齐') }
      }
      return { tone: 'recording', text: t('录制轴') }
    }
    return { tone: 'idle', text: t('预览轴 · 未对齐') }
  }, [alignStatus, isRecordingReview, isRoomRecording, multiSelectCount, t])

  const alignBadgeTip = alignBadge.tone === 'ready'
    ? t('多房间已按音频对齐，时间线为公共轴：各房间的标记与切片可直接互相对照')
    : alignBadge.tone === 'warn'
      ? t('公共时间轴已失效（刷新预览 / 重连后会发生），时间线已退回各房间自己的预览轴；重新「一键对齐」后恢复')
      : isRecordingReview
        ? t('正在回看已录制文件，时间轴为录制文件时间轴')
        : isRoomRecording
          ? (multiSelectCount > 1
              ? t('多房间正在独立录制，时间线为参考房录制轴；多房间精确同步切片可点击「一键对齐」建立公共轴')
              : t('当前房间正在录制中，时间轴为本房间录制时间轴；单房间切片直接对应录制文件，无需跨房对齐'))
          : t('尚未做多房间音频对齐，时间线为当前房间的预览轴；单房间切片不受影响')

  const displayMarkIn = (() => {
    if (localDragMark?.type === 'in') {
      return Math.max(0, localDragMark.time - windowStart)
    }
    return timelineView
      ? (timelineView.markIn != null ? Math.max(0, timelineView.markIn - windowStart) : null)
      : (localMarkIn != null ? Math.max(0, localMarkIn - windowStart) : null)
  })()
  const displayMarkOut = (() => {
    if (localDragMark?.type === 'out') {
      return Math.max(0, localDragMark.time - windowStart)
    }
    return timelineView
      ? (timelineView.markOut != null ? Math.max(0, timelineView.markOut - windowStart) : null)
      : (localMarkOut != null ? Math.max(0, localMarkOut - windowStart) : null)
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
      .map(c => {
        const hasRecordingRange = typeof c.recording_start_sec === 'number'
          && Number.isFinite(c.recording_start_sec)
          && typeof c.recording_end_sec === 'number'
          && Number.isFinite(c.recording_end_sec)
          && c.recording_end_sec > c.recording_start_sec
        const start = localRecordingAxis && !isRecordingReview && hasRecordingRange
          ? c.recording_start_sec!
          : c.start
        const end = localRecordingAxis && !isRecordingReview && hasRecordingRange
          ? c.recording_end_sec!
          : c.end
        return {
          start: Math.max(0, start - windowStart),
          end: Math.max(0, end - windowStart),
          uid: c.round_key ?? c.clip_id ?? '',
        }
      })
  }, [timelineView, clips, room?.room_id, windowStart, localRecordingAxis, isRecordingReview])

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
      padding: '6px 20px 8px',
      background: 'var(--surface-1)',
      borderTop: '1px solid var(--border-hairline)',
      display: 'flex',
      flexDirection: 'column',
      gap: 6,
      flexShrink: 0,
      zIndex: 20,
    }}>
      {multiSelectCount > 0 && (
        <div className="control-bar__meta-row">
          <span className="control-bar__hint control-bar__hint--muted">
            {t('{count} 房间 · 全局控制', { count: multiSelectCount })}
          </span>
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
        onScrubMove={onScrubMove}
        onMarkerDrag={onMarkerDrag}
        onMarkerDragEnd={onMarkerDragEnd}
        onDeleteMarker={onDeleteMarker}
        activeRefine={activeRefine}
        dvrStart={dvrStart ?? null}
        bufferedRanges={bufferedRanges}
        followLive={followLive}
        isScrubbing={isScrubbing}
        height={60}
        zoomLevel={zoomLevel}
        onZoomChange={onZoomChange}
        analysisProgress={analysisProgress}
        scanRange={scanRange}
      />

      {/* 控制与走带工具排 */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        marginTop: 2,
        flexWrap: 'wrap',
        gap: 8,
        rowGap: 6,
      }}>
        {/* 左侧：播放走带控制 + 选区入出点 */}
        <Space size={3} wrap align="center">
          <Tooltip title={t('后退 10 秒')}>
            <Button 
              type="text" size="small"
              icon={<StepBackwardOutlined />}
              onClick={onSeekBack}
              disabled={isDisabled}
              style={{ width: 28, height: 28, borderRadius: 'var(--radius-xs)' }}
            />
          </Tooltip>
          
          <Tooltip title={isPlaying ? t('暂停 (空格)') : t('播放 (空格)')}>
            <Button 
              type="text" size="small"
              icon={isPlaying ? <PauseCircleOutlined style={{ color: 'var(--brand-400)' }} /> : <PlayCircleOutlined />}
              onClick={onPlayPause}
              disabled={isDisabled}
              style={{ width: 32, height: 28, fontSize: 18, borderRadius: 'var(--radius-xs)' }}
            />
          </Tooltip>
          
          <Tooltip title={t('前进 10 秒')}>
            <Button 
              type="text" size="small"
              icon={<StepForwardOutlined />}
              onClick={onSeekFwd}
              disabled={isDisabled}
              style={{ width: 28, height: 28, borderRadius: 'var(--radius-xs)' }}
            />
          </Tooltip>

          {onPlaybackRateChange && (
            <Tooltip title={t('播放速率（Shift + , 降档 / Shift + . 升档）')}>
              <Select
                size="small"
                value={playbackRate}
                onChange={(v) => onPlaybackRateChange(v as PlaybackRate)}
                disabled={isDisabled}
                style={{ width: 68, height: 26 }}
                options={PLAYBACK_RATE_STEPS.map(r => ({ value: r, label: `${r}×` }))}
                popupMatchSelectWidth={false}
              />
            </Tooltip>
          )}

          <div style={{ width: 1, height: 16, background: 'var(--border-hairline)', margin: '0 4px' }} />

          {/* 入点与出点标记 */}
          <Tooltip title={t('标记入点 (I) · 打在播放头处')}>
            <Button 
              type="text" size="small"
              icon={<AimOutlined style={{ color: 'var(--state-success)' }} />}
              onClick={onMarkIn}
              disabled={isDisabled}
              style={{
                height: 26,
                padding: '0 8px',
                fontSize: 11,
                borderRadius: 'var(--radius-xs)',
                background: room?.mark_in != null ? 'rgba(52, 199, 89, 0.12)' : undefined,
                color: room?.mark_in != null ? 'var(--state-success)' : undefined,
                border: room?.mark_in != null ? '1px solid rgba(52, 199, 89, 0.3)' : '1px solid transparent',
              }}
            >
              {t('入点 [I]')}
            </Button>
          </Tooltip>
          {room?.mark_in != null && (
            <span style={{ fontSize: 11, fontFamily: 'var(--font-mono)', color: 'var(--state-success)', opacity: 0.9, marginRight: 2 }}>
              {formatTime(room.mark_in)}
            </span>
          )}
          
          <Tooltip title={t('标记出点 (O) · 打在播放头处')}>
            <Button 
              type="text" size="small"
              icon={<AimOutlined style={{ color: 'var(--state-error)' }} />}
              onClick={onMarkOut}
              disabled={isDisabled}
              style={{
                height: 26,
                padding: '0 8px',
                fontSize: 11,
                borderRadius: 'var(--radius-xs)',
                background: room?.mark_out != null ? 'rgba(255, 59, 48, 0.12)' : undefined,
                color: room?.mark_out != null ? 'var(--state-error)' : undefined,
                border: room?.mark_out != null ? '1px solid rgba(255, 59, 48, 0.3)' : '1px solid transparent',
              }}
            >
              {t('出点 [O]')}
            </Button>
          </Tooltip>
          {room?.mark_out != null && (
            <span style={{ fontSize: 11, fontFamily: 'var(--font-mono)', color: 'var(--state-error)', opacity: 0.9, marginRight: 2 }}>
              {formatTime(room.mark_out)}
            </span>
          )}
          {room?.mark_in != null && room?.mark_out != null && room.mark_out > room.mark_in && (
            <span
              style={{
                fontSize: 11,
                fontWeight: 600,
                fontFamily: 'var(--font-mono)',
                color: 'var(--brand-400)',
                background: 'rgba(49, 179, 174, 0.12)',
                padding: '2px 8px',
                borderRadius: 'var(--radius-xs)',
                border: '1px solid rgba(49, 179, 174, 0.25)',
                marginRight: 4,
              }}
              title={t('已选切片时长')}
            >
              {t('时长')} {formatTime(room.mark_out - room.mark_in)}
            </span>
          )}
        </Space>

        {/* 中间：三轴语义化等宽大字时间码 */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          padding: '2px 10px',
          background: 'var(--surface-0)',
          border: '1px solid var(--border-hairline)',
          borderRadius: 'var(--radius-xs)',
        }}>
          {/* 参考系 + 对齐健康度：颜色即状态 */}
          <Tooltip title={alignBadgeTip}>
            <span className={`control-bar__align-badge control-bar__align-badge--${alignBadge.tone}`}>
              {alignBadge.text}
            </span>
          </Tooltip>
          <span style={{ color: 'var(--border-hairline)' }}>|</span>
          {/* 当前播放位置 */}
          {isEditingTime ? (
            <Input
              size="small"
              autoFocus
              defaultValue={formatTime(progressSummary.previewPosition)}
              style={{
                width: 82,
                fontFamily: 'var(--font-mono)',
                fontSize: 12,
                height: 20,
                padding: '0 4px',
                borderRadius: 3,
                background: 'var(--surface-2)',
              }}
              onPressEnter={(e) => {
                const val = parseTimecode(e.currentTarget.value)
                if (val !== null && onSeek) {
                  onSeek(val)
                }
                setIsEditingTime(false)
              }}
              onBlur={(e) => {
                const val = parseTimecode(e.target.value)
                if (val !== null && onSeek) {
                  onSeek(val)
                }
                setIsEditingTime(false)
              }}
              onKeyDown={(e) => {
                if (e.key === 'Escape') setIsEditingTime(false)
              }}
            />
          ) : (
            <Tooltip title={t('点击直接输入时间码跳转（如 01:23 或 83，回车确认）')}>
              <span
                ref={timeLabelRef}
                onClick={() => !isDisabled && setIsEditingTime(true)}
                style={{
                  fontFamily: 'var(--font-mono)',
                  fontSize: 13,
                  fontWeight: 600,
                  color: 'var(--text-primary)',
                  whiteSpace: 'nowrap',
                  cursor: isDisabled ? 'default' : 'pointer',
                  padding: '1px 4px',
                  borderRadius: 3,
                }}
              >
                {formatTime(progressSummary.previewPosition)}
              </span>
            </Tooltip>
          )}
        </div>

        {/* 右侧：视图控制 + 添加切片主动作 */}
        <Space size={3} wrap align="center">
          {onGoLive && (
            <Tooltip title={
              isDisabled
                ? t('请先选择一个房间')
                : goLiveDisabled
                  ? t('当前在回看已录制文件，没有实时沿可跳')
                  : t('回到直播实时沿（时间线贴右，恢复跟播）')
            }>
              <span style={{ display: 'inline-flex' }}>
                <Button
                  type="text"
                  size="small"
                  icon={<ThunderboltOutlined style={{ color: followLive ? 'var(--state-success)' : undefined }} />}
                  onClick={onGoLive}
                  disabled={isDisabled || goLiveDisabled}
                  style={{
                    height: 28,
                    padding: '0 8px',
                    fontSize: 11,
                    borderRadius: 'var(--radius-xs)',
                    color: followLive ? 'var(--state-success)' : undefined,
                  }}
                >
                  {t('LIVE')}
                </Button>
              </span>
            </Tooltip>
          )}

          {onToggleLoop && (
            <Tooltip title={
              !hasSelection
                ? t('先用 I / O 标记入出点，才能试听选区')
                : loopPreview
                  ? t('停止试听选区')
                  : t('试听选区（循环播放入/出点）')
            }>
              <span style={{ display: 'inline-flex' }}>
                <Button
                  type={loopPreview ? 'primary' : 'text'}
                  size="small"
                  icon={<SyncOutlined spin={loopPreview} />}
                  onClick={onToggleLoop}
                  disabled={!hasSelection}
                  style={{ width: 28, height: 28, borderRadius: 'var(--radius-xs)' }}
                />
              </span>
            </Tooltip>
          )}

          {/* 核心主按钮：添加切片 */}
          <Tooltip title={addClipTip}>
            <span style={{ display: 'inline-flex' }}>
              <Button 
                type={canAddClip ? 'primary' : 'default'}
                size="small"
                icon={<ScissorOutlined />}
                onClick={onAddClip}
                disabled={!room || !canAddClip}
                style={{
                  height: 28,
                  fontSize: 12,
                  fontWeight: 600,
                  borderRadius: 'var(--radius-xs)',
                  background: canAddClip ? 'var(--brand-500)' : undefined,
                  borderColor: canAddClip ? 'var(--brand-500)' : 'var(--border-hairline)',
                  boxShadow: canAddClip ? 'var(--brand-glow)' : undefined,
                }}
              >
                {t('添加到切片')}
              </Button>
            </span>
          </Tooltip>

          <div style={{ width: 1, height: 16, background: 'var(--border-hairline)', margin: '0 4px' }} />

          {onZoomChange && (
            <>
              <Tooltip title={t('缩小时间线 (Ctrl+滚轮)')}>
                <Button
                  type="text"
                  size="small"
                  icon={<ZoomOutOutlined />}
                  onClick={() => onZoomChange(Math.max(1, zoomLevel / 1.5))}
                  disabled={zoomLevel <= 1}
                  style={{ width: 26, height: 28, borderRadius: 'var(--radius-xs)' }}
                />
              </Tooltip>
              <span style={{ fontSize: 11, fontFamily: 'var(--font-mono)', color: 'var(--text-tertiary)', minWidth: 28, textAlign: 'center', userSelect: 'none' }}>
                {zoomLevel.toFixed(1)}x
              </span>
              <Tooltip title={t('放大时间线 (Ctrl+滚轮)')}>
                <Button
                  type="text"
                  size="small"
                  icon={<ZoomInOutlined />}
                  onClick={() => onZoomChange(Math.min(20, zoomLevel * 1.5))}
                  style={{ width: 26, height: 28, borderRadius: 'var(--radius-xs)' }}
                />
              </Tooltip>
              <Tooltip title={t('重置缩放')}>
                <Button
                  type="text"
                  size="small"
                  icon={<CompressOutlined />}
                  onClick={() => onZoomChange(1)}
                  disabled={zoomLevel === 1}
                  style={{ width: 26, height: 28, borderRadius: 'var(--radius-xs)' }}
                />
              </Tooltip>
            </>
          )}
        </Space>
      </div>
    </div>
  )
}, areControlBarPropsEqual)
