import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { Row, Col, Card, Input, Button, Space, message, Empty, Modal, Tooltip, Select, Alert, Radio, Switch } from 'antd'
import { PlusOutlined, VideoCameraOutlined, SoundOutlined, MutedOutlined, SyncOutlined } from '@ant-design/icons'
import { useWebSocket } from '@/hooks/useWebSocket'
import { useAppStore } from '@/store/appStore'
import { useKeyboardShortcuts, PLAYBACK_RATE_STEPS, type PlaybackRate } from '@/hooks/useKeyboardShortcuts'
import { RoomCard } from './components/RoomCard'
import { ControlBar, type TimelineViewModel } from './components/ControlBar'
import { ClipList, type ExportProgressInfo } from './components/ClipList'
import { RefreshButton } from './components/RefreshButton'
import { ClipSegment, ContinuousAnalysisStatus, TimelineHighlightBand } from '@/types'
import { EXPORT_PRESETS, getDefaultPreset } from '@/services/exportPresets'
import { formatTime } from '@/utils/time'
import { getAligner, type PreviewAudioCaptureDiagnostics } from '@/utils/previewAudioAligner'
import {
  commonToPreview,
  commonToRecording,
  computeRecordedDurationHint,
  getAlignStatus,
  isNoDvrPreviewMode,
  isRecordingReviewMode,
  pickReferenceRoomId,
  previewToCommon,
  recordingToCommon,
  panTimelineWindowStart,
  resolveRecordingReviewSpan,
} from '@/utils/timelineCoords'

/** 贴右此容差内视为回到 Live（秒） */
const LIVE_EDGE_TOLERANCE_SEC = 1.0
/** 越过紫标左沿此容差内视为回到 Live（秒） */
const DVR_LEFT_TOLERANCE_SEC = 0.25
const TIMELINE_MAX_WINDOW = 600

function getRoomBufferedRange(roomId: string): { start: number; end: number } | null {
  const registry = (window as any).__msePlayers
  const entry = registry?.[roomId]
  const player = entry?.player ?? entry
  return player?.getBufferedRange?.() ?? null
}

/** 优先参考房，否则选中列表里第一个有缓冲的预览房 */
function resolveDvrSourceRoomId(
  referenceRoomId: string | null,
  selectedRoomId: string | null,
  selectedRoomIds: string[],
  rooms: { room_id: string; preview_enabled?: boolean; preview_phase?: string; preview_mode?: string }[],
): string | null {
  const candidates = [
    referenceRoomId,
    selectedRoomId,
    ...selectedRoomIds,
    ...rooms.map((r) => r.room_id),
  ].filter(Boolean) as string[]
  const seen = new Set<string>()
  for (const rid of candidates) {
    if (seen.has(rid)) continue
    seen.add(rid)
    const room = rooms.find((r) => r.room_id === rid)
    if (!room?.preview_enabled) continue
    if (room.preview_mode === 'recording_review' || room.preview_mode === 'degraded') continue
    // preview_phase 可能被 rooms_updated 冲掉；有缓冲即可作为 DVR 源
    const phase = room.preview_phase
    if (phase === 'error' || phase === 'idle' || phase === 'refreshing_url' || phase === 'probing') continue
    if (getRoomBufferedRange(rid)) return rid
  }
  return null
}

function getRoomMediaDuration(roomId: string): number | null {
  const video = (window as any).__msePlayers?.[roomId]?.player?.videoElement as HTMLVideoElement | undefined
  const dur = video?.duration
  return dur != null && Number.isFinite(dur) && dur > 0 ? dur : null
}

function targetsIncludeNoDvrMode(targets: Set<string>, roomList: { room_id: string; preview_mode?: string }[]): boolean {
  for (const rid of targets) {
    const mode = roomList.find(r => r.room_id === rid)?.preview_mode
    if (isNoDvrPreviewMode(mode)) return true
  }
  return false
}
import { sendRequest } from '@/utils/wsRequest'
import { scheduleBatchedToast } from '@/utils/toastBatch'
import { formatManualClipLabel } from '@/utils/clipNaming'
import { AnalysisProgress } from '@/components/AnalysisProgress'
import './Workbench.css'
/** 分析模式 */
type AnalysisMode = 'valorant_round' | 'generic'

type CaptureFailure = {
  roomId: string
  reason: string
  diagnostics?: PreviewAudioCaptureDiagnostics | null
}

function canExportForShortcut(c: ClipSegment): boolean {
  const confirmed = !c.confirm_status ||
    c.confirm_status === 'user_confirmed' ||
    c.confirm_status === 'ocr_confirmed'
  if (!confirmed) return false
  if (c.export_status === 'queued' || c.export_status === 'exporting') return false
  return true
}

function isApproximateClip(c: ClipSegment): boolean {
  if (c.clip_snapshot_id || c.mark_precision === 'exact') return false
  if (c.is_ai_highlight && c.mark_precision !== 'approximate') return false
  return (
    c.mark_precision === 'approximate' ||
    (c.mark_precision !== 'exact' &&
      (c.mark_in_wallclock == null || c.mark_out_wallclock == null))
  )
}

function clipSnapshotJobId(clipId: string): string {
  return `clip-${clipId.slice(0, 8)}`
}

/** 会停止录制的危险操作统一二次确认 */
function confirmStopRecording(title: string, content: string, onOk: () => void) {
  Modal.confirm({
    title,
    content,
    okText: '确认',
    okButtonProps: { danger: true },
    cancelText: '取消',
    onOk,
  })
}

function formatPreviewDegradationLabel(width: number, height: number, fps?: number): string {
  let label: string
  if (height === 360 || height === 480 || height === 720) {
    label = `${height}p`
  } else if (width > 0 && height > 0) {
    label = `${width}×${height}`
  } else {
    label = '较低画质'
  }
  if (fps && fps > 0) label += `@${fps}fps`
  return label
}

function formatCaptureFailureSummary(failures: CaptureFailure[]): string {
  if (failures.length === 0) return '原因未知'
  const labels: Record<string, string> = {
    no_video: '无预览播放器',
    worklet_not_loaded: '音频 Worklet 未加载',
    capture_stream_unavailable: '浏览器不支持音频捕获',
    no_audio_track: '无音轨',
    buffer_empty: 'buffer 空',
    silent: '静音或音量过低',
    timeout: '捕获超时',
    capture_exception: '捕获异常',
  }
  return failures
    .map(failure => {
      const label = labels[failure.reason] ?? failure.reason
      const sampleCount = failure.diagnostics?.sample_count
      const rms = failure.diagnostics?.rms
      const suffix = sampleCount !== undefined
        ? ` samples=${sampleCount}${typeof rms === 'number' ? ` rms=${rms.toFixed(5)}` : ''}`
        : ''
      return `${failure.roomId}:${label}${suffix}`
    })
    .join('；')
}

export default function Workbench() {
  const { isConnected, send, on } = useWebSocket()
  const rooms = useAppStore((state) => state.rooms)
  const selectedRoomId = useAppStore((state) => state.selectedRoomId)
  const connectionStatus = useAppStore((state) => state.connectionStatus)
  const appSettings = useAppStore((state) => state.appSettings)
  // L11: WebSocket 断连提示防抖——仅在断开超过 2 秒后才显示 banner，
  // 避免后端启动慢时反复 connect/disconnect 导致 banner 闪烁
  const [showDisconnectAlert, setShowDisconnectAlert] = useState(false)
  useEffect(() => {
    if (connectionStatus === 'disconnected') {
      const timer = setTimeout(() => setShowDisconnectAlert(true), 2000)
      return () => clearTimeout(timer)
    }
    setShowDisconnectAlert(false)
    return undefined
  }, [connectionStatus])
  const clips = useAppStore((state) => state.clips)
  const continuousAnalysisStatus = useAppStore((state) => state.continuousAnalysisStatus)
  const previewDegradationBanner = useAppStore((state) => state.previewDegradationBanner)
  const dismissPreviewDegradationBanner = useAppStore((state) => state.dismissPreviewDegradationBanner)
  const setSelectedRoomId = useAppStore((state) => state.setSelectedRoomId)
  const addClip = useAppStore((state) => state.addClip)
  const setClips = useAppStore((state) => state.setClips)
  const setContinuousAnalysisStatus = useAppStore((state) => state.setContinuousAnalysisStatus)
  const timelineContext = useAppStore((state) => state.timelineContext)
  const timelineInvalidated = useAppStore((state) => state.timelineInvalidated)
  const setTimelineContext = useAppStore((state) => state.setTimelineContext)
  const setTimelineInvalidated = useAppStore((state) => state.setTimelineInvalidated)
  const [loading, setLoading] = useState(false)
  const [url, setUrl] = useState('')
  const [previewClip, setPreviewClip] = useState<ClipSegment | null>(null)
  const [exportPresetId, setExportPresetId] = useState(appSettings.default_export_preset || getDefaultPreset().id)
  // 分析导出 Modal 状态（持续分析 + 同步分析导出合并）
  const [continuousModalOpen, setContinuousModalOpen] = useState(false)
  const [continuousMainRoom, setContinuousMainRoom] = useState<string | null>(null)
  // 精修状态
  const [refiningClipId, setRefiningClipId] = useState<string | null>(null)
  // 本地拖拽 marker 的即时显示值（拖拽中覆盖 room mark，松手清除）
  const [localDragMark, setLocalDragMark] = useState<{ type: 'in' | 'out'; time: number } | null>(null)

  const [continuousPresetId, setContinuousPresetId] = useState(appSettings.default_export_preset || getDefaultPreset().id)
  const [analysisIsContinuous, setAnalysisIsContinuous] = useState(false)
  const [continuousSubmitting, setContinuousSubmitting] = useState(false)
  // 运行中的持续分析状态
  const [continuousAnalyzing, setContinuousAnalyzing] = useState(false)
  const [continuousRoomId, setContinuousRoomId] = useState<string | null>(null)
  const [continuousTargetRoomIds, setContinuousTargetRoomIds] = useState<string[]>([])
  const [analysisGameType, setAnalysisGameType] = useState<AnalysisMode>('generic')
  const isValorantRoundCutting = analysisGameType === 'valorant_round'
    const [valorantProfile, setValorantProfile] = useState<'pov' | 'broadcast'>('pov')
  const continuousActiveRoomRef = useRef<string | null>(null)
  // 同步导出模式标记（response 监听器据此预创建 clips 关联 job_id）
  const isSyncExportModeRef = useRef(false)
  const syncMainRoomRef = useRef<string | null>(null)
  const syncTargetRoomIdsRef = useRef<string[]>([])
  /** 乐观入队的 job_id，用于导出提交失败时精确回滚 */
  const pendingExportJobIdsRef = useRef<Set<string>>(new Set())
  const [loopPreview, setLoopPreview] = useState(false)
  const [playbackRate, setPlaybackRate] = useState<PlaybackRate>(1)
  const [timelineZoom, setTimelineZoom] = useState(1)
  const [commonMarkIn, setCommonMarkIn] = useState<number | null>(null)
  const [commonMarkOut, setCommonMarkOut] = useState<number | null>(null)
  const [waveformPeaks, setWaveformPeaks] = useState<number[]>([])
  const [allMuted, setAllMuted] = useState(false)
  const [sortBy, setSortBy] = useState<string>('default')
  const [aligning, setAligning] = useState(false)
  const [exportProgressMap, setExportProgressMap] = useState<Record<string, ExportProgressInfo>>({})
  const aligningRoomIdsRef = useRef<Set<string>>(new Set())
  const alignButtonRef = useRef<HTMLButtonElement | null>(null)
  const loopRafRef = useRef<number | null>(null)
  const loopBoundsRef = useRef<{ in: number; out: number; common: boolean } | null>(null)
  // 长按刷新：由 RefreshButton 组件内部管理粒子动效
  const [fullscreenRoomId, setFullscreenRoomId] = useState<string | null>(null)
  // 两级放大：第一级为“区域放大”（填满左侧面板），第二级为“全屏”（fixed 覆盖视口）
  const [expandedRoomId, setExpandedRoomId] = useState<string | null>(null)
  const [selectedRoomIds, setSelectedRoomIds] = useState<Set<string>>(new Set())
  const [clipSelectedIndices, setClipSelectedIndices] = useState<Set<number>>(new Set())
  const selectedRoomIdsRef = useRef<Set<string>>(new Set())
  const [lastClickedIndex, setLastClickedIndex] = useState<number | null>(null)
  const pendingRoomSavesRef = useRef(0)
  const pendingAddUrlRef = useRef('')
  // 多 URL 添加时追踪待完成的响应数量，全部到达后才关闭 loading（M17）
  const pendingAddCountRef = useRef(0)
  // 预览播放位置（从 MSE player 定期读取，驱动时间线播放头）
  const [previewPositions, setPreviewPositions] = useState<Record<string, number>>({})
  // 缓存上次 previewPositions 快照，仅在 currentTime 真正变化时 setState，避免无差别重渲染
  const lastPreviewPositionsRef = useRef<Record<string, number>>({})
  /** 用户拖拽/步进后的 UI 播放头；超出 MSE 缓冲时仍保持，避免被拽回直播沿 */
  const scrubOverrideRef = useRef<Record<string, number>>({})
  /** 跟随直播沿：窗口贴右、播放头可顶在最右；用户 scrub 后关闭，点「直播」恢复 */
  const [timelineFollowLive, setTimelineFollowLive] = useState(true)
  /** 拖拽 scrub 中：冻结 windowStart，避免窗跟着播放头平移导致圆点「拖不动」 */
  const [timelineScrubbing, setTimelineScrubbing] = useState(false)
  const timelineScrubbingRef = useRef(false)
  const [frozenWindowStart, setFrozenWindowStart] = useState<number | null>(null)
  const lastWindowStartRef = useRef(0)
  /** 时间线内容右沿（只增不减）：回看时不得随播放头收缩，否则光标会「钉」在缩短后的最右 */
  const lastContentEndRef = useRef(1)
  const contentEdgeRoomRef = useRef<string | null>(null)
  useEffect(() => {
    const id = setInterval(() => {
      // scrub 中跳过：光标走 Timeline 本地 dragTime，避免父级 200ms 重渲染抢帧
      if (timelineScrubbingRef.current) return
      const registry = (window as any).__msePlayers
      if (!registry) return
      const next: Record<string, number> = { ...lastPreviewPositionsRef.current }
      let changed = false
      for (const rid of Object.keys(registry)) {
        const entry = registry[rid]
        const t = entry?.player?.videoElement?.currentTime
        if (typeof t !== 'number' || t < 0) continue
        const scrub = scrubOverrideRef.current[rid]
        if (scrub != null) {
          if (Math.abs(t - scrub) < 0.35) {
            delete scrubOverrideRef.current[rid]
            next[rid] = t
            changed = true
          } else if (next[rid] !== scrub) {
            next[rid] = scrub
            changed = true
          }
          continue
        }
        const prev = lastPreviewPositionsRef.current[rid]
        if (prev === undefined || Math.abs(t - prev) > 0.01) {
          next[rid] = t
          changed = true
        }
      }
      if (changed) {
        lastPreviewPositionsRef.current = next
        setPreviewPositions(next)
      }
    }, 200)  // S4: 从 500ms 提升到 200ms，时间线播放头更平滑
    return () => clearInterval(id)
  }, [])

  const [timelineTick, setTimelineTick] = useState(0)
  useEffect(() => {
    const recording = rooms.some(r => selectedRoomIds.has(r.room_id) && r.is_recording)
    if (!recording) return
    const id = setInterval(() => setTimelineTick(t => t + 1), 1000)
    return () => clearInterval(id)
  }, [rooms, selectedRoomIds])

  const alignStatus = getAlignStatus(timelineContext, timelineInvalidated)
  const commonMode = alignStatus === 'ready' && !!timelineContext

  const referenceRoomId = useMemo(
    () => pickReferenceRoomId(timelineContext, selectedRoomIds, selectedRoomId),
    [timelineContext, selectedRoomIds, selectedRoomId],
  )

  useEffect(() => {
    const unsubReady = on('timeline_ready', (data: { timeline?: typeof timelineContext }) => {
      if (data?.timeline) {
        setTimelineContext(data.timeline)
        setTimelineInvalidated(false)
        setCommonMarkIn(null)
        setCommonMarkOut(null)
      }
    })
    const unsubInvalid = on('timeline_invalidated', () => {
      setTimelineContext(null)
      setTimelineInvalidated(true)
      setCommonMarkIn(null)
      setCommonMarkOut(null)
      setWaveformPeaks([])
      const affectedCount = useAppStore.getState().clips.filter(c => c.timeline_id).length
      message.warning(
        `公共轴已失效${affectedCount > 0 ? `，${affectedCount} 个多房间切片需重新对齐后导出` : '，请重新对齐'}`,
        5,
      )
    })
    return () => {
      unsubReady()
      unsubInvalid()
    }
  }, [on, setTimelineContext, setTimelineInvalidated])

  useEffect(() => {
    if (!timelineContext?.timeline_id || !referenceRoomId) return
    const room = rooms.find(r => r.room_id === referenceRoomId)
    if (room?.mark_in != null) {
      setCommonMarkIn(previewToCommon(timelineContext, referenceRoomId, room.mark_in))
    }
    if (room?.mark_out != null) {
      setCommonMarkOut(previewToCommon(timelineContext, referenceRoomId, room.mark_out))
    }
  }, [timelineContext?.timeline_id, referenceRoomId, rooms])

  const timelineHighlights = useMemo((): TimelineHighlightBand[] => {
    const out: TimelineHighlightBand[] = []
    for (const c of clips) {
      if (!c.is_ai_highlight) continue
      let start = c.common_start ?? c.start
      let end = c.common_end ?? c.end
      if (commonMode && timelineContext && c.room_id && c.common_start == null) {
        try {
          start = previewToCommon(timelineContext, c.room_id, c.start)
          end = previewToCommon(timelineContext, c.room_id, c.end)
        } catch {
          continue
        }
      }
      out.push({
        id: c.clip_id || `${c.room_id}-${start}`,
        start,
        end,
        reason: c.highlight_reason,
        score: c.highlight_score,
        label: c.label,
      })
    }
    return out
  }, [clips, commonMode, timelineContext])

  const timelineView = useMemo((): TimelineViewModel | null => {
    if (!commonMode || !timelineContext || !referenceRoomId) return null
    // 换参考房时重置右沿，避免串房
    if (contentEdgeRoomRef.current !== referenceRoomId) {
      contentEdgeRoomRef.current = referenceRoomId
      lastContentEndRef.current = 1
    }
    const refRoom = rooms.find(r => r.room_id === referenceRoomId)
    const previewT = previewPositions[referenceRoomId] ?? 0
    const curCommon = previewToCommon(timelineContext, referenceRoomId, previewT)
    const isRecordingReview = isRecordingReviewMode(refRoom?.preview_mode)
  // 仅用与播放头同轴的 common 时间撑开窗口；recording_review 额外允许录制全长撑右沿
    let elapsed = Math.max(commonMarkOut ?? 0, commonMarkIn ?? 0, curCommon)
    if (isRecordingReview) {
      const recordedHint = computeRecordedDurationHint(refRoom, continuousAnalysisStatus?.recorded_duration)
      const reviewSpan = resolveRecordingReviewSpan(
        previewT,
        recordedHint,
        getRoomMediaDuration(referenceRoomId),
        commonMarkIn,
        commonMarkOut,
      )
      elapsed = Math.max(elapsed, reviewSpan)
    }
    const refineClip = refiningClipId
      ? clips.find(c => c.round_key === refiningClipId || c.clip_id === refiningClipId)
      : null
    let refineStart = commonMarkIn
    let refineEnd = commonMarkOut
    if ((refineStart == null || refineEnd == null) && refineClip) {
      refineStart = refineClip.common_start ?? refineClip.start
      refineEnd = refineClip.common_end ?? refineClip.end
    }
    if (refineEnd != null && refineEnd > elapsed) {
      elapsed = refineEnd
    }
    if (refineStart != null && refineStart > elapsed) {
      elapsed = refineStart
    }
    for (const c of clips) {
      const end = c.common_end ?? c.end
      if (end > elapsed) elapsed = end
    }
    // 右沿只增不减：回看时 preview/playhead 变小不得缩短整条时间线
    const rawEnd = Math.max(elapsed, previewT, curCommon, 0)
    const contentEnd = Math.max(lastContentEndRef.current, rawEnd, 1)
    lastContentEndRef.current = contentEnd
    let ws = 0
    let dur = contentEnd
    if (refineStart != null && refineEnd != null && refineEnd > refineStart) {
      const mid = (refineStart + refineEnd) / 2
      const half = Math.min(TIMELINE_MAX_WINDOW, Math.max(30, (refineEnd - refineStart) * 4)) / 2
      ws = Math.max(0, mid - half)
      dur = Math.max(contentEnd, ws + half * 2, 1)
    } else if (contentEnd > TIMELINE_MAX_WINDOW) {
      dur = contentEnd
      if (timelineFollowLive && !timelineScrubbing) {
        // 跟随直播：窗口贴最新 10 分钟，播放头可顶右
        ws = contentEnd - TIMELINE_MAX_WINDOW
      } else if (timelineScrubbing && frozenWindowStart != null) {
        // 拖拽中冻结坐标系
        ws = frozenWindowStart
      } else {
        // 松手后仅当播放头越出可见窗时平移（不钉 15%）
        const playhead = Math.max(0, curCommon)
        ws = panTimelineWindowStart(
          playhead,
          contentEnd,
          TIMELINE_MAX_WINDOW,
          frozenWindowStart ?? lastWindowStartRef.current,
        )
      }
    }
    lastWindowStartRef.current = ws
    const markIn = commonMarkIn ?? (refRoom?.mark_in != null
      ? previewToCommon(timelineContext, referenceRoomId, refRoom.mark_in)
      : null)
    const markOut = commonMarkOut ?? (refRoom?.mark_out != null
      ? previewToCommon(timelineContext, referenceRoomId, refRoom.mark_out)
      : null)
    const clipBlocks = clips
      .filter(c => !c.is_ai_highlight && c.end > c.start)
      .map(c => {
        let start = c.common_start ?? c.start
        let end = c.common_end ?? c.end
        if (c.room_id && c.common_start == null) {
          try {
            start = previewToCommon(timelineContext, c.room_id, c.start)
            end = previewToCommon(timelineContext, c.room_id, c.end)
          } catch {
            return null
          }
        }
        return { start, end }
      })
      .filter((c): c is { start: number; end: number } => c != null)
    // Live：播放头钉在内容末端（视觉最右）；非 Live：跟真实/scrub 位置
    const liveCur = (timelineFollowLive && !timelineScrubbing)
      ? contentEnd
      : Math.max(0, curCommon)
    return {
      duration: dur,
      currentTime: liveCur,
      windowStart: ws,
      markIn,
      markOut,
      clips: clipBlocks,
      highlights: timelineHighlights,
      waveformPeaks,
      contentEnd,
    }
  }, [
    commonMode, timelineContext, referenceRoomId, rooms, previewPositions,
    commonMarkIn, commonMarkOut, clips, timelineHighlights, timelineTick,
    refiningClipId, waveformPeaks,     timelineFollowLive, timelineScrubbing, frozenWindowStart,
    continuousAnalysisStatus?.recorded_duration,
  ])

  // 紫标 = MSE 缓冲左沿（与 timelineView / contentEnd 同轴）；recording_review / degraded 无紫标
  const dvrStart = useMemo((): number | null => {
    const rid = resolveDvrSourceRoomId(
      referenceRoomId,
      selectedRoomId,
      selectedRoomIds,
      rooms,
    )
    if (!rid) return null
    const room = rooms.find(r => r.room_id === rid)
    if (isNoDvrPreviewMode(room?.preview_mode)) return null
    if (!room?.preview_enabled) return null
    const phase = room.preview_phase
    if (phase === 'error' || phase === 'idle' || phase === 'refreshing_url' || phase === 'probing') {
      return null
    }
    const buf = getRoomBufferedRange(rid)
    if (!buf) return null
    const bufStart = buf.start
    if (commonMode && timelineContext?.room_snapshots[rid]) {
      try {
        return previewToCommon(timelineContext, rid, bufStart)
      } catch {
        // 对齐快照瞬时不可用时回退 preview 轴，避免紫标整段消失
        return bufStart
      }
    }
    return bufStart
  }, [referenceRoomId, selectedRoomId, selectedRoomIds, rooms, commonMode, timelineContext, previewPositions, timelineTick])

  // recording_review / degraded：强制退出 followLive
  useEffect(() => {
    const rid = referenceRoomId || selectedRoomId
    if (!rid) return
    const room = rooms.find(r => r.room_id === rid)
    if (isNoDvrPreviewMode(room?.preview_mode)) {
      setTimelineFollowLive(false)
    }
  }, [rooms, referenceRoomId, selectedRoomId])

  // Escape 键退出放大（capture 阶段拦截，避免触发其他快捷键如 mark_in/out）
  useEffect(() => {
    if (expandedRoomId === null && fullscreenRoomId === null) return
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        e.stopPropagation()
        if (fullscreenRoomId !== null) {
          setFullscreenRoomId(null)
        } else {
          setExpandedRoomId(null)
        }
      }
    }
    window.addEventListener('keydown', handleKeyDown, true)
    return () => window.removeEventListener('keydown', handleKeyDown, true)
  }, [expandedRoomId, fullscreenRoomId])

  // 程序切到后台后再切回前台时，恢复所有 MSE player 的播放
  useEffect(() => {
    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        const registry = (window as any).__msePlayers
        if (!registry) return
        for (const rid of Object.keys(registry)) {
          const player = registry[rid]?.player
          if (player && typeof player.resumePlayback === 'function') {
            player.resumePlayback()
          }
        }
      }
    }
    document.addEventListener('visibilitychange', handleVisibilityChange)
    return () => document.removeEventListener('visibilitychange', handleVisibilityChange)
  }, [])

  // Sync store's selectedRoomId with multi-select state
  useEffect(() => {
    selectedRoomIdsRef.current = selectedRoomIds
    setSelectedRoomId(selectedRoomIds.size === 1 ? [...selectedRoomIds][0] : null)
  }, [selectedRoomIds, setSelectedRoomId])

  // 反向同步：当 store 的 selectedRoomId 变化且不在多选集合中时，
  // 将其同步到 selectedRoomIds，确保 Workbench 选中该房间。
  // 注意：不依赖 selectedRoomIds，改用 ref 读取，避免取消勾选时 effect 误触发导致循环。
  useEffect(() => {
    if (selectedRoomId && !selectedRoomIdsRef.current.has(selectedRoomId)) {
      setSelectedRoomIds(new Set([selectedRoomId]))
    }
  }, [selectedRoomId])

  // 获取房间列表 + 查询当前是否有正在运行的持续分析任务
  useEffect(() => {
    if (isConnected) {
      send('get_rooms', {})
      send('get_continuous_analysis_status', {})
    }
  }, [isConnected, send])

  // 轮询补偿：每 5s 查询持续分析状态，防止后端广播遗漏导致 UI 冻结
  useEffect(() => {
    if (!isConnected || !continuousAnalyzing) return
    const timer = setInterval(() => {
      send('get_continuous_analysis_status', {})
    }, 5000)
    return () => clearInterval(timer)
  }, [isConnected, continuousAnalyzing, send])

  // 监听后端事件（仅用于界面反馈，状态由 useWebSocket 统一写入 store）
  useEffect(() => {
    const unsubs: (() => void)[] = []
    const applyContinuousStatus = (data: ContinuousAnalysisStatus) => {
      const previous = useAppStore.getState().continuousAnalysisStatus
      const merged = previous?.room_id === data?.room_id
        ? { ...previous, ...data }
        : data
      setContinuousAnalysisStatus(merged)
      if (data?.running && data?.room_id) {
        setContinuousAnalyzing(true)
        setContinuousRoomId(data.room_id)
        continuousActiveRoomRef.current = data.room_id
        if (Array.isArray(data.target_room_ids)) {
          setContinuousTargetRoomIds(data.target_room_ids)
        }
      } else {
        setContinuousAnalyzing(false)
        setContinuousRoomId(null)
        continuousActiveRoomRef.current = null
        setContinuousTargetRoomIds([])
      }
    }
    unsubs.push(on('continuous_analysis_status', applyContinuousStatus))
    unsubs.push(on('get_continuous_analysis_status_response', applyContinuousStatus))
    unsubs.push(on('room_connect_finished', (data: { room_id: string; success: boolean; error: string }) => {
      if (!data.success) {
        message.error(`连接失败：${data.error || '未知错误'}`)
      }
    }))
    unsubs.push(on('recording_started', (data: { room_id: string; success: boolean; error: string }) => {
      if (!data.success) {
        message.error(`录制启动失败：${data.error || '未知错误'}`)
      }
    }))
    // W8.2：磁盘满停录 —— 强制 toast.error（聚焦窗口也显示）
    unsubs.push(on('recording_stopped', (data: { room_id?: string; reason?: string; message?: string }) => {
      if (data?.reason === 'disk_full' && data?.message) {
        message.error({ content: data.message, duration: 0 })
      }
    }))
    // 仅 accepted=false / success=false+error 时回滚乐观 is_connecting；
    // async 受理成功不得 toast「连接成功」，真正结果等 room_connect_finished
    unsubs.push(on('connect_room_response', (data: {
      success?: boolean
      accepted?: boolean
      async?: boolean
      error?: string
      room_id?: string
    }) => {
      const rejected = data?.accepted === false || (data?.success === false && !!data?.error)
      if (data?.room_id && rejected) {
        useAppStore.getState().updateRoom(data.room_id, { is_connecting: false })
        if (data.error) {
          message.error(`连接失败：${data.error}`)
        }
      }
    }))
    // 单房间录制响应（后端返回 start_recording_response，不是 recording_started）
    unsubs.push(on('start_recording_response', (data: { success?: boolean; error?: string; room_id?: string }) => {
      if (data?.room_id) {
        useAppStore.getState().updateRoom(data.room_id, { is_recording_starting: false })
      }
      if (data?.success) {
        message.success('录制已开始')
      } else {
        message.error(`录制启动失败：${data?.error || '未知错误'}`)
      }
    }))
    // 停止录制响应
    unsubs.push(on('stop_recording_response', (data: { success?: boolean; error?: string }) => {
      if (data?.success) {
        message.success('录制已停止')
      } else {
        message.error(`停止录制失败：${data?.error || '未知错误'}`)
      }
    }))

    // MSE 断流：按 reason 分层——仅 offline 停录并提示下线，网络类错误只提示预览异常
    unsubs.push(on('mse_error', (data: { room_id?: string; error?: string; reason?: string }) => {
      if (!data?.room_id) return
      const reason = data.reason || 'unknown'
      const r = useAppStore.getState().rooms.find(x => x.room_id === data.room_id)
      if (reason === 'offline') {
        if (r?.is_recording) send('stop_recording', { room_id: data.room_id })
        message.warning('主播已下线，录制已保存，可回看录制内容', 5)
        return
      }
      message.warning(data.error || '预览异常，请检查网络或重试预览', 5)
    }))

    return () => unsubs.forEach(u => u())
  }, [on, send])

  // 添加/删除房间成功后，发送 save_rooms 持久化房间列表
  useEffect(() => {
    const unsubs: (() => void)[] = []

    unsubs.push(on('rooms_updated', () => {
      if (pendingRoomSavesRef.current > 0) {
        pendingRoomSavesRef.current -= 1
        const currentRooms = useAppStore.getState().rooms
        send('save_rooms', { rooms: currentRooms })
      }
    }))

    unsubs.push(on('add_room_response', (data: { success?: boolean; error?: string; room_id?: string }) => {
      // 多 URL 添加：用计数器追踪待完成数量，全部响应到达后才关闭 loading（M17）
      if (pendingAddCountRef.current > 0) {
        pendingAddCountRef.current -= 1
        if (pendingAddCountRef.current === 0) {
          setLoading(false)
        }
      } else {
        // 兜底：单 URL 或异常情况下直接关闭 loading
        setLoading(false)
      }
      if (data?.success === false || data?.error) {
        // 添加失败：显示错误提示，保留输入框内容供重试
        if (pendingRoomSavesRef.current > 0) {
          pendingRoomSavesRef.current -= 1
        }
        message.error(data.error || '添加房间失败')
        // 恢复输入框为待添加的 URL（因为可能已被清空）
        if (pendingAddUrlRef.current) {
          setUrl(pendingAddUrlRef.current)
          pendingAddUrlRef.current = ''
        }
      } else {
        // 添加成功：清空输入框
        pendingAddUrlRef.current = ''
        setUrl('')
      }
    }))

    unsubs.push(on('remove_room_response', (data: { error?: string }) => {
      if (data?.error && pendingRoomSavesRef.current > 0) {
        pendingRoomSavesRef.current -= 1
      }
    }))

    return () => unsubs.forEach(u => u())
  }, [on, send])

  // 监听导出完成事件，更新导出队列状态
  useEffect(() => {
    const unsubs: (() => void)[] = []
    unsubs.push(on('export_progress', (data: any) => {
      if (data?.job_id && typeof data.percent === 'number') {
        const progressStore = useAppStore.getState()
        progressStore.setClips(progressStore.clips.map(c => c.job_id === data.job_id ? { ...c, export_status: 'exporting' as const } : c))
        setExportProgressMap(prev => ({
          ...prev,
          [data.job_id]: {
            percent: data.percent,
            elapsed: data.elapsed ?? 0,
            total: data.total ?? 0,
          },
        }))
      }
    }))
    unsubs.push(on('clip_completed', (data: any) => {
      if (data?.job_id) {
        const store = useAppStore.getState()
        const updatedClips = store.clips.map(c =>
          c.job_id === data.job_id
            ? { ...c, exported: true, outputPath: data.output_path, export_status: 'completed' as const, export_error: undefined }
            : c
        )
        store.setClips(updatedClips)
        setExportProgressMap(prev => {
          if (!prev[data.job_id]) return prev
          const next = { ...prev }
          delete next[data.job_id]
          return next
        })
        if (!document.hasFocus()) {
          message.success('切片导出完成')
        }
      }
    }))
    // 导出失败/取消：后端通过 clip_failed 通知
    unsubs.push(on('clip_failed', (data: { room_id?: string; job_id?: string; error?: string }) => {
      const isCancelled = data.error === '导出已取消'
      if (!isCancelled && data.error) {
        message.error({ content: `导出失败：${data.error}`, duration: 5 })
      } else if (!isCancelled && !data.error) {
        message.error({ content: '导出失败：未知错误。请点击切片列表中的「打开输出文件夹」排查或重试。', duration: 5 })
      }
      if (data?.job_id) {
        const jid = data.job_id
        setExportProgressMap(prev => {
          if (!prev[jid]) return prev
          const next = { ...prev }
          delete next[jid]
          return next
        })
        const store = useAppStore.getState()
        const updatedClips = store.clips.map(c =>
          c.job_id === jid
            ? { ...c, export_status: 'failed' as const, export_error: data.error || '导出失败' }
            : c
        )
        store.setClips(updatedClips)
      }
    }))
    // export_clip / export_clip_by_id 提交响应：失败时提示并回滚 queued
    const handleExportSubmitResponse = (data: { success?: boolean; error?: string; job_id?: string }) => {
      const failed = data?.success === false || (Boolean(data?.error) && data?.success !== true)
      if (!failed) {
        if (data?.job_id) pendingExportJobIdsRef.current.delete(data.job_id)
        return
      }
      message.error(`导出失败：${data.error || '未知错误'}`)
      const rollbackIds = new Set<string>()
      if (data?.job_id) {
        rollbackIds.add(data.job_id)
        pendingExportJobIdsRef.current.delete(data.job_id)
      } else {
        for (const id of pendingExportJobIdsRef.current) rollbackIds.add(id)
        pendingExportJobIdsRef.current.clear()
      }
      if (rollbackIds.size > 0) {
        setExportProgressMap(prev => {
          let next = prev
          for (const id of rollbackIds) {
            if (next[id]) {
              if (next === prev) next = { ...prev }
              delete next[id]
            }
          }
          return next
        })
        const store = useAppStore.getState()
        store.setClips(store.clips.map(c =>
          c.job_id && rollbackIds.has(c.job_id)
            ? { ...c, export_status: 'failed' as const, export_error: data.error || '导出失败' }
            : c
        ))
      }
    }
    unsubs.push(on('export_clip_response', handleExportSubmitResponse))
    unsubs.push(on('export_clip_by_id_response', handleExportSubmitResponse))
    // cancel_export 响应：后端确认取消后置 cancelled，失败时提示用户
    unsubs.push(on('cancel_export_response', (data: { success?: boolean; error?: string; job_id?: string }) => {
      if (data?.success === false) {
        message.warning(`取消导出失败：${data.error || '任务可能已结束'}`)
      }
    }))
    return () => unsubs.forEach(u => u())
  }, [on])

  // 添加房间
  const handleAddRoom = async () => {
    console.log('[Workbench] 用户操作: 添加房间, url:', url);
    if (loading) return
    const trimmedUrl = url.trim()
    if (!trimmedUrl) {
      message.warning('请输入直播间链接')
      return
    }

    // Support multi-line paste: split by newlines and add each URL
    const urls = trimmedUrl.split('\n').map(u => u.trim()).filter(Boolean)

    if (urls.length > 1) {
      message.info(`正在添加 ${urls.length} 个房间...`)
    }

    setLoading(true)
    // 多 URL 添加：计数器记录待完成响应数量，全部到达后才关闭 loading（M17）
    pendingAddCountRef.current = urls.length
    urls.forEach((u, i) => {
      pendingRoomSavesRef.current += 1
      if (i === urls.length - 1) {
        pendingAddUrlRef.current = u
      }
      send('add_room', { url: u })
    })
    // 不在此处清空输入框或 setLoading(false)
    // 改为在 add_room_response 回调中处理
  }

  // 连接房间（乐观更新 is_connecting，避免等后端广播才出现 loading）
  const handleConnect = useCallback((roomId: string) => {
    console.log('[Workbench] 用户操作: 连接房间, roomId:', roomId);
    useAppStore.getState().updateRoom(roomId, { is_connecting: true, last_error: '' })
    send('connect_room', { room_id: roomId })
  }, [send])

  // 断开房间：先停止录制/预览/分析，再断开连接（录制中须二次确认）
  const handleDisconnect = useCallback((roomId: string) => {
    console.log('[Workbench] 用户操作: 断开房间连接, roomId:', roomId);
    const doDisconnect = () => {
      const room = useAppStore.getState().rooms.find(r => r.room_id === roomId)
      if (room?.is_recording) {
        send('stop_recording', { room_id: roomId })
      }
      if (room?.preview_enabled) {
        send('enable_preview', { room_id: roomId, enabled: false, mode: 'mse' })
      }
      // 如果该房间正在被持续分析，停止它
      const continuousStatus = useAppStore.getState().continuousAnalysisStatus
      if (continuousStatus?.running && continuousStatus.room_id === roomId) {
        send('stop_continuous_analysis', { main_room_id: roomId })
      }
      // 最后断开连接
      send('disconnect_room', { room_id: roomId })
    }
    const room = useAppStore.getState().rooms.find(r => r.room_id === roomId)
    if (room?.is_recording) {
      Modal.confirm({
        title: '确认断开',
        content: `断开将停止录制「${room.streamer_name || '未知主播'}」`,
        okText: '确认',
        okButtonProps: { danger: true },
        cancelText: '取消',
        onOk: doDisconnect,
      })
      return
    }
    doDisconnect()
  }, [send])

  // 切换静音（乐观写 store，与 VideoPreview 一致，避免 stale rooms_updated 打回图标）
  const handleToggleMute = useCallback((roomId: string) => {
    const room = useAppStore.getState().rooms.find((r) => r.room_id === roomId)
    if (!room) return
    const newMuted = !room.preview_muted
    console.log('[Workbench] 用户操作: 切换静音状态, roomId:', roomId, 'newMuted:', newMuted);
    useAppStore.getState().updateRoom(roomId, { preview_muted: newMuted })
    // 取消静音时显式 resume AudioContext，确保 Web Audio 路由有输出
    if (!newMuted) {
      const ctx = getAligner().getContextSync()
      if (ctx.state === 'suspended') {
        ctx.resume().catch((e) => {
          console.warn('[Workbench] Failed to resume AudioContext on unmute:', e)
        })
      }
    }
    send('set_preview_muted', { room_id: roomId, muted: newMuted })
  }, [send])

  // 开始录制（乐观显示 loading，避免等 FFmpeg 启动完成才有反馈）
  const handleStartRecord = useCallback((roomId: string) => {
    console.log('[Workbench] 用户操作: 开始录制, roomId:', roomId);
    useAppStore.getState().updateRoom(roomId, { is_recording_starting: true, last_error: '' })
    send('start_recording', { room_id: roomId })
  }, [send])

  // 停止录制
  const handleStopRecord = useCallback((roomId: string) => {
    console.log('[Workbench] 用户操作: 停止录制, roomId:', roomId);
    const ca = useAppStore.getState().continuousAnalysisStatus
    const analyzingThisRoom = Boolean(
      ca?.running && (ca.room_id === roomId || (ca.target_room_ids || []).includes(roomId)),
    )
    send('stop_recording', { room_id: roomId })
    if (analyzingThisRoom) {
      message.info('录制已停止。请稍候，持续分析正在收尾确认回合并导出，勿立刻关掉分析', 6)
    }
  }, [send])

  // 启用/停止预览（Electron 模式：后端 FFmpeg 抓帧推送）
  const handleTogglePreview = useCallback((roomId: string, enabled: boolean) => {
    console.log('[Workbench] 用户操作: 切换预览状态, roomId:', roomId, 'enabled:', enabled);
    if (enabled) {
      const activePreviews = rooms.filter(r => r.preview_enabled && r.room_id !== roomId).length
      if (activePreviews >= 4) {
        message.warning('最多 4 路同时预览，请先关闭一路')
        return
      }
      if (activePreviews >= 3) {
        message.info('多路预览已自动降画质以保证流畅', 3)
      }
    }
    send('enable_preview', { room_id: roomId, enabled, mode: 'mse' })
  }, [send, rooms])

  // 放大预览（两级：第一次进入左侧区域放大，第二次进入真正全屏）
  const handleFullscreen = useCallback((roomId: string) => {
    if (fullscreenRoomId === roomId) {
      setFullscreenRoomId(null)
      setExpandedRoomId(roomId)
      return
    }
    if (expandedRoomId === roomId) {
      setFullscreenRoomId(roomId)
      return
    }
    setExpandedRoomId(roomId)
    setFullscreenRoomId(null)
  }, [expandedRoomId, fullscreenRoomId])

  const handleCollapse = useCallback((roomId: string) => {
    if (fullscreenRoomId === roomId) {
      setFullscreenRoomId(null)
      return
    }
    if (expandedRoomId === roomId) {
      setExpandedRoomId(null)
    }
  }, [expandedRoomId, fullscreenRoomId])

  const handleExitFullscreen = useCallback((roomId: string) => {
    if (fullscreenRoomId === roomId) {
      setFullscreenRoomId(null)
      setExpandedRoomId(roomId)
    }
  }, [fullscreenRoomId])

  // 删除房间
  const handleRemove = useCallback((roomId: string) => {
    console.log('[Workbench] 用户操作: 删除房间, roomId:', roomId);
    // 项目记忆硬约束：“Fullscreen preview must be exited before deleting a
    // room to avoid crashes”。若该房间正处于全屏预览，先退出全屏再删除，
    // 避免 VideoPreview 组件实例继续渲染已删除房间的 MSE streamer 导致崩溃。
    if (fullscreenRoomId === roomId) {
      setFullscreenRoomId(null)
    }
    if (expandedRoomId === roomId) {
      setExpandedRoomId(null)
    }
    pendingRoomSavesRef.current += 1
    send('remove_room', { room_id: roomId })
    setSelectedRoomIds(prev => {
      const next = new Set(prev)
      next.delete(roomId)
      return next
    })
  }, [send, fullscreenRoomId, expandedRoomId])

  // 选择房间（支持 Ctrl/Shift 多选）
  const handleSelect = useCallback((roomId: string, e: React.MouseEvent) => {
    console.log('[Workbench] 用户操作: 选择/切换选中状态, roomId:', roomId);
    const currentRooms = useAppStore.getState().rooms
    const roomIndex = currentRooms.findIndex(r => r.room_id === roomId)

    if (e.ctrlKey || e.metaKey) {
      // Ctrl+Click: toggle selection
      setSelectedRoomIds(prev => {
        const next = new Set(prev)
        if (next.has(roomId)) {
          next.delete(roomId)
        } else {
          next.add(roomId)
        }
        return next
      })
      setLastClickedIndex(roomIndex)
    } else if (e.shiftKey && lastClickedIndex !== null) {
      // Shift+Click: range selection
      const start = Math.min(lastClickedIndex, roomIndex)
      const end = Math.max(lastClickedIndex, roomIndex)
      const rangeIds = currentRooms.slice(start, end + 1).map(r => r.room_id)
      setSelectedRoomIds(new Set(rangeIds))
      setLastClickedIndex(roomIndex)
    } else {
      // Normal click: single selection
      setSelectedRoomIds(new Set([roomId]))
      setLastClickedIndex(roomIndex)
    }
  }, [lastClickedIndex])

  // Checkbox 多选切换（无需 Ctrl 键，点击即切换选中/取消）
  const handleToggleMultiSelect = useCallback((roomId: string, e: React.MouseEvent) => {
    const currentRooms = useAppStore.getState().rooms
    const roomIndex = currentRooms.findIndex(r => r.room_id === roomId)

    if (e.shiftKey && lastClickedIndex !== null) {
      // Shift+Click on checkbox: range selection
      const start = Math.min(lastClickedIndex, roomIndex)
      const end = Math.max(lastClickedIndex, roomIndex)
      const rangeIds = currentRooms.slice(start, end + 1).map(r => r.room_id)
      setSelectedRoomIds(prev => {
        const next = new Set(prev)
        rangeIds.forEach(id => next.add(id))
        return next
      })
    } else {
      // Normal click on checkbox: toggle selection
      setSelectedRoomIds(prev => {
        const next = new Set(prev)
        if (next.has(roomId)) {
          next.delete(roomId)
        } else {
          next.add(roomId)
        }
        return next
      })
    }
    setLastClickedIndex(roomIndex)
  }, [lastClickedIndex])

  // 批量录制（绑定快捷键 Ctrl+R，需二次确认以防误触）
  const handleBatchRecord = useCallback(() => {
    const connectableRooms = useAppStore.getState().rooms.filter(r => r.is_connected && !r.is_recording)
    if (connectableRooms.length === 0) {
      message.info('没有可录制的房间')
      return
    }
    console.log('[Workbench] 用户操作: 批量录制, 房间数:', connectableRooms.length)
    Modal.confirm({
      title: '确认批量录制',
      content: `将开始录制 ${connectableRooms.length} 个房间`,
      okText: '确认录制',
      cancelText: '取消',
      onOk: () => {
        connectableRooms.forEach(r => {
          useAppStore.getState().updateRoom(r.room_id, { is_recording_starting: true, last_error: '' })
          send('start_recording', { room_id: r.room_id })
        })
      },
    })
  }, [send])

  // 批量停止（绑定快捷键 Ctrl+Shift+R，需二次确认以防误触）
  const handleBatchStop = useCallback(() => {
    const recordingRooms = useAppStore.getState().rooms.filter(r => r.is_recording)
    if (recordingRooms.length === 0) {
      message.info('没有正在录制的房间')
      return
    }
    console.log('[Workbench] 用户操作: 批量停止, 房间数:', recordingRooms.length)
    Modal.confirm({
      title: '确认批量停止',
      content: `将停止 ${recordingRooms.length} 个房间的录制`,
      okText: '确认停止',
      cancelText: '取消',
      okButtonProps: { danger: true },
      onOk: () => {
        recordingRooms.forEach(r => {
          send('stop_recording', { room_id: r.room_id })
        })
      },
    })
  }, [send])

  // 获取 MSE player 的当前播放位置
  const getPreviewCurrentTime = useCallback((roomId: string): number => {
    const registry = (window as any).__msePlayers
    const entry = registry?.[roomId]
    if (entry?.player?.videoElement) {
      return entry.player.videoElement.currentTime
    }
    return 0
  }, [])

  const resolveReviewSeekEdge = useCallback((targets: Set<string>): number => {
    const refId = referenceRoomId || selectedRoomId || [...targets][0]
    if (!refId) return Math.max(lastContentEndRef.current, 1)
    const refRoom = rooms.find(r => r.room_id === refId)
    if (!isRecordingReviewMode(refRoom?.preview_mode)) {
      return Math.max(lastContentEndRef.current, 1)
    }
    const hint = computeRecordedDurationHint(refRoom, continuousAnalysisStatus?.recorded_duration)
    return resolveRecordingReviewSpan(
      getPreviewCurrentTime(refId),
      hint,
      getRoomMediaDuration(refId),
      refRoom?.mark_in,
      refRoom?.mark_out,
    )
  }, [
    referenceRoomId, selectedRoomId, rooms,
    continuousAnalysisStatus?.recorded_duration, getPreviewCurrentTime,
  ])

  // 直接控制 MSE player 的 video 元素（Electron 模式下后端无法控制 MSE video）
  const mseSeek = useCallback((roomId: string, time: number, opts?: { quiet?: boolean }) => {
    const t = Math.max(0, time)
    const quiet = opts?.quiet === true || timelineScrubbingRef.current
    if (!quiet) {
      console.log('[Workbench] MSE seek:', roomId, 'time:', t.toFixed(2))
    }
    // UI 播放头立即跟上；超出缓冲也不回弹到直播沿
    scrubOverrideRef.current[roomId] = t
    lastPreviewPositionsRef.current = { ...lastPreviewPositionsRef.current, [roomId]: t }
    // scrub 中时间线用本地 dragTime，跳过父级 setState 避免整页重渲染卡光标
    if (!quiet) {
      setPreviewPositions(prev => ({ ...prev, [roomId]: t }))
      setTimelineFollowLive(false)
    }

    const registry = (window as any).__msePlayers
    const video = registry?.[roomId]?.player?.videoElement as HTMLVideoElement | undefined
    if (video && video.buffered.length > 0) {
      const bufStart = video.buffered.start(0)
      const bufEnd = video.buffered.end(video.buffered.length - 1)
      if (t >= bufStart && t <= bufEnd) {
        try { video.currentTime = t } catch { /* seek 可能被浏览器拒绝 */ }
      }
      // 缓冲外：只动时间线 UI，不把 video 拽回 live edge
    }
    // scrub 中不刷 WebSocket seek，松手后再同步
    if (!quiet) {
      send('seek', { room_id: roomId, time: t })
    }
  }, [send])

  const mseTogglePlayPause = useCallback((roomId: string) => {
    console.log('[Workbench] 用户操作: 播放/暂停切换, roomId:', roomId)
    const registry = (window as any).__msePlayers
    const player = registry?.[roomId]?.player
    if (player) {
      if (player.state === 'paused' || player.videoElement?.paused) {
        player.resumePlayback(true)
      } else {
        player.pause()
      }
    }
    send('toggle_play_pause', { room_id: roomId })
  }, [send])

  // 时间线跳转（多选时按 content_offset 调整每房间 seek 位置）
  const resolveSeekTargets = useCallback((): Set<string> => {
    if (selectedRoomIds.size > 0) return selectedRoomIds
    if (selectedRoomId) return new Set([selectedRoomId])
    if (referenceRoomId) return new Set([referenceRoomId])
    if (rooms[0]?.room_id) return new Set([rooms[0].room_id])
    return new Set()
  }, [selectedRoomIds, selectedRoomId, referenceRoomId, rooms])

  const enterTimelineLive = useCallback((targets?: Set<string>) => {
    const ids = targets && targets.size > 0 ? targets : resolveSeekTargets()
    const roomList = useAppStore.getState().rooms
    if (targetsIncludeNoDvrMode(ids, roomList)) return
    scrubOverrideRef.current = {}
    setFrozenWindowStart(null)
    setTimelineFollowLive(true)
    timelineScrubbingRef.current = false
    setTimelineScrubbing(false)
    const registry = (window as any).__msePlayers
    ids.forEach(rid => {
      const entry = registry?.[rid]
      const player = entry?.player
      const video = player?.videoElement as HTMLVideoElement | undefined
      const bufferedLength = video?.buffered?.length ?? 0
      const bufferedStart = bufferedLength > 0 ? video!.buffered.start(0) : null
      const bufferedEnd = bufferedLength > 0 ? video!.buffered.end(bufferedLength - 1) : null
      console.log('[Workbench] 直播按钮诊断', {
        roomId: rid,
        hasPlayer: !!player,
        hasVideo: !!video,
        bufferedLength,
        bufferedStart,
        bufferedEnd,
        readyState: video?.readyState ?? null,
        currentTime: video?.currentTime ?? null,
        paused: video?.paused ?? null,
      })
      if (player && typeof player.goLive === 'function') {
        player.goLive()
      } else if (video && bufferedLength > 0 && bufferedEnd != null) {
        video.currentTime = Math.max(0, bufferedEnd - 0.5)
        video.play().catch(() => {})
      }
    })
  }, [resolveSeekTargets])

  const handleTimelineSeek = useCallback((time: number) => {
    const targets = resolveSeekTargets()
    const scrubbing = timelineScrubbingRef.current
    if (!scrubbing) {
      console.log('[Workbench] 用户操作: 时间线跳转, time:', time.toFixed(2), '房间数:', targets.size)
    }
    if (targets.size === 0) return

    const noDvr = targetsIncludeNoDvrMode(targets, rooms)
    const edge = noDvr ? resolveReviewSeekEdge(targets) : Math.max(lastContentEndRef.current, 1)
    // 不可拖过直播沿；贴右容差内视为回 Live（recording_review 无 Live 沿）
    const clamped = Math.max(0, Math.min(time, edge))
    if (!scrubbing && !noDvr && dvrStart != null && clamped < dvrStart - DVR_LEFT_TOLERANCE_SEC) {
      enterTimelineLive(targets)
      return
    }
    if (!scrubbing && !noDvr && edge - clamped <= LIVE_EDGE_TOLERANCE_SEC) {
      enterTimelineLive(targets)
      return
    }

    if (!scrubbing) setTimelineFollowLive(false)
    const ctx = useAppStore.getState().timelineContext
    const status = getAlignStatus(ctx, useAppStore.getState().timelineInvalidated)
    // scrub 中只预览一路 video，多路正式落点在松手时同步
    const seekIds = scrubbing ? [[...targets][0]].filter(Boolean) : [...targets]
    seekIds.forEach(rid => {
      if (status === 'ready' && ctx?.room_snapshots[rid]) {
        mseSeek(rid, Math.max(0, commonToPreview(ctx, rid, clamped)), { quiet: scrubbing })
        return
      }
      const room = rooms.find(r => r.room_id === rid)
      const offset = room?.content_offset ?? 0
      mseSeek(rid, Math.max(0, clamped - offset), { quiet: scrubbing })
    })
  }, [resolveSeekTargets, enterTimelineLive, mseSeek, rooms, dvrStart, resolveReviewSeekEdge])

  const handleTimelineScrubStart = useCallback((ws: number) => {
    // 冻结真实左缘；一拖即退出 Live
    setFrozenWindowStart(Math.max(0, ws))
    timelineScrubbingRef.current = true
    setTimelineScrubbing(true)
    setTimelineFollowLive(false)
  }, [])

  const handleTimelineScrubEnd = useCallback((finalTime?: number) => {
    timelineScrubbingRef.current = false
    setTimelineScrubbing(false)
    const targets = resolveSeekTargets()
    const noDvr = targetsIncludeNoDvrMode(targets, rooms)
    const edge = noDvr ? resolveReviewSeekEdge(targets) : Math.max(lastContentEndRef.current, 1)
    const ctx = useAppStore.getState().timelineContext
    const status = getAlignStatus(ctx, useAppStore.getState().timelineInvalidated)

    let playhead = finalTime
    if (playhead == null) {
      const rid = referenceRoomId
        || selectedRoomId
        || (selectedRoomIds.size > 0 ? [...selectedRoomIds][0] : null)
        || rooms[0]?.room_id
        || null
      if (!rid) return
      playhead = lastPreviewPositionsRef.current[rid] ?? 0
      if (status === 'ready' && ctx?.room_snapshots[rid]) {
        try {
          playhead = previewToCommon(ctx, rid, playhead)
        } catch { /* 保持 preview 轴 */ }
      }
    }

    const clamped = Math.max(0, Math.min(playhead, edge))
    if (!noDvr && dvrStart != null && clamped < dvrStart - DVR_LEFT_TOLERANCE_SEC) {
      enterTimelineLive(targets)
      return
    }
    if (!noDvr && edge - clamped <= LIVE_EDGE_TOLERANCE_SEC) {
      enterTimelineLive(targets)
      return
    }
    setTimelineFollowLive(false)
    // 正式落点：写 UI + WS（拖拽过程不 seek，仅松手一次）
    targets.forEach(targetRid => {
      if (status === 'ready' && ctx?.room_snapshots[targetRid]) {
        mseSeek(targetRid, Math.max(0, commonToPreview(ctx, targetRid, clamped)))
        return
      }
      const room = rooms.find(r => r.room_id === targetRid)
      const offset = room?.content_offset ?? 0
      mseSeek(targetRid, Math.max(0, clamped - offset))
    })
  }, [referenceRoomId, selectedRoomId, selectedRoomIds, rooms, enterTimelineLive, resolveSeekTargets, mseSeek, dvrStart, resolveReviewSeekEdge])

  // 添加到切片列表
  const handleAddClip = useCallback((roomId: string) => {
    const currentRooms = useAppStore.getState().rooms
    const currentClips = useAppStore.getState().clips
    const room = currentRooms.find(r => r.room_id === roomId)
    if (!room?.record_output_path) {
      message.warning('请先开始录制后再添加切片')
      return
    }
    if (room && room.mark_in !== null && room.mark_out !== null) {
      console.log('[Workbench] 用户操作: 添加切片, roomId:', roomId, 'mark_in:', room.mark_in, 'mark_out:', room.mark_out)
      const roomManualCount = currentClips.filter(c => c.room_id === roomId && c.source === 'manual').length
      const newIndex = roomManualCount + 1
      const newClip: ClipSegment = {
        start: room.mark_in,
        end: room.mark_out,
        label: formatManualClipLabel(room.streamer_name || roomId, newIndex),
        room_id: roomId,
        source: 'manual',
        mark_in_wallclock: room.mark_in_wallclock ?? null,
        mark_out_wallclock: room.mark_out_wallclock ?? null,
        recording_start_mono: room.recording_start_mono ?? null,
        recording_media_start_mono: room.recording_media_start_mono ?? null,
        content_offset: room.content_offset ?? 0,
        mark_precision:
          room.mark_in_wallclock != null &&
          room.mark_out_wallclock != null &&
          (room.recording_media_start_mono ?? room.recording_start_mono) != null
            ? 'exact'
            : 'approximate',
      }
      addClip(newClip)
      message.success('已添加到切片列表')
    } else {
      message.warning('请先设置入点和出点')
    }
  }, [addClip])

  // ── 稳定的 ControlBar / RoomCard 回调 ──

  const handleControlPlayPause = useCallback(() => {
    console.log('[Workbench] 用户操作: 控制栏播放/暂停, 房间数:', selectedRoomIds.size)
    selectedRoomIds.forEach(rid => mseTogglePlayPause(rid))
  }, [selectedRoomIds, mseTogglePlayPause])

  /** 相对当前播放头步进（公共轴同步各房）；后退退出 Live，贴右回 Live */
  const handleSeekByDelta = useCallback((delta: number) => {
    const targets = resolveSeekTargets()
    if (targets.size === 0) return
    const ctx = useAppStore.getState().timelineContext
    const status = getAlignStatus(ctx, useAppStore.getState().timelineInvalidated)
    const noDvr = targetsIncludeNoDvrMode(targets, rooms)
    const edge = noDvr ? resolveReviewSeekEdge(targets) : Math.max(lastContentEndRef.current, 1)
    if (status === 'ready' && ctx) {
      const refId = pickReferenceRoomId(ctx, selectedRoomIds, selectedRoomId)
      if (!refId) return
      const commonT = previewToCommon(ctx, refId, getPreviewCurrentTime(refId)) + delta
      const clamped = Math.max(0, Math.min(commonT, edge))
      if (!noDvr && dvrStart != null && clamped < dvrStart - DVR_LEFT_TOLERANCE_SEC) {
        enterTimelineLive(targets)
        return
      }
      if (!noDvr && edge - clamped <= LIVE_EDGE_TOLERANCE_SEC) {
        enterTimelineLive(targets)
        return
      }
      setTimelineFollowLive(false)
      targets.forEach(rid => {
        if (!ctx.room_snapshots[rid]) return
        mseSeek(rid, Math.max(0, commonToPreview(ctx, rid, clamped)))
      })
      return
    }
    const anyId = [...targets][0]
    const next = Math.max(0, Math.min(getPreviewCurrentTime(anyId) + delta, edge))
    if (!noDvr && dvrStart != null && next < dvrStart - DVR_LEFT_TOLERANCE_SEC) {
      enterTimelineLive(targets)
      return
    }
    if (!noDvr && edge - next <= LIVE_EDGE_TOLERANCE_SEC) {
      enterTimelineLive(targets)
      return
    }
    setTimelineFollowLive(false)
    targets.forEach(rid => {
      const cur = getPreviewCurrentTime(rid)
      mseSeek(rid, Math.max(0, Math.min(cur + delta, edge)))
    })
  }, [selectedRoomIds, selectedRoomId, getPreviewCurrentTime, mseSeek, resolveSeekTargets, enterTimelineLive, dvrStart, rooms, resolveReviewSeekEdge])

  const handleControlSeekBack = useCallback(() => {
    handleSeekByDelta(-10)
  }, [handleSeekByDelta])

  const handleControlSeekFwd = useCallback(() => {
    handleSeekByDelta(10)
  }, [handleSeekByDelta])

  const handleNudgeMark = useCallback((which: 'in' | 'out', delta: number) => {
    const ctx = useAppStore.getState().timelineContext
    const status = getAlignStatus(ctx, useAppStore.getState().timelineInvalidated)
    if (status === 'ready' && ctx) {
      const base = which === 'in' ? commonMarkIn : commonMarkOut
      if (base == null) {
        message.warning(which === 'in' ? '请先设置入点' : '请先设置出点')
        return
      }
      const next = Math.max(0, base + delta)
      if (which === 'in') setCommonMarkIn(next)
      else setCommonMarkOut(next)
      selectedRoomIds.forEach(rid => {
        if (!ctx.room_snapshots[rid]) return
        const local = commonToPreview(ctx, rid, next)
        send(which === 'in' ? 'set_mark_in' : 'set_mark_out', { room_id: rid, time: local, live: false })
      })
      return
    }
    selectedRoomIds.forEach(rid => {
      const room = useAppStore.getState().rooms.find(r => r.room_id === rid)
      const base = which === 'in' ? room?.mark_in : room?.mark_out
      if (base == null) return
      const next = Math.max(0, base + delta)
      send(which === 'in' ? 'set_mark_in' : 'set_mark_out', { room_id: rid, time: next, live: false })
    })
  }, [selectedRoomIds, send, commonMarkIn, commonMarkOut])

  const applyPlaybackRate = useCallback((rate: number) => {
    selectedRoomIds.forEach(rid => {
      const registry = (window as any).__msePlayers
      const video = registry?.[rid]?.player?.videoElement as HTMLVideoElement | undefined
      if (video) {
        try { video.playbackRate = rate } catch { /* ignore */ }
      }
    })
  }, [selectedRoomIds])

  const handleSetPlaybackRate = useCallback((rate: PlaybackRate) => {
    setPlaybackRate(rate)
    applyPlaybackRate(rate)
  }, [applyPlaybackRate])

  const handleCyclePlaybackRate = useCallback((dir: 1 | -1) => {
    const idx = PLAYBACK_RATE_STEPS.indexOf(playbackRate)
    const nextIdx = Math.max(0, Math.min(PLAYBACK_RATE_STEPS.length - 1, (idx < 0 ? 1 : idx) + dir))
    const next = PLAYBACK_RATE_STEPS[nextIdx] as PlaybackRate
    handleSetPlaybackRate(next)
  }, [playbackRate, handleSetPlaybackRate])

  const handleControlMarkIn = useCallback(() => {
    console.log('[Workbench] 用户操作: 控制栏设置入点, 房间数:', selectedRoomIds.size)
    const ctx = useAppStore.getState().timelineContext
    const status = getAlignStatus(ctx, useAppStore.getState().timelineInvalidated)
    if (status === 'ready' && ctx) {
      const refId = pickReferenceRoomId(ctx, selectedRoomIds, selectedRoomId)
      if (!refId) return
      const commonT = previewToCommon(ctx, refId, getPreviewCurrentTime(refId))
      setCommonMarkIn(commonT)
      selectedRoomIds.forEach(rid => {
        if (!ctx.room_snapshots[rid]) return
        const local = commonToPreview(ctx, rid, commonT)
        send('set_mark_in', { room_id: rid, time: local, live: true })
      })
      return
    }
    selectedRoomIds.forEach(rid => {
      const time = getPreviewCurrentTime(rid)
      send('set_mark_in', { room_id: rid, time, live: true })
    })
  }, [selectedRoomIds, send, getPreviewCurrentTime, selectedRoomId])

  const handleControlMarkOut = useCallback(() => {
    console.log('[Workbench] 用户操作: 控制栏设置出点, 房间数:', selectedRoomIds.size)
    const ctx = useAppStore.getState().timelineContext
    const status = getAlignStatus(ctx, useAppStore.getState().timelineInvalidated)
    if (status === 'ready' && ctx) {
      const refId = pickReferenceRoomId(ctx, selectedRoomIds, selectedRoomId)
      if (!refId) return
      const commonT = previewToCommon(ctx, refId, getPreviewCurrentTime(refId))
      setCommonMarkOut(commonT)
      selectedRoomIds.forEach(rid => {
        if (!ctx.room_snapshots[rid]) return
        const local = commonToPreview(ctx, rid, commonT)
        send('set_mark_out', { room_id: rid, time: local, live: true })
      })
      return
    }
    selectedRoomIds.forEach(rid => {
      const time = getPreviewCurrentTime(rid)
      send('set_mark_out', { room_id: rid, time, live: true })
    })
  }, [selectedRoomIds, send, getPreviewCurrentTime, selectedRoomId])

  const handleControlAddClip = useCallback(async () => {
    if (selectedRoomIds.size === 0) return
    const ctx = useAppStore.getState().timelineContext
    const status = getAlignStatus(ctx, useAppStore.getState().timelineInvalidated)
    if (status === 'ready' && ctx && commonMarkIn != null && commonMarkOut != null && commonMarkIn < commonMarkOut) {
      if (!ctx.clip_ready) {
        message.warning('已对齐但录制未就绪，无法创建精确切片；请确认各房间正在录制')
        return
      }
      const targetIds = [...selectedRoomIds].filter(rid => {
        const room = useAppStore.getState().rooms.find(r => r.room_id === rid)
        return !!room?.record_output_path && !!ctx.room_snapshots[rid]
      })
      if (targetIds.length === 0) {
        message.warning('请先开始录制后再添加切片')
        return
      }
      try {
        const res = await sendRequest({ send, on }, 'create_clip_snapshot', {
          timeline_id: ctx.timeline_id,
          common_start: commonMarkIn,
          common_end: commonMarkOut,
          target_room_ids: targetIds,
          source: 'manual',
        }) as { success?: boolean; clips?: Array<{ clip_id: string; room_id: string; common_start: number; common_end: number }>; error?: string; failed_room?: string }
        if (!res?.success || !res.clips?.length) {
          message.error(res?.error === 'RANGE_UNAVAILABLE'
            ? `时间范围不可用: ${res.failed_room ?? ''}`
            : (res?.error || '创建切片失败'))
          return
        }
        const store = useAppStore.getState()
        res.clips.forEach((c, i) => {
          const room = store.rooms.find(r => r.room_id === c.room_id)
          const roomManualCount = store.clips.filter(cc => cc.room_id === c.room_id && cc.source === 'manual').length
          addClip({
            start: c.common_start,
            end: c.common_end,
            common_start: c.common_start,
            common_end: c.common_end,
            label: formatManualClipLabel(room?.streamer_name ?? c.room_id, roomManualCount + i + 1),
            room_id: c.room_id,
            clip_id: c.clip_id,
            clip_snapshot_id: c.clip_id,
            timeline_id: ctx.timeline_id,
            source: 'manual',
            mark_precision: 'exact',
          })
        })
        message.success(`已添加 ${res.clips.length} 个切片（公共轴精确）`)
      } catch (err) {
        console.error('[Workbench] create_clip_snapshot 失败:', err)
        message.error('创建切片失败')
      }
      return
    }
    console.log('[Workbench] 用户操作: 控制栏添加切片, 房间数:', selectedRoomIds.size)
    selectedRoomIds.forEach(rid => handleAddClip(rid))
  }, [selectedRoomIds, handleAddClip, addClip, commonMarkIn, commonMarkOut, send, on])

  const handleGoLive = useCallback(() => {
    const targets = resolveSeekTargets()
    if (targetsIncludeNoDvrMode(targets, rooms)) return
    console.log('[Workbench] 用户操作: 跳转到直播最新位置, 房间数:', targets.size)
    if (targets.size === 0) {
      console.warn('[Workbench] 直播按钮诊断: 未选中房间')
      return
    }
    enterTimelineLive(targets)
  }, [rooms, resolveSeekTargets, enterTimelineLive])

  // Phase 3: 音频对齐结果监听器
  useEffect(() => {
    const unsub = on('align_preview_audio_response', (data: any) => {
      setAligning(false)
      message.destroy('align')
      if (!data?.success || !data?.offsets) {
        console.warn('[Workbench] 音频对齐失败:', data?.error, data?.scores)
        const err = String(data?.error || '')
        if (err.includes('可信对齐不足')) {
          const scores = (data?.scores || {}) as Record<string, number>
          const vals = Object.values(scores).map(v => Number(v) || 0)
          const best = vals.length ? Math.max(...vals) : 0
          message.warning(
            `未精确对齐：声音匹配度不足（最高置信度 ${Math.round(best * 100)}%）。请确认各房间在播同一场、音量正常，并停在直播沿后重试`,
          )
        } else {
          message.warning(`未精确对齐：${err || '对齐计算失败'}，导出可能不同步（已用本地时间）`)
        }
        return
      }
      if (data.timeline) {
        setTimelineContext(data.timeline)
        setTimelineInvalidated(false)
        setCommonMarkIn(null)
        setCommonMarkOut(null)
      }
      const offsets = data.offsets as Record<string, number>
      const scores = (data.scores || {}) as Record<string, number>
      const referenceRoomId = data.reference_room_id as string
      const registry = (window as any).__msePlayers
      const alignmentTrustThreshold = 0.3
      let alignedCount = 0
      let lowConfidenceCount = 0
      const trustedScores: number[] = []
      const aligningRoomIds = Array.from(aligningRoomIdsRef.current)
      aligningRoomIds.forEach((rid: string) => {
        const offset = offsets[rid]
        if (offset === undefined) return
        const score = scores[rid] ?? 0
        if (score < alignmentTrustThreshold) {
          lowConfidenceCount++
          send('set_content_offset', { room_id: rid, offset: 0 })
          return
        }

        trustedScores.push(score)
        // 回传 content_offset 到后端（用于导出时补偿）
        send('set_content_offset', { room_id: rid, offset })

        if (offset < 0.05) { alignedCount++; return }
        const video = registry?.[rid]?.player?.videoElement as HTMLVideoElement | undefined
        if (video && video.buffered.length > 0) {
          const bufStart = video.buffered.start(0)
          const target = Math.max(bufStart, video.currentTime - offset)
          try { video.currentTime = target } catch {}
          video.play().catch(() => {})
          alignedCount++
        }
      })

      const avgScore = trustedScores.length > 0
        ? trustedScores.reduce((a, b) => a + b, 0) / trustedScores.length
        : 0
      console.log(
        '[Workbench] 音频对齐完成: aligned=' + alignedCount
        + ', lowConfidence=' + lowConfidenceCount
        + ', avgScore=' + avgScore.toFixed(3),
      )

      const fastRoomIds = aligningRoomIds.filter((rid: string) => {
        const offset = offsets[rid]
        const score = scores[rid] ?? 0
        return offset !== undefined && score >= alignmentTrustThreshold && offset > 0.05 && rid !== referenceRoomId
      })

      if (lowConfidenceCount > 0) {
        message.warning(
          `${alignedCount} 个直播间已对齐，${lowConfidenceCount} 个置信度不足已跳过`,
        )
      } else {
        message.success(
          `已精确对齐 ${alignedCount} 个直播间（置信度 ${Math.round(avgScore * 100)}%）`,
        )
      }

      if (fastRoomIds.length > 0) {
        message.info({
          content: (
            <span>
              检测到 {fastRoomIds.length} 个快房间，
              <Button
                type="link"
                size="small"
                style={{ padding: '0 4px' }}
                onClick={() => {
                  fastRoomIds.forEach(rid => {
                    send('set_preview_muted', { room_id: rid, muted: true })
                  })
                  message.success(`已静音 ${fastRoomIds.length} 个快房间`)
                }}
              >
                点击静音
              </Button>
            </span>
          ),
          duration: 6,
        })
      }
    })
    return () => unsub()
  }, [on, send, setTimelineContext, setTimelineInvalidated])

  const handleAlignLive = useCallback(async () => {
    if (selectedRoomIds.size === 0) return
    console.log('[Workbench] 用户操作: 一键对齐, 房间数:', selectedRoomIds.size)
    const registry = (window as any).__msePlayers
    if (!registry) return

    // Phase 1: 各房间独立跳到自己的直播沿。
    // 禁止共用同一个 currentTime 绝对值——预览启动有先后时，
    // 长缓冲房间会被拉回旧画面，短缓冲房间仍在直播沿，互相关必然失败。
    {
      let anyBuffered = false
      await Promise.all([...selectedRoomIds].map(async rid => {
        const entry = registry?.[rid]
        const player = entry?.player
        const video = player?.videoElement as HTMLVideoElement | undefined
        if (!video || video.buffered.length === 0) return
        anyBuffered = true
        const end = video.buffered.end(video.buffered.length - 1)
        const targetTime = Math.max(0, end - 0.5)
        if (Math.abs(video.currentTime - targetTime) < 0.8) {
          video.play().catch(() => {})
          return
        }
        if (player && typeof player.goLive === 'function') {
          player.goLive()
        } else {
          try { video.currentTime = targetTime } catch { /* ignore */ }
          video.play().catch(() => {})
        }
        await new Promise<void>(resolve => {
          const onSeeked = () => {
            video.removeEventListener('seeked', onSeeked)
            resolve()
          }
          video.addEventListener('seeked', onSeeked)
          setTimeout(resolve, 1500)
        })
      }))
      if (!anyBuffered && selectedRoomIds.size >= 2) {
        message.warning('未精确对齐：预览缓冲未就绪，请等画面开始播放后再试')
        return
      }
      // seek 后稍等，让 Web Audio 重新流出有效 PCM
      if (selectedRoomIds.size >= 2) {
        await new Promise(resolve => setTimeout(resolve, 350))
      }
    }

    // 少于 2 个房间时不需要音频对齐
    if (selectedRoomIds.size < 2) {
      message.info('已同步预览进度（单房间无需音频对齐）')
      return
    }
    message.loading({ content: '对齐中...', key: 'align', duration: 0 })

    // Phase 2: 并行音频捕获 + 后端 FFT 计算
    setAligning(true)
    aligningRoomIdsRef.current = new Set(selectedRoomIds)
    try {
      const aligner = getAligner()
      const previewAlignDuration = 8.0
      const captureFailures: CaptureFailure[] = []
      const capturePromises = [...selectedRoomIds].map(async rid => {
        const entry = registry?.[rid]
        const video = entry?.player?.videoElement as HTMLVideoElement | undefined
        if (!video) {
          captureFailures.push({ roomId: rid, reason: 'no_video' })
          return null
        }
        const pcm = await aligner.captureAudio(rid, video, previewAlignDuration)
        const captureDiagnostics = aligner.getLastCaptureDiagnostics(rid)
        if (!pcm) {
          captureFailures.push({
            roomId: rid,
            reason: captureDiagnostics?.reason ?? 'unknown',
            diagnostics: captureDiagnostics,
          })
          return null
        }
        const buffered = video.buffered
        return {
          room_id: rid,
          sample_rate: 16000,
          pcm_base64: aligner.base64Encode(pcm),
          diagnostics: {
            current_time: video.currentTime,
            buffer_start: buffered.length ? buffered.start(0) : null,
            buffer_end: buffered.length ? buffered.end(buffered.length - 1) : null,
            ingest_mode: entry?.ingestMode || 'unknown',
            ready_state: captureDiagnostics?.ready_state ?? video.readyState,
            has_audio_track: captureDiagnostics?.has_audio_track ?? true,
            rms: captureDiagnostics?.rms ?? null,
            sample_count: captureDiagnostics?.sample_count ?? pcm.length,
            capture_reason: captureDiagnostics?.reason ?? 'ok',
          },
        }
      })
      const results = (await Promise.all(capturePromises)).filter((r): r is {
        room_id: string
        sample_rate: number
        pcm_base64: string
        diagnostics: {
          current_time: number
          buffer_start: number | null
          buffer_end: number | null
          ingest_mode: string
          ready_state: number
          has_audio_track: boolean
          rms: number | null
          sample_count: number
          capture_reason: string
        }
      } => r !== null)
      if (results.length < 2) {
        setAligning(false)
        message.destroy('align')
        const failureSummary = formatCaptureFailureSummary(captureFailures)
        console.warn('[Workbench] 音频捕获不足诊断', {
          selectedRooms: [...selectedRoomIds],
          capturedRooms: results.map(r => r.room_id),
          captureFailures,
          failureSummary,
        })
        message.warning(`未精确对齐：有效音频不足（${failureSummary}）`)
        return
      }
      send('align_preview_audio', { rooms: results })
    } catch (err) {
      setAligning(false)
      message.destroy('align')
      console.error('[Workbench] 音频对齐异常:', err)
      message.warning('未精确对齐：音频捕获异常，导出可能不同步（已用本地时间）')
    }
  }, [selectedRoomIds, send, rooms])

  // 拖拽中：仅本地更新显示，不发 WS（松手才 commit）
  const handleMarkerDrag = useCallback((type: 'in' | 'out', time: number) => {
    setLocalDragMark({ type, time })
  }, [])

  const handleMarkerDragEnd = useCallback((type: 'in' | 'out', time: number) => {
    setLocalDragMark(null)  // 清除本地拖拽显示
    console.log('[Workbench] 用户操作: 标记拖拽结束, type:', type, 'time:', time.toFixed(2))
    const ctx = useAppStore.getState().timelineContext
    const status = getAlignStatus(ctx, useAppStore.getState().timelineInvalidated)
    if (status === 'ready' && ctx) {
      if (type === 'in') setCommonMarkIn(time)
      else setCommonMarkOut(time)
      selectedRoomIds.forEach(rid => {
        if (!ctx.room_snapshots[rid]) return
        const local = commonToPreview(ctx, rid, time)
        send(type === 'in' ? 'set_mark_in' : 'set_mark_out', { room_id: rid, time: local, live: false })
      })
      if (selectedRoomIds.size > 0) {
        const refId = pickReferenceRoomId(ctx, selectedRoomIds, selectedRoomId)
        if (refId) mseSeek(refId, Math.max(0, commonToPreview(ctx, refId, time)))
      }
      return
    }
    selectedRoomIds.forEach(rid => {
      if (type === 'in') {
        send('set_mark_in', { room_id: rid, time, live: false })
      } else {
        send('set_mark_out', { room_id: rid, time, live: false })
      }
    })
    if (selectedRoomIds.size > 0) {
      mseSeek([...selectedRoomIds][0], time)
    }
    message.info('近似定位：拖拽标记可能偏差数秒，精确导出请用 I / O 键', 3)
  }, [selectedRoomIds, send, mseSeek, selectedRoomId])

  const handleDeleteMarker = useCallback((type: 'in' | 'out') => {
    console.log('[Workbench] 用户操作: 删除标记, type:', type)
    if (type === 'in') setCommonMarkIn(null)
    else setCommonMarkOut(null)
    selectedRoomIds.forEach(rid => {
      if (type === 'in') {
        send('set_mark_in', { room_id: rid, time: null })
      } else {
        send('set_mark_out', { room_id: rid, time: null })
      }
    })
    // 列表已反映变化，无需 toast
  }, [selectedRoomIds, send])

  // 删除切片（W8.1：删除前取消相关导出/精修任务）
  const handleDeleteClip = (index: number) => {
    console.log('[Workbench] 用户操作: 删除切片, index:', index)
    const clip = clips[index]
    if (!clip) return
    if (clip.export_status === 'queued' || clip.export_status === 'exporting') {
      if (clip.job_id) {
        console.log('[Workbench] 删除切片时取消导出任务, job_id:', clip.job_id)
        send('cancel_export', { job_id: clip.job_id })
      }
    }
    if (clip.confirm_status === 'refining' && refiningClipId != null) {
      const clipKey = clip.round_key || clip.clip_id || ''
      if (clipKey === refiningClipId) {
        console.log('[Workbench] 删除切片时取消精修, clipKey:', clipKey)
        send('cancel_refine_clip', {
          room_id: clip.room_id,
          round_key: clipKey,
          start: clip.start,
          end: clip.end,
        })
        setRefiningClipId(null)
      }
    }
    setClips(clips.filter((_, i) => i !== index))
  }

  // 导出切片
  const handleExportClip = (clip: ClipSegment, _index?: number) => {
    console.log('[Workbench] 用户操作: 导出切片, roomId:', clip.room_id, 'label:', clip.label)
    setPreviewClip(clip)
  }

  // 进入精修：发送 begin_refine_clip，写入 mark_in/out，seek 到入点
  const applySelectClip = (clip: ClipSegment, clipKey: string, oldRefiningClip?: ClipSegment) => {
    if (oldRefiningClip) {
      send('cancel_refine_clip', {
        room_id: oldRefiningClip.room_id,
        round_key: oldRefiningClip.round_key || oldRefiningClip.clip_id || '',
        start: oldRefiningClip.start,
        end: oldRefiningClip.end,
      })
    }
    setRefiningClipId(clipKey)
    const roomId = clip.room_id
    if (roomId) {
      setSelectedRoomIds(new Set([roomId]))
      setSelectedRoomId(roomId)
    }

    const start = clip.start
    const end = clip.end
    const ctx = useAppStore.getState().timelineContext
    const status = getAlignStatus(ctx, useAppStore.getState().timelineInvalidated)

    const toPreview = (rec: number): number => {
      const snap = ctx?.room_snapshots[roomId!]
      if (!snap) return rec
      return rec + snap.recording_to_common_delta - snap.preview_to_common_delta
    }

    const pvStart = toPreview(start)
    const pvEnd = toPreview(end)
    if (status === 'ready' && ctx && roomId) {
      try {
        const commonStart = clip.common_start ?? recordingToCommon(ctx, roomId, start)
        const commonEnd = clip.common_end ?? recordingToCommon(ctx, roomId, end)
        setCommonMarkIn(commonStart)
        setCommonMarkOut(commonEnd)
        const targets = new Set<string>([roomId, ...selectedRoomIds])
        targets.forEach(rid => {
          const snap = ctx.room_snapshots[rid]
          if (!snap) return
          send('set_mark_in', { room_id: rid, time: commonToPreview(ctx, rid, commonStart), live: false })
          send('set_mark_out', { room_id: rid, time: commonToPreview(ctx, rid, commonEnd), live: false })
        })
        mseSeek(roomId, clip.common_start != null ? commonToPreview(ctx, roomId, clip.common_start) : pvStart)
      } catch {
        setCommonMarkIn(null)
        setCommonMarkOut(null)
        send('set_mark_in', { room_id: roomId, time: pvStart, live: false })
        send('set_mark_out', { room_id: roomId, time: pvEnd, live: false })
        mseSeek(roomId, pvStart)
      }
    } else if (roomId) {
      setCommonMarkIn(null)
      setCommonMarkOut(null)
      send('set_mark_in', { room_id: roomId, time: pvStart, live: false })
      send('set_mark_out', { room_id: roomId, time: pvEnd, live: false })
      mseSeek(roomId, pvStart)
    }

    send('begin_refine_clip', {
      room_id: roomId,
      round_key: clipKey,
      start,
      end,
    })
  }

  const hasRefineMarksChanged = (oldClip: ClipSegment): boolean => {
    const EPS = 0.05
    const st = useAppStore.getState()
    const room = st.rooms.find(r => r.room_id === oldClip.room_id)
    const ctx = st.timelineContext
    const status = getAlignStatus(ctx, st.timelineInvalidated)

    if (status === 'ready' && ctx && commonMarkIn != null && commonMarkOut != null) {
      let start = commonMarkIn
      let end = commonMarkOut
      if (localDragMark) {
        if (localDragMark.type === 'in') start = localDragMark.time
        else end = localDragMark.time
      }
      const origStart = oldClip.common_start ?? oldClip.start
      const origEnd = oldClip.common_end ?? oldClip.end
      return Math.abs(start - origStart) > EPS || Math.abs(end - origEnd) > EPS
    }

    if (room?.mark_in != null && room?.mark_out != null) {
      let start = room.mark_in
      let end = room.mark_out
      if (localDragMark) {
        if (localDragMark.type === 'in') start = localDragMark.time
        else end = localDragMark.time
      }
      return Math.abs(start - oldClip.start) > EPS || Math.abs(end - oldClip.end) > EPS
    }
    return false
  }

  const handleSelectClip = (clip: ClipSegment, _index: number) => {
    // 手动切片（非 AI 高光，无 round_key）不进精修，直接提示可导出
    const isManual = clip.source === 'manual' || (!clip.source && !clip.is_ai_highlight && !clip.round_key)
    if (isManual && clip.clip_id) {
      message.info('手动切片可直接导出，无需进入精修')
      return
    }
    if (!clip.round_key && !clip.clip_id) return
    const clipKey = clip.round_key || clip.clip_id || ''
    if (refiningClipId === clipKey) return

    const oldRefiningClip = refiningClipId
      ? clips.find(c => c.round_key === refiningClipId || c.clip_id === refiningClipId)
      : undefined

    if (oldRefiningClip && hasRefineMarksChanged(oldRefiningClip)) {
      Modal.confirm({
        title: '放弃未保存的精修调整？',
        content: '当前回合的入出点已修改，切换后将丢弃这些调整。',
        okText: '切换',
        cancelText: '留在当前',
        onOk: () => applySelectClip(clip, clipKey, oldRefiningClip),
      })
      return
    }

    applySelectClip(clip, clipKey, oldRefiningClip)
  }

  const handleConfirmClip = (clip: ClipSegment, _index: number): ClipSegment | null => {
    const clipKey = clip.round_key || clip.clip_id || ''
    const store = useAppStore.getState()
    const room = store.rooms.find(r => r.room_id === clip.room_id)
    const ctx = store.timelineContext
    const status = getAlignStatus(ctx, store.timelineInvalidated)

    const rid = clip.room_id
    const toRecording = (pv: number): number => {
      const snap = ctx?.room_snapshots[rid ?? '']
      if (!snap) return pv
      return pv - snap.recording_to_common_delta + snap.preview_to_common_delta
    }

    let start = clip.start
    let end = clip.end
    if (status === 'ready' && ctx && rid && commonMarkIn != null && commonMarkOut != null && commonMarkIn < commonMarkOut) {
      try {
        start = commonToRecording(ctx, rid, commonMarkIn)
        end = commonToRecording(ctx, rid, commonMarkOut)
      } catch {
        /* fall through to room marks */
      }
    } else if (room?.mark_in != null && room?.mark_out != null && room.mark_in < room.mark_out) {
      try {
        start = toRecording(room.mark_in)
        end = toRecording(room.mark_out)
      } catch { /* keep clip.start/end */ }
    }
    if (localDragMark) {
      try {
        if (status === 'ready' && ctx && rid) {
          if (localDragMark.type === 'in') start = commonToRecording(ctx, rid, localDragMark.time)
          else end = commonToRecording(ctx, rid, localDragMark.time)
        } else {
          if (localDragMark.type === 'in') start = toRecording(localDragMark.time)
          else end = toRecording(localDragMark.time)
        }
      } catch { /* keep prior start/end */ }
    }
    if (!(end > start)) {
      message.warning('入出点无效，请先调整后再确认')
      return null
    }

    const targetRoomIds = store.rooms
      .filter(r => r.room_id !== clip.room_id && r.is_connected)
      .map(r => r.room_id)
    send('confirm_highlight_clip', {
      room_id: clip.room_id,
      round_key: clipKey,
      start,
      end,
      target_room_ids: targetRoomIds,
    })
    setRefiningClipId(null)
    setLocalDragMark(null)

    const confirmed: ClipSegment = {
      ...clip,
      start,
      end,
      confirm_status: 'user_confirmed',
    }
    store.setClips(store.clips.map(c => {
      const same =
        (clipKey && (c.round_key === clipKey || c.clip_id === clipKey)) ||
        (c.clip_id && c.clip_id === clip.clip_id) ||
        (c.room_id === clip.room_id && c.start === clip.start && c.end === clip.end)
      return same ? { ...c, start, end, confirm_status: 'user_confirmed' as const } : c
    }))
    return confirmed
  }

  const handleConfirmAndExport = (clip: ClipSegment, index: number) => {
    console.log('[Workbench] 用户操作: 确认并导出, roomId:', clip.room_id, 'label:', clip.label)
    const confirmed = handleConfirmClip(clip, index)
    if (!confirmed) return
    setPreviewClip(confirmed)
  }

  const handleExportMany = (targets: ClipSegment[]) => {
    if (targets.length === 0) return
    if (targets.length === 1) {
      handleExportClip(targets[0])
      return
    }
    const hasApproximate = targets.some(isApproximateClip)
    const store = useAppStore.getState()
    let queued = 0
    let skipped = 0
    targets.forEach((clip, i) => {
      const room = store.rooms.find(r => r.room_id === clip.room_id)
      if (!room?.record_output_path) { skipped++; return }
      const jobId = `export-${Date.now()}-${i}`
      const preset = exportPresetId || useAppStore.getState().appSettings?.default_export_preset || ''
      const snapshotJobId = clip.clip_snapshot_id ? clipSnapshotJobId(clip.clip_snapshot_id) : jobId
      if (clip.clip_snapshot_id) {
        send('export_clip_by_id', {
          clip_id: clip.clip_snapshot_id,
          label: clip.label,
          preset_id: preset,
          source: clip.is_ai_highlight ? 'ai_highlight' : 'manual',
        })
      } else {
        send('export_clip', {
          room_id: clip.room_id,
          start: clip.start,
          end: clip.end,
          label: clip.label,
          preset_id: preset,
          job_id: jobId,
          source: clip.is_ai_highlight ? 'ai_highlight' : 'manual',
          mark_in_wallclock: clip.mark_in_wallclock,
          mark_out_wallclock: clip.mark_out_wallclock,
          recording_start_mono: clip.recording_start_mono,
          recording_media_start_mono: clip.recording_media_start_mono,
          content_offset: clip.content_offset,
          use_room_marks: false,
        })
      }
      queued += 1
      pendingExportJobIdsRef.current.add(snapshotJobId)
      store.setClips(useAppStore.getState().clips.map(c =>
        c.clip_id === clip.clip_id || (c.start === clip.start && c.end === clip.end && c.room_id === clip.room_id)
          ? { ...c, job_id: snapshotJobId, exported: false, export_status: 'queued' as const, export_error: undefined }
          : c
      ))
    })
    if (queued > 0) {
      const skipMsg = skipped > 0 ? `，跳过 ${skipped}（无录制文件）` : ''
      if (hasApproximate) {
        message.warning(
          `含近似定位切片，导出时间可能偏差数秒；精确导出请用 I / O 键标记。已排队 ${queued}${skipMsg}`,
        )
      } else {
        message.success(`已排队 ${queued}${skipMsg}`)
      }
    } else {
      message.warning('没有可导出的切片（缺少录制文件）')
    }
  }

  // 打开导出的文件
  const handleOpenExportFile = (outputPath: string) => {
    console.log('[Workbench] 用户操作: 打开导出文件, path:', outputPath)
    if (window.electronAPI) {
      window.electronAPI.openPath(outputPath)
    }
  }

  const handleConfirmExport = () => {
    if (!previewClip) return
    console.log('[Workbench] 用户操作: 确认导出, roomId:', previewClip.room_id, 'start:', previewClip.start, 'end:', previewClip.end, 'preset:', exportPresetId)
    // 检查该房间是否有可用的录制文件
    const room = rooms.find(r => r.room_id === previewClip.room_id)
    if (!room) {
      message.error('房间不存在')
      return
    }
    if (!room.record_output_path) {
      message.error('该房间没有录制文件，请先开始录制再导出切片')
      return
    }
    const isApproximate = isApproximateClip(previewClip)
    const jobId = previewClip.clip_snapshot_id
      ? clipSnapshotJobId(previewClip.clip_snapshot_id)
      : `export-${Date.now()}`

    if (previewClip.clip_snapshot_id) {
      send('export_clip_by_id', {
        clip_id: previewClip.clip_snapshot_id,
        label: previewClip.label,
        preset_id: exportPresetId,
        source: previewClip.is_ai_highlight ? 'ai_highlight' : 'manual',
      })
    } else {
      send('export_clip', {
        room_id: previewClip.room_id,
        start: previewClip.start,
        end: previewClip.end,
        label: previewClip.label,
        preset_id: exportPresetId,
        job_id: jobId,
        source: previewClip.is_ai_highlight ? 'ai_highlight' : 'manual',
        mark_in_wallclock: previewClip.mark_in_wallclock,
        mark_out_wallclock: previewClip.mark_out_wallclock,
        recording_start_mono: previewClip.recording_start_mono,
        recording_media_start_mono: previewClip.recording_media_start_mono,
        content_offset: previewClip.content_offset,
        use_room_marks: false,
      })
    }
    // 将 job_id 写入对应 clip，使 ClipList 能关联导出进度
    pendingExportJobIdsRef.current.add(jobId)
    const store = useAppStore.getState()
    store.setClips(store.clips.map(c =>
      c.start === previewClip.start && c.end === previewClip.end && c.room_id === previewClip.room_id
        ? { ...c, job_id: jobId, exported: false, export_status: 'queued', export_error: undefined }
        : c
    ))
    setPreviewClip(null)
    if (isApproximate) {
      message.warning(
        '该切片为近似定位，导出时间可能偏差数秒；精确导出请用 I / O 键标记。导出任务已提交',
      )
    } else {
      message.info('导出任务已提交')
    }
  }

  const handleCancelExportModal = () => {
    setPreviewClip(null)
  }

  const handleCancelExport = useCallback((jobId: string) => {
    send('cancel_export', { job_id: jobId })
  }, [send])

  // ── 选区试听（A-B 真循环：rAF 听播放头） ──
  const loopPreviewRef = useRef(loopPreview)
  loopPreviewRef.current = loopPreview

  const stopLoopPreview = useCallback(() => {
    setLoopPreview(false)
    loopBoundsRef.current = null
    if (loopRafRef.current != null) {
      cancelAnimationFrame(loopRafRef.current)
      loopRafRef.current = null
    }
  }, [])

  const handleToggleLoop = useCallback(() => {
    if (loopPreviewRef.current) {
      console.log('[Workbench] 用户操作: 停止循环试听')
      stopLoopPreview()
      return
    }

    const state = useAppStore.getState()
    const ctx = state.timelineContext
    const status = getAlignStatus(ctx, state.timelineInvalidated)
    const ids = selectedRoomIds.size > 0
      ? [...selectedRoomIds]
      : (state.selectedRoomId ? [state.selectedRoomId] : [])
    if (ids.length === 0) return

    let markIn: number | null = null
    let markOut: number | null = null
    const useCommon = status === 'ready' && !!ctx
    if (useCommon && commonMarkIn != null && commonMarkOut != null) {
      markIn = commonMarkIn
      markOut = commonMarkOut
    } else {
      const room = state.rooms.find(r => r.room_id === ids[0])
      markIn = room?.mark_in ?? null
      markOut = room?.mark_out ?? null
    }
    if (markIn == null || markOut == null || markIn >= markOut) {
      message.warning('请先设置入点和出点')
      return
    }

    loopBoundsRef.current = { in: markIn, out: markOut, common: useCommon }

    if (useCommon && ctx) {
      ids.forEach(rid => {
        if (!ctx.room_snapshots[rid]) return
        mseSeek(rid, Math.max(0, commonToPreview(ctx, rid, markIn!)))
        ;(window as any).__msePlayers?.[rid]?.player?.resumePlayback?.(true)
      })
    } else {
      ids.forEach(rid => {
        mseSeek(rid, markIn!)
        ;(window as any).__msePlayers?.[rid]?.player?.resumePlayback?.(true)
      })
    }

    setLoopPreview(true)
    console.log('[Workbench] 用户操作: 开始循环试听', { markIn, markOut, rooms: ids.length })

    const tick = () => {
      if (!loopPreviewRef.current) {
        loopRafRef.current = null
        return
      }
      const bounds = loopBoundsRef.current
      if (!bounds) {
        loopRafRef.current = null
        return
      }
      const st = useAppStore.getState()
      const c = st.timelineContext
      const probeId = ids[0]
      let cur: number
      if (bounds.common && c?.room_snapshots[probeId]) {
        cur = previewToCommon(c, probeId, getPreviewCurrentTime(probeId))
      } else {
        cur = getPreviewCurrentTime(probeId)
      }
      if (cur >= bounds.out - 0.04) {
        if (bounds.common && c) {
          ids.forEach(rid => {
            if (!c.room_snapshots[rid]) return
            mseSeek(rid, Math.max(0, commonToPreview(c, rid, bounds.in)))
          })
        } else {
          ids.forEach(rid => mseSeek(rid, bounds.in))
        }
      }
      loopRafRef.current = requestAnimationFrame(tick)
    }
    loopRafRef.current = requestAnimationFrame(tick)
  }, [mseSeek, selectedRoomIds, commonMarkIn, commonMarkOut, getPreviewCurrentTime, stopLoopPreview])

  // Cleanup loop on unmount
  useEffect(() => {
    return () => {
      if (loopRafRef.current != null) cancelAnimationFrame(loopRafRef.current)
    }
  }, [])
  // 同步分析导出启用条件：多选≥2 + 所有选中房间 align_group_id 一致非空 + 有录制文件
  const selectedRoomList = rooms.filter(r => selectedRoomIds.has(r.room_id))
  const currentTargetIds = selectedRoomIds.size > 0
    ? [...selectedRoomIds]
    : (selectedRoomId ? [selectedRoomId] : [])
  const currentTargetRoomList = rooms.filter(r => currentTargetIds.includes(r.room_id))
  const continuousTargetRooms = rooms.filter(r => continuousTargetRoomIds.includes(r.room_id))
  const targetHasRecordings = currentTargetRoomList.every(r => r.record_output_path)
  const targetAlignGroupReady = currentTargetRoomList.length <= 1 || (() => {
    const groups = new Set(currentTargetRoomList.map(r => r.align_group_id || ''))
    return groups.size === 1 && !groups.has('')
  })()
  const analysisEnabled = currentTargetRoomList.length >= 1
    && targetHasRecordings
    && targetAlignGroupReady
  const analysisNeedsAlign = currentTargetRoomList.length >= 1
    && targetHasRecordings
    && !targetAlignGroupReady
  const analysisTooltip = currentTargetRoomList.length < 1
    ? '请先选择要分析的房间'
    : !targetHasRecordings
      ? '选中房间需先有录制文件，请先开始录制'
      : !targetAlignGroupReady
        ? currentTargetRoomList.length > 1
          ? '多房间分析需先点击「一键对齐」，且各房间对齐组一致（仅缓冲区对齐不可用于分析）'
          : '请先点击一键对齐'
        : '分析主直播间高光，按对齐偏移映射导出'

  const scrollToAlignButton = useCallback(() => {
    alignButtonRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    message.info('请点击上方「一键对齐」完成多房间同步')
  }, [])

  // 分析导出确认（持续分析 / 同步分析导出 合并）
  const handleConfirmAnalysisExport = () => {
    if (!continuousMainRoom) {
      message.warning('请选择主直播间')
      return
    }
    const targetRoomIds = continuousTargetRoomIds.length > 0
      ? continuousTargetRoomIds
      : [...selectedRoomIds]
    const targetRooms = rooms.filter(r => targetRoomIds.includes(r.room_id))
    if (targetRooms.length === 0) {
      message.warning('请先选择房间')
      return
    }
    if (!targetRooms.every(r => r.record_output_path)) {
      message.error('选中房间缺少录制文件')
      return
    }
    if (!targetRoomIds.includes(continuousMainRoom)) {
      message.error('主直播间必须在目标房间中')
      return
    }
    if (targetRooms.length > 1) {
      const groupSet = new Set(targetRooms.map(r => r.align_group_id || ''))
      if (groupSet.size !== 1 || groupSet.has('')) {
        message.error('多房间分析需要先一键对齐，且对齐组一致')
        return
      }
    }

    if (analysisIsContinuous) {
      if (targetRoomIds.length < 2 && currentTargetIds.length >= 2) {
        message.error('持续分析目标房间不足，请关闭弹窗后保持多选两间房再打开')
        return
      }
      const mainRoomPreviewEnabled = rooms.find(r => r.room_id === continuousMainRoom)?.preview_enabled ?? false
      send('start_continuous_analysis', {
        main_room_id: continuousMainRoom,
        target_room_ids: targetRoomIds,
        mode: isValorantRoundCutting ? 'valorant_round' : 'scene',
        interval: isValorantRoundCutting ? (mainRoomPreviewEnabled ? 60 : 45) : 60,
        preview_enabled: mainRoomPreviewEnabled,
        threshold: 0.3,
        game: isValorantRoundCutting ? 'valorant' : 'generic',
        valorant_profile: isValorantRoundCutting ? valorantProfile : undefined,
      })
      setContinuousModalOpen(false)
      message.info('持续分析启动请求已发送')
    } else {
      if (targetRoomIds.length < 2) {
        message.warning('请至少选中 2 个房间（或开启持续分析）')
        return
      }
      const jobPrefix = `hlexport-${Date.now()}`
      isSyncExportModeRef.current = true
      syncMainRoomRef.current = continuousMainRoom
      syncTargetRoomIdsRef.current = [...targetRoomIds]
      setContinuousSubmitting(true)
      const analysisMode = isValorantRoundCutting ? 'valorant_round' : 'scene'
      console.log('[Workbench] 用户操作: 多房间同步分析导出, main:', continuousMainRoom,
        'targets:', targetRoomIds, 'mode:', analysisMode)
      send('start_analysis_export', {
        main_room_id: continuousMainRoom,
        target_room_ids: targetRoomIds,
        mode: analysisMode,
        threshold: 0.3,
        whisper_model: 'auto',
        weights: { audio: 0.45, visual: 0.35, scene: 0.20 },

        preset_id: continuousPresetId,
        job_prefix: jobPrefix,
        game: isValorantRoundCutting ? 'valorant' : 'generic',
      })
      setContinuousModalOpen(false)
      setContinuousSubmitting(false)
    }
  }

  // 监听分析结果与进度
  useEffect(() => {
    const unsubs: (() => void)[] = []
    // 同步分析导出响应：后端已自动提交导出，前端预创建 clips 关联 job_id
    unsubs.push(on('start_analysis_export_response', (data: any) => {
      isSyncExportModeRef.current = false
      syncTargetRoomIdsRef.current = []
      if (data?.success && data?.highlights) {
        // 与持续分析一致：切片通过 clip_queued 事件入列（confirm_status=pending），不自动导出
        message.success(`已分析 ${data.highlights.length} 个高光（${data.submitted_count} 个已入列待确认）`)
      } else {
        message.error(data?.error || '同步分析失败')
      }
    }))
    unsubs.push(on('start_continuous_analysis_response', (data: any) => {
      if (data?.success) {
        const roomId = data.main_room_id || continuousMainRoom
        const targetRoomIds = Array.isArray(data.target_room_ids) ? data.target_room_ids : continuousTargetRoomIds
        setContinuousAnalyzing(true)
        setContinuousRoomId(roomId)
        continuousActiveRoomRef.current = roomId
        if (Array.isArray(data.target_room_ids)) {
          setContinuousTargetRoomIds(data.target_room_ids)
        }
        setContinuousAnalysisStatus({
          running: true,
          room_id: roomId,
          target_room_ids: targetRoomIds,
          mode: data.mode || 'scene',
          analyzed_duration: 0,
          total_highlights: 0,
          phase: 'running',
          updated_at: Date.now(),
        })
        message.success('持续分析已启动（快速回合检测），边录边分析新内容')
      } else {
        setContinuousAnalyzing(false)
        setContinuousRoomId(null)
        continuousActiveRoomRef.current = null
        setContinuousAnalysisStatus({
          running: false,
          room_id: null,
          phase: 'error',
          error: data?.error || '持续分析启动失败',
          updated_at: Date.now(),
        })
        message.error(data?.error || '持续分析启动失败')
      }
    }))
    unsubs.push(on('stop_continuous_analysis_response', (data: any) => {
      if (data?.success) {
        setContinuousAnalyzing(false)
        setContinuousRoomId(null)
        continuousActiveRoomRef.current = null
        message.success('持续分析已停止')
      } else {
        message.error(data?.error || '持续分析停止失败')
      }
    }))
    // 录制结束后持续分析收尾完成通知
    unsubs.push(on('continuous_analysis_complete', (data: any) => {
      message.success(`录制结束分析完成：共 ${data?.total_highlights || 0} 个回合`)
      setContinuousAnalyzing(false)
      setContinuousRoomId(null)
      continuousActiveRoomRef.current = null
      setContinuousAnalysisStatus({
        running: false,
        room_id: data?.room_id ?? null,
        total_highlights: data?.total_highlights ?? 0,
        phase: 'completed',
        updated_at: Date.now(),
      })
    }))
    // 刷新房间状态响应
    unsubs.push(on('refresh_room_status', (data: any) => {
      if (data?.success) {
        // 刷新完成后的房间列表已自动更新，无需额外 toast
      }
    }))
    // 持续分析高光更新：仅显示通知，不添加切片到列表
    // 切片由 clip_queued 统一添加（使用映射后时间做 clip_id，避免多房间时 clip_id 不一致导致重复）
    unsubs.push(on('continuous_highlights', (data: any) => {
      if (data?.mapping_fallback) {
        message.warning(
          `副房间映射失败：${data.error || '已回退仅主房间'}。请检查对齐偏移后重试`,
        )
      } else if (
        data?.main_room_id &&
        Array.isArray(data?.target_room_ids) &&
        data.target_room_ids.length > 1 &&
        data?.mapped_highlights_by_room &&
        typeof data.mapped_highlights_by_room === 'object'
      ) {
        const mappedRooms = Object.keys(data.mapped_highlights_by_room)
        if (mappedRooms.length <= 1) {
          message.warning('副房间未映射到切片，请检查对齐偏移后重试')
        }
      }
      const newCount = data?.new_count || 0
      if (newCount > 0) {
        scheduleBatchedToast(
          'continuous_highlights',
          (count, meta) => {
            const total = (meta.total as number | undefined) ?? data?.total ?? 0
            message.success(`持续分析: 新增 ${count} 个回合 (累计 ${total})`)
          },
          800,
          { total: data?.total ?? 0 },
        )
      }
    }))
    return () => unsubs.forEach(u => u())
  }, [on])

  // ── 后端自动导出入队通知：切片添加到列表 ──
  useEffect(() => {
    const unsubs: Array<() => void> = []
    unsubs.push(on('clip_queued', (data: any) => {
      if (!data?.clip_id || !data?.room_id) return
      const st = useAppStore.getState()
      const ctx = st.timelineContext
      const commonReady = getAlignStatus(ctx, st.timelineInvalidated) === 'ready' && !!ctx
      let commonStart: number | undefined
      let commonEnd: number | undefined
      if (commonReady && ctx) {
        try {
          commonStart = previewToCommon(ctx, data.room_id, data.start)
          commonEnd = previewToCommon(ctx, data.room_id, data.end)
        } catch {
          /* keep preview-local coords */
        }
      }
      // 同 room_id+round_key 已存在则 update 边界/状态，不重复 add；upsert 不弹「新切片」
      if (data.round_key) {
        const existing = st.clips.find(
          c => c.room_id === data.room_id && c.round_key === data.round_key
        )
        if (existing) {
          st.setClips(st.clips.map(c => {
            if (c.room_id === data.room_id && c.round_key === data.round_key) {
              return {
                ...c,
                start: data.start,
                end: data.end,
                common_start: commonStart ?? c.common_start,
                common_end: commonEnd ?? c.common_end,
                confirm_status: data.confirm_status ?? c.confirm_status,
                label: data.label || c.label,
                clip_id: data.clip_id || c.clip_id,
                job_id: data.job_id || c.job_id,
              }
            }
            return c
          }))
          return
        }
      }
      // 无 round_key 时按 clip_id 去重；有 round_key 的 upsert 已在上方处理
      if (!data.round_key && st.clips.some(c => c.clip_id === data.clip_id)) return
      if (data.upsert) {
        // 后端标记 upsert 但前端尚无条目（竞态）：静默补一条，不 toast
        st.addClip({
          start: data.start,
          end: data.end,
          common_start: commonStart,
          common_end: commonEnd,
          label: data.label || '高光',
          room_id: data.room_id,
          room_name: data.room_name,
          clip_id: data.clip_id,
          clip_snapshot_id: data.clip_snapshot_id,
          timeline_id: data.timeline_id,
          job_id: data.job_id,
          export_status: data.export_deferred ? 'pending' : 'queued',
          is_ai_highlight: true,
          highlight_reason: data.highlight_reason ?? data.reason,
          highlight_score: data.score,
          mark_precision: data.clip_snapshot_id ? 'exact' : undefined,
          confirm_status: data.confirm_status ?? (data.export_deferred ? 'pending' : undefined),
          round_key: data.round_key,
        })
        return
      }
      st.addClip({
        start: data.start,
        end: data.end,
        common_start: commonStart,
        common_end: commonEnd,
        label: data.label || '高光',
        room_id: data.room_id,
        room_name: data.room_name,
        clip_id: data.clip_id,
        clip_snapshot_id: data.clip_snapshot_id,
        timeline_id: data.timeline_id,
        job_id: data.job_id,
        export_status: data.export_deferred ? 'pending' : 'queued',
        is_ai_highlight: true,
        highlight_reason: data.highlight_reason ?? data.reason,
        highlight_score: data.score,
        mark_precision: data.clip_snapshot_id ? 'exact' : undefined,
        confirm_status: data.confirm_status ?? (data.export_deferred ? 'pending' : undefined),
        round_key: data.round_key,
      })
      scheduleBatchedToast(
        'clip_queued',
        (count) => {
          const latest = useAppStore.getState()
          const totalClips = latest.clips.length
          const pendingCount = latest.clips.filter(c => c.confirm_status === 'pending').length
          if (pendingCount > 0) {
            message.success(`新增 ${count} 个回合（累计 ${totalClips} 个，${pendingCount} 个待调整）`, 3)
          } else if (count === 1) {
            message.success(`[AI] ${data.label || '回合'} · ${formatTime(data.end - data.start)}`, 3)
          } else {
            message.success(`[AI] 新增 ${count} 个高光回合`, 3)
          }
        },
        800,
        { label: data.label, duration: data.end - data.start },
      )
    }))
    // 精修状态更新（refining / user_confirmed / ocr_confirmed）；目标房缺条目时 upsert
    unsubs.push(on('clip_confirm_status', (data: any) => {
      if (!data?.room_id || !data?.round_key) return
      const st = useAppStore.getState()
      const existing = st.clips.find(
        c => c.room_id === data.room_id && c.round_key === data.round_key
      )
      if (existing) {
        st.setClips(st.clips.map(c => {
          if (c.room_id === data.room_id && c.round_key === data.round_key) {
            return {
              ...c,
              confirm_status: data.confirm_status,
              ...(data.start != null ? { start: data.start } : {}),
              ...(data.end != null ? { end: data.end } : {}),
              ...(data.label ? { label: data.label } : {}),
            }
          }
          return c
        }))
        if (data.confirm_status === 'pending' && data.start != null && data.end != null) {
          send('set_mark_in', { room_id: data.room_id, time: data.start, live: false })
          send('set_mark_out', { room_id: data.room_id, time: data.end, live: false })
        }
        return
      }
      // 多房确认同步：目标房尚无同 round_key 条目则补一条
      if (data.confirm_status === 'user_confirmed' || data.confirm_status === 'ocr_confirmed') {
        const start = typeof data.start === 'number' ? data.start : 0
        const end = typeof data.end === 'number' ? data.end : start
        const room = st.rooms.find(r => r.room_id === data.room_id)
        st.addClip({
          start,
          end,
          label: data.label || '高光',
          room_id: data.room_id,
          room_name: room?.streamer_name || room?.stream_title || data.room_id,
          clip_id: data.clip_id || `${data.room_id}-${data.round_key}`,
          is_ai_highlight: true,
          confirm_status: data.confirm_status,
          round_key: data.round_key,
          export_status: 'pending',
        })
      }
    }))
    unsubs.push(on('clip_export_started', (data: any) => {
      if (!data?.clip_id && !data?.job_id) return
      const st = useAppStore.getState()
      st.setClips(st.clips.map(c => {
        if ((data.clip_id && c.clip_id === data.clip_id) || (data.job_id && c.job_id === data.job_id)) {
          return { ...c, export_status: 'queued' as const, job_id: data.job_id || c.job_id }
        }
        return c
      }))
    }))
    return () => unsubs.forEach(u => u())
  }, [on])

  // ── 导出文件操作 ──
  const handleOpenExportFolder = (outputPath: string) => {
    console.log('[Workbench] 用户操作: 打开导出文件夹, path:', outputPath)
    if (window.electronAPI) {
      // 优先用 showItemInFolder 在资源管理器中高亮定位文件，
      // 避免 openPath 用默认播放器打开 .mp4（与"打开文件夹"按钮语义不符）。
      if (window.electronAPI.showItemInFolder) {
        window.electronAPI.showItemInFolder(outputPath)
      } else {
        window.electronAPI.openPath(outputPath)
      }
    }
  }

  // 点击切片：选中房间并跳转到入点
  // 多选时取第一个选中房间作为时间线代表，确保时间线正常显示
  const selectedRoom = rooms.find(r => r.room_id === selectedRoomId)
    || (selectedRoomIds.size > 0 ? rooms.find(r => r.room_id === [...selectedRoomIds][0]) : undefined)

  // 本地模式也维护直播沿（只增不减）
  {
    const previewT = previewPositions[selectedRoom?.room_id ?? ''] ?? 0
    let localEnd = previewT
    if (selectedRoom?.mark_out != null && selectedRoom.mark_out > localEnd) localEnd = selectedRoom.mark_out
    if (selectedRoom?.mark_in != null && selectedRoom.mark_in > localEnd) localEnd = selectedRoom.mark_in
    const end = Math.max(timelineView?.duration ?? 0, localEnd, 1)
    lastContentEndRef.current = Math.max(lastContentEndRef.current, end)
  }

  const activeRefineRange = useMemo(() => {
    if (!refiningClipId) return null
    const ctx = timelineContext
    const toDisplay = (localStart: number, localEnd: number, roomId?: string | null) => {
      if (commonMode && ctx && roomId && ctx.room_snapshots[roomId]) {
        try {
          return {
            start: previewToCommon(ctx, roomId, localStart),
            end: previewToCommon(ctx, roomId, localEnd),
          }
        } catch {
          /* fallthrough */
        }
      }
      return { start: localStart, end: localEnd }
    }

    if (localDragMark) {
      let start = commonMarkIn
      let end = commonMarkOut
      if (start == null || end == null) {
        const room = selectedRoom
        if (room?.mark_in != null && room?.mark_out != null) {
          const mapped = toDisplay(room.mark_in, room.mark_out, room.room_id)
          start = start ?? mapped.start
          end = end ?? mapped.end
        }
      }
      if (localDragMark.type === 'in') start = localDragMark.time
      else end = localDragMark.time
      if (start != null && end != null && end > start) return { start, end }
    }
    if (commonMarkIn != null && commonMarkOut != null && commonMarkOut > commonMarkIn) {
      return { start: commonMarkIn, end: commonMarkOut }
    }
    if (selectedRoom?.mark_in != null && selectedRoom?.mark_out != null
      && selectedRoom.mark_out > selectedRoom.mark_in) {
      return toDisplay(selectedRoom.mark_in, selectedRoom.mark_out, selectedRoom.room_id)
    }
    const clip = clips.find(c => c.round_key === refiningClipId || c.clip_id === refiningClipId)
    if (clip && clip.end > clip.start) {
      if (clip.common_start != null && clip.common_end != null) {
        return { start: clip.common_start, end: clip.common_end }
      }
      return toDisplay(clip.start, clip.end, clip.room_id)
    }
    return null
  }, [
    refiningClipId, localDragMark, commonMarkIn, commonMarkOut, selectedRoom, clips,
    commonMode, timelineContext,
  ])

  const recordedDurationHint = useMemo(() => {
    return computeRecordedDurationHint(selectedRoom, continuousAnalysisStatus?.recorded_duration)
  }, [continuousAnalysisStatus?.recorded_duration, selectedRoom?.is_recording, selectedRoom?.record_started_at, timelineTick])

  const exportSummary = useMemo(() => clips.reduce((summary, clip) => {
    const status = clip.export_status ?? (clip.exported ? 'completed' : undefined)
    if (status === 'pending') summary.queued += 1
    else if (status === 'queued' || status === 'exporting' || status === 'completed' || status === 'failed') summary[status] += 1
    return summary
  }, { queued: 0, exporting: 0, completed: 0, failed: 0 }), [clips])

  // Sort rooms (memoize to prevent new array reference on every render)
  const sortedRooms = useMemo(() => [...rooms].sort((a, b) => {
    switch (sortBy) {
      case 'status':
        return (a.is_recording ? -1 : 1) - (b.is_recording ? -1 : 1) ||
               (a.is_connected ? -1 : 1) - (b.is_connected ? -1 : 1)
      case 'platform':
        return (a.platform_name || '').localeCompare(b.platform_name || '')
      case 'name':
        return (a.streamer_name || '').localeCompare(b.streamer_name || '')
      default:
        return 0
    }
  }), [rooms, sortBy])

  // ── 工作区全局快捷键 ──
  const handleWorkbenchShortcut = useCallback(
    (id: string) => {
      const firstSelectedId = selectedRoomIds.size === 1 ? [...selectedRoomIds][0] : selectedRoomId

      const noRoomNeeded = ['batch:record', 'batch:stop', 'select:all', 'export:clip']
      if (!firstSelectedId && !noRoomNeeded.includes(id)) {
        message.info('请先选择房间')
        return
      }

      switch (id) {
        case 'play:toggle':
          selectedRoomIds.forEach(rid => mseTogglePlayPause(rid))
          break
        case 'seek:back-1':
          handleSeekByDelta(-1)
          break
        case 'seek:fwd-1':
          handleSeekByDelta(1)
          break
        case 'seek:back-fine':
          handleSeekByDelta(-0.2)
          break
        case 'seek:fwd-fine':
          handleSeekByDelta(0.2)
          break
        case 'seek:back-2':
          handleSeekByDelta(-2)
          break
        case 'seek:fwd-2':
          handleSeekByDelta(2)
          break
        case 'mark:nudge-out-back':
          handleNudgeMark('out', -0.5)
          break
        case 'mark:nudge-out-fwd':
          handleNudgeMark('out', 0.5)
          break
        case 'mark:nudge-in-back':
          handleNudgeMark('in', -0.5)
          break
        case 'mark:nudge-in-fwd':
          handleNudgeMark('in', 0.5)
          break
        case 'rate:cycle-down':
          handleCyclePlaybackRate(-1)
          break
        case 'rate:cycle-up':
          handleCyclePlaybackRate(1)
          break
        case 'mark:in':
          handleControlMarkIn()
          break
        case 'mark:out':
          handleControlMarkOut()
          break
        case 'record:toggle': {
          // Iterate each selected room individually to avoid mixed-state issues
          const toStop: string[] = []
          const toStart: string[] = []
          selectedRoomIds.forEach(rid => {
            const r = rooms.find(r2 => r2.room_id === rid)
            if (!r) return
            if (r.is_recording) {
              toStop.push(rid)
            } else if (r.is_connected) {
              toStart.push(rid)
            }
          })
          toStart.forEach(rid => handleStartRecord(rid))
          if (toStop.length === 1) {
            const r = rooms.find(r2 => r2.room_id === toStop[0])
            const ca = useAppStore.getState().continuousAnalysisStatus
            const analyzing = Boolean(ca?.running)
            confirmStopRecording(
              '确认停止录制',
              analyzing
                ? `将停止录制「${r?.streamer_name || '未知主播'}」。请先结束录制，再等待持续分析收尾完成（确认回合并导出），不要立刻关闭分析。`
                : `将停止录制「${r?.streamer_name || '未知主播'}」`,
              () => handleStopRecord(toStop[0]),
            )
          } else if (toStop.length > 1) {
            const ca = useAppStore.getState().continuousAnalysisStatus
            const analyzing = Boolean(ca?.running)
            confirmStopRecording(
              '确认停止录制',
              analyzing
                ? `将停止 ${toStop.length} 个房间的录制。请等待持续分析收尾完成后再关闭分析。`
                : `将停止 ${toStop.length} 个房间的录制`,
              () => toStop.forEach(rid => handleStopRecord(rid)),
            )
          }
          break
        }
        case 'mute:toggle':
          selectedRoomIds.forEach(rid => handleToggleMute(rid))
          break
        case 'fullscreen':
          if (firstSelectedId) handleFullscreen(firstSelectedId)
          break
        case 'batch:record':
          handleBatchRecord()
          break
        case 'batch:stop':
          handleBatchStop()
          break
        case 'select:all':
          if (rooms.length > 0) {
            setSelectedRoomIds(new Set(rooms.map(r => r.room_id)))
            message.info(`已选中 ${rooms.length} 个房间`)
          }
          break
        case 'export:clip': {
          // 优先级：勾选可导项 → refining 条 → 第一条可导 → toast
          const exportableClips = clips.filter((c, i) => {
            if (!clipSelectedIndices.has(i)) return false
            const room = rooms.find(r => r.room_id === c.room_id)
            return !!room?.record_output_path && canExportForShortcut(c)
          })
          if (exportableClips.length > 0) {
            handleExportMany(exportableClips)
            break
          }
          const refiningClip = refiningClipId
            ? clips.find(c => c.round_key === refiningClipId || c.clip_id === refiningClipId)
            : undefined
          if (refiningClip) {
            const room = rooms.find(r => r.room_id === refiningClip.room_id)
            if (room?.record_output_path) {
              handleExportClip(refiningClip)
              break
            }
          }
          const firstExportable = clips.find(c => {
            const room = rooms.find(r => r.room_id === c.room_id)
            return !!room?.record_output_path && canExportForShortcut(c)
          })
          if (firstExportable) {
            handleExportClip(firstExportable)
          } else if (clips.length > 0) {
            message.info('没有可导出的切片（缺少录制文件或未确认）')
          } else {
            message.info('切片列表为空')
          }
          break
        }
      }
    },
    [selectedRoomIds, selectedRoomId, send, rooms, clips, handleControlMarkIn, handleControlMarkOut, mseTogglePlayPause, handleStartRecord, handleStopRecord, handleToggleMute, handleFullscreen, handleBatchRecord, handleBatchStop, handleExportClip, handleSeekByDelta, handleNudgeMark, handleCyclePlaybackRate]
  )

  useKeyboardShortcuts(
    [
      { key: ' ',                                   id: 'play:toggle' },
      { key: 'k',                                   id: 'play:toggle' },
      { key: 'i',                                   id: 'mark:in' },
      { key: 'o',                                   id: 'mark:out' },
      { key: 'ArrowLeft',                           id: 'seek:back-1' },
      { key: 'ArrowRight',                          id: 'seek:fwd-1' },
      { key: ',', shift: false,                     id: 'seek:back-fine' },
      { key: '.', shift: false,                     id: 'seek:fwd-fine' },
      { key: 'j',                                   id: 'seek:back-2' },
      { key: 'l',                                   id: 'seek:fwd-2' },
      { key: '[', shift: false,                     id: 'mark:nudge-out-back' },
      { key: ']', shift: false,                     id: 'mark:nudge-out-fwd' },
      { key: '{',                                   id: 'mark:nudge-in-back' },
      { key: '}',                                   id: 'mark:nudge-in-fwd' },
      { key: '<',                                   id: 'rate:cycle-down' },
      { key: '>',                                   id: 'rate:cycle-up' },
      { key: 'r', ctrl: false,        preventDefault: false, id: 'record:toggle' },
      { key: 'm',                                   id: 'mute:toggle' },
      { key: 'f',                                   id: 'fullscreen' },
      { key: 'r', ctrl: true,                       id: 'batch:record' },
      { key: 'r', ctrl: true, shift: true,          id: 'batch:stop' },
      { key: 'a', ctrl: true, shift: true,          id: 'select:all' },
      { key: 'e', ctrl: true,                       id: 'export:clip' },
    ],
    handleWorkbenchShortcut
  )

  // R 键按下的场景：仅在非输入框焦点时触发作录制/停止切换，且不吞掉默认行为（输入框需要 R）

  const batchRecordDisabled = !rooms.some(r => r.is_connected && !r.is_recording)
  const batchRecordTooltip = batchRecordDisabled
    ? (rooms.length === 0
        ? '没有可录制的房间'
        : !rooms.some(r => r.is_connected)
          ? '没有已连接的房间'
          : '所有已连接房间已在录制中')
    : undefined

  const batchStopDisabled = !rooms.some(r => r.is_recording)
  const batchStopTooltip = batchStopDisabled ? '没有正在录制的房间' : undefined

  // ── 刷新按钮回调 ──
  const handleRefreshShortClick = useCallback(() => {
    send('refresh_room_status', {})
    const currentRooms = useAppStore.getState().rooms
    currentRooms.forEach(r => {
      if (r.preview_enabled && r.is_connected) {
        send('enable_preview', { room_id: r.room_id, enabled: false, mode: 'mse' })
        setTimeout(() => {
          send('enable_preview', { room_id: r.room_id, enabled: true, mode: 'mse' })
        }, 300)
      }
    })
    message.info('正在刷新房间状态和预览...')
  }, [send])

  const handleRefreshLongPress = useCallback(() => {
    Modal.confirm({
      title: '确认刷新全部',
      content: '将停止全部房间的录制、预览与分析，然后重启预览',
      okText: '确认',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: () => {
        const currentRooms = useAppStore.getState().rooms
        message.info('长按刷新全部：正在停止所有录制、预览和分析...')
        currentRooms.forEach(r => {
          if (r.is_recording) send('stop_recording', { room_id: r.room_id })
          if (r.preview_enabled) send('enable_preview', { room_id: r.room_id, enabled: false, mode: 'mse' })
        })
        const currentAnalysisStatus = useAppStore.getState().continuousAnalysisStatus
        if (currentAnalysisStatus?.running && currentAnalysisStatus.room_id) {
          send('stop_continuous_analysis', { main_room_id: currentAnalysisStatus.room_id })
        }
        // 1.5 秒后重启连接和预览
        setTimeout(() => {
          send('refresh_room_status', {})
          const freshRooms = useAppStore.getState().rooms
          freshRooms.forEach(r => {
            if (r.is_connected) {
              setTimeout(() => {
                send('enable_preview', { room_id: r.room_id, enabled: true, mode: 'mse' })
              }, 500)
            }
          })
          message.success('刷新全部完成：所有房间已重启')
        }, 1500)
      },
    })
  }, [send])

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      {/* WebSocket 连接状态提示（防抖：仅在断开超过 2 秒后显示） */}
      {showDisconnectAlert && (
        <Alert
          type="error"
          message="WebSocket 连接断开，正在重连..."
          banner
          showIcon
        />
      )}
      {previewDegradationBanner && (
        <Alert
          type="info"
          banner
          showIcon
          closable
          onClose={dismissPreviewDegradationBanner}
          message={`多路预览已降为 ${formatPreviewDegradationLabel(
            previewDegradationBanner.width,
            previewDegradationBanner.height,
            previewDegradationBanner.fps,
          )} 以保流畅`}
          description={previewDegradationBanner.reason}
        />
      )}
      {/* 顶部操作栏 */}
      <div style={{ 
        padding: '16px 24px',
        background: 'var(--bg-secondary)',
        borderBottom: '1px solid var(--border-default)',
        display: 'flex',
        flexDirection: 'column',
        gap: 12,
      }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12 }}>
          <Space>
            <h2 style={{ margin: 0, fontSize: 18, fontWeight: 600 }}>多房间工作台</h2>
            <span style={{ color: 'var(--text-tertiary)', fontSize: 13 }}>
              {rooms.length} 个房间
            </span>
            <Select
              size="small"
              value={sortBy}
              onChange={setSortBy}
              style={{ width: 110, marginLeft: 8, fontSize: 12 }}
              options={[
                { value: 'default', label: '默认排序' },
                { value: 'status', label: '按状态' },
                { value: 'platform', label: '按平台' },
                { value: 'name', label: '按名称' },
              ]}
            />
            <Tooltip title={continuousAnalyzing ? '停止持续分析' : analysisTooltip}>
              <span>
                <Button
                  size="small"
                  type={continuousAnalyzing ? 'primary' : 'default'}
                  disabled={!continuousAnalyzing && !analysisEnabled}
                  onClick={() => {
                    if (!continuousAnalyzing && !analysisEnabled) {
                      if (currentTargetRoomList.length < 1) return
                      if (!targetHasRecordings) {
                        message.warning('请先开始录制')
                        return
                      }
                      if (!targetAlignGroupReady) {
                        scrollToAlignButton()
                        return
                      }
                      return
                    }
                    if (continuousAnalyzing) {
                      const activeRoomId = continuousActiveRoomRef.current || continuousRoomId || undefined
                      send('stop_continuous_analysis', { main_room_id: activeRoomId })
                      setContinuousModalOpen(false)
                      // 后端响应成功后会通过 stop_continuous_analysis_response 提示
                    } else {
                      const targetRoomIds = currentTargetIds
                      if (targetRoomIds.length === 0) return
                      setContinuousTargetRoomIds(targetRoomIds)
                      setContinuousMainRoom(targetRoomIds[0])
                      setContinuousPresetId(getDefaultPreset().id)
                      setAnalysisIsContinuous(false)
                      setContinuousModalOpen(true)
                    }
                  }}
                >
                  {continuousAnalyzing ? '停止持续分析' : '分析导出'}
                </Button>
              </span>
            </Tooltip>
            {analysisNeedsAlign && (
              <Button type="link" size="small" onClick={scrollToAlignButton} style={{ padding: '0 4px' }}>
                去对齐
              </Button>
            )}
          </Space>
          <Space wrap>
            <Button
              size="small"
              onClick={() => {
                if (rooms.length > 0) {
                  setSelectedRoomIds(new Set(rooms.map(r => r.room_id)))
                  message.info(`已选中 ${rooms.length} 个房间`)
                }
              }}
              disabled={rooms.length === 0}
            >
              全选
            </Button>
            <Button
              ref={alignButtonRef}
              size="small"
              icon={<SyncOutlined spin={aligning} />}
              onClick={handleAlignLive}
              loading={aligning}
              disabled={aligning || selectedRoomIds.size === 0}
            >
              {aligning ? '对齐中...' : '一键对齐'}
            </Button>

            <RefreshButton
              onShortClick={handleRefreshShortClick}
              onLongPress={handleRefreshLongPress}
            />

            <Button
              size="small"
              type={allMuted ? 'primary' : 'default'}
              icon={allMuted ? <MutedOutlined /> : <SoundOutlined />}
              onClick={() => {
                const newMuted = !allMuted
                setAllMuted(newMuted)
                rooms.forEach(r => {
                  if (r.preview_enabled) {
                    useAppStore.getState().updateRoom(r.room_id, { preview_muted: newMuted })
                    send('set_preview_muted', { room_id: r.room_id, muted: newMuted })
                  }
                })
              }}
              disabled={rooms.length === 0}
            >
              {allMuted ? '取消静音' : '静音'}
            </Button>
            <Tooltip title={batchRecordTooltip}>
              <span>
                <Button
                  type="primary"
                  icon={<VideoCameraOutlined />}
                  onClick={handleBatchRecord}
                  disabled={batchRecordDisabled}
                >
                  批量录制
                </Button>
              </span>
            </Tooltip>
            <Tooltip title={batchStopTooltip}>
              <span>
                <Button
                  danger
                  onClick={handleBatchStop}
                  disabled={batchStopDisabled}
                >
                  批量停止
                </Button>
              </span>
            </Tooltip>
          </Space>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
          <AnalysisProgress status={continuousAnalysisStatus} compact exportSummary={exportSummary} />
        </div>
      </div>

      {refiningClipId && (() => {
        const alignS = getAlignStatus(timelineContext, timelineInvalidated)
        const axisLabel = alignS === 'ready' ? '公共时间轴' : '预览时间轴'
        return (
          <div style={{ padding: '6px 24px', background: 'rgba(250, 173, 20, 0.08)', borderBottom: '1px solid rgba(250, 173, 20, 0.25)', fontSize: 12 }}>
            <span style={{ color: '#854F0B' }}>精修模式：当前为{axisLabel}，与录制文件可能有 2–5 秒延迟。拖拽标记请使用 I/O 键精确定位，确认前可预览入点前后各 1.5 秒。</span>
          </div>
        )
      })()}

      {/* 主内容区 */}
      <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>
        {/* 左侧：房间卡片 + 控制栏 */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>

          {/* 房间卡片网格 — 放大通过 CSS position:fixed 在 RoomCard 内部实现，不销毁实例 */}
          <div
            className="workbench-room-scroll"
            style={{ flex: 1, minHeight: 0, overflowY: 'auto', overflowX: 'hidden', padding: '16px 24px' }}
          >
            {rooms.length === 0 ? (
              <Empty
                description="暂无房间，请添加直播间地址"
                style={{ marginTop: 100 }}
              />
            ) : (
              <Row gutter={[16, 16]}>
                {sortedRooms.map(room => {
                  const isExpanded = expandedRoomId === room.room_id && !fullscreenRoomId
                  return (
                    <Col key={room.room_id} xs={24} sm={isExpanded ? 24 : 12} lg={isExpanded ? 24 : 12} xl={isExpanded ? 24 : 8}>
                      <RoomCard
                        room={room}
                        send={send}
                        selected={selectedRoomIds.has(room.room_id)}
                        multiSelected={selectedRoomIds.size > 1 && selectedRoomIds.has(room.room_id)}
                        onSelect={handleSelect}
                        onConnect={handleConnect}
                        onDisconnect={handleDisconnect}
                        onStartRecord={handleStartRecord}
                        onStopRecord={handleStopRecord}
                        onRemove={handleRemove}
                        onTogglePreview={handleTogglePreview}
                        onToggleMute={handleToggleMute}
                        onFullscreen={handleFullscreen}
                        onToggleMultiSelect={handleToggleMultiSelect}
                        expandedRoomId={expandedRoomId}
                        fullscreenRoomId={fullscreenRoomId}
                        onCollapse={handleCollapse}
                        onExitFullscreen={handleExitFullscreen}
                      />
                    </Col>
                  )
                })}
              </Row>
            )}
          </div>

          {/* 底部控制栏 */}
          <ControlBar
            room={selectedRoom}
            multiSelectCount={selectedRoomIds.size > 1 ? selectedRoomIds.size : 0}
            loopPreview={loopPreview}
            clips={clips}
            previewPos={previewPositions[selectedRoom?.room_id ?? ''] ?? 0}
            followLive={timelineFollowLive}
            isScrubbing={timelineScrubbing}
            frozenWindowStart={frozenWindowStart}
            alignStatus={alignStatus}
            timelineView={timelineView}
            onSeek={handleTimelineSeek}
            onScrubStart={handleTimelineScrubStart}
            onScrubEnd={handleTimelineScrubEnd}
            onPlayPause={handleControlPlayPause}
            onSeekBack={handleControlSeekBack}
            onSeekFwd={handleControlSeekFwd}
            playbackRate={playbackRate}
            onPlaybackRateChange={handleSetPlaybackRate}
            onMarkIn={handleControlMarkIn}
            onMarkOut={handleControlMarkOut}
            onAddClip={handleControlAddClip}
            onToggleLoop={handleToggleLoop}
            onGoLive={handleGoLive}
            zoomLevel={timelineZoom}
            onZoomChange={setTimelineZoom}
            onMarkerDrag={handleMarkerDrag}
            onMarkerDragEnd={handleMarkerDragEnd}
            onDeleteMarker={handleDeleteMarker}
            localDragMark={localDragMark}
            activeRefine={activeRefineRange}
            recordedDurationHint={recordedDurationHint}
            dvrStart={dvrStart}
            onHighlightClick={(h) => {
              setCommonMarkIn(h.start)
              setCommonMarkOut(h.end)
              handleTimelineSeek(h.start)
            }}
          />
        </div>

        {/* 右侧面板 */}
        <div style={{ 
          width: 320,
          borderLeft: '1px solid var(--border-default)',
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
        }}>
          {/* 添加直播间 */}
          <Card 
            size="small" 
            title="添加直播间"
            style={{ 
              margin: 16, 
              marginBottom: 8,
              background: 'var(--bg-secondary)',
            }}
          >
            <Space.Compact style={{ width: '100%' }}>
              <Input
                placeholder="粘贴直播间链接..."
                value={url}
                onChange={e => setUrl(e.target.value)}
                onPressEnter={handleAddRoom}
              />
              <Button 
                type="primary" 
                icon={<PlusOutlined />}
                onClick={handleAddRoom}
                loading={loading}
              >
                添加
              </Button>
            </Space.Compact>
          </Card>

          {/* 切片列表 */}
          <ClipList
            clips={clips}
            onDelete={handleDeleteClip}
            onExport={handleExportClip}
            onExportMany={handleExportMany}
            onOpenFile={handleOpenExportFile}
            onOpenFolder={handleOpenExportFolder}
            onCancelExport={handleCancelExport}
            exportProgress={exportProgressMap}
            onSelectClip={handleSelectClip}
            onConfirmClip={handleConfirmClip}
            onConfirmAndExport={handleConfirmAndExport}
            refiningClipId={refiningClipId}
            selectedIndices={clipSelectedIndices}
            onSelectedIndicesChange={setClipSelectedIndices}
          />
        </div>
      </div>

      {/* 导出预览弹窗 */}
      <Modal
        title="导出切片预览"
        open={!!previewClip}
        onCancel={handleCancelExportModal}
        footer={[
          <Button key="cancel" onClick={handleCancelExportModal}>取消</Button>,
          <Button key="export" type="primary" onClick={handleConfirmExport}>确认导出</Button>,
        ]}
      >
        {previewClip && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div>
              <strong>房间：</strong>
              {previewClip.room_name || rooms.find(r => r.room_id === previewClip.room_id)?.streamer_name || '未知房间'}
            </div>
            <div><strong>入点：</strong>{formatTime(previewClip.start)}</div>
            <div><strong>出点：</strong>{formatTime(previewClip.end)}</div>
            <div><strong>时长：</strong>{formatTime(previewClip.end - previewClip.start)}</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <strong>导出预设：</strong>
              <Select
                value={exportPresetId}
                onChange={setExportPresetId}
                style={{ flex: 1, fontSize: 13 }}
                options={EXPORT_PRESETS.map(p => ({
                  value: p.id,
                  label: `${p.name} — ${p.description}`,
                }))}
              />
            </div>
          </div>
        )}
      </Modal>

      {/* 分析导出 Modal（持续分析 + 同步分析导出合并） */}
      <Modal
        title={analysisIsContinuous ? '持续分析设置' : '多房间同步分析导出'}
        open={continuousModalOpen}
        onCancel={() => setContinuousModalOpen(false)}
        width={520}
        footer={[
          <Button key="cancel" onClick={() => setContinuousModalOpen(false)}>取消</Button>,
          <Button
            key="confirm"
            type="primary"
            loading={continuousSubmitting}
            disabled={!continuousMainRoom || (!analysisIsContinuous && selectedRoomList.length > 1 && !targetAlignGroupReady)}
            onClick={handleConfirmAnalysisExport}
          >
            {analysisIsContinuous ? '开始持续分析' : '开始分析与导出'}
          </Button>,
        ]}
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          {!analysisIsContinuous && selectedRoomList.length > 1 && !targetAlignGroupReady && (
            <div style={{
              padding: '8px 12px', borderRadius: 6,
              background: 'rgba(255, 153, 10, 0.15)', fontSize: 12, color: 'var(--state-warning-dark)',
              border: '1px solid var(--state-warning-dark)',
            }}>
              ⚠ 多房间分析导出需要先点击「一键对齐」，否则各房间切片无法同步对齐
            </div>
          )}
          <div style={{
            padding: '8px 12px', borderRadius: 6,
            background: 'var(--bg-tertiary)', fontSize: 12, color: 'var(--text-secondary)',
          }}>
            {analysisIsContinuous
              ? `边录边分析主直播间高光，自动同步导入所有目标房间的切片列表（目标 ${continuousTargetRooms.length} 间）。`
              : `分析主直播间高光，按 content_offset 映射到所有目标房间导出。目标 ${continuousTargetRooms.length} 个房间。`}
          </div>
          <div>
            <div style={{ fontWeight: 600, marginBottom: 8 }}>主直播间（用于高光分析）</div>
            <Radio.Group
              value={continuousMainRoom}
              onChange={(e) => setContinuousMainRoom(e.target.value)}
              style={{ display: 'flex', flexDirection: 'column', gap: 6 }}
            >
              {continuousTargetRooms.map(r => (
                <Radio key={r.room_id} value={r.room_id}>
                  {r.streamer_name || r.room_id}
                  {!r.record_output_path && <span style={{ color: 'var(--state-error)', marginLeft: 8 }}>（无录制文件）</span>}
                </Radio>
              ))}
            </Radio.Group>
          </div>
          <div>
            <div style={{ fontWeight: 600, marginBottom: 8 }}>直播类型</div>
            <Radio.Group
              value={analysisGameType}
              onChange={(e) => setAnalysisGameType(e.target.value)}
              optionType="button"
              buttonStyle="solid"
            >
              <Radio.Button value="valorant_round">无畏契约回合切割</Radio.Button>
              <Radio.Button value="generic">通用直播</Radio.Button>
            </Radio.Group>
          </div>

          {isValorantRoundCutting && analysisIsContinuous && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <strong>视角类型：</strong>
              <Radio.Group
                value={valorantProfile}
                onChange={(e) => setValorantProfile(e.target.value)}
                optionType="button"
                buttonStyle="solid"
                size="small"
              >
                <Radio.Button value="pov">游戏视角</Radio.Button>
                <Radio.Button value="broadcast">赛事解说</Radio.Button>
              </Radio.Group>
            </div>
          )}

          {!analysisIsContinuous && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <strong>导出预设：</strong>
              <Select
                value={continuousPresetId}
                onChange={setContinuousPresetId}
                style={{ flex: 1, fontSize: 13 }}
                options={EXPORT_PRESETS.map(p => ({ value: p.id, label: `${p.name} — ${p.description}` }))}
              />
            </div>
          )}
          <div style={{
            display: 'flex', alignItems: 'center', gap: 8,
            padding: '8px 12px', borderRadius: 6,
            background: 'var(--bg-tertiary)',
          }}>
            <strong>持续分析</strong>
            <Switch
              checked={analysisIsContinuous}
              onChange={(checked) => {
                setAnalysisIsContinuous(checked)
              }}
              disabled={continuousAnalyzing}
            />
            <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
              {analysisIsContinuous
                ? '边录边分析。结束时请先停录，再等状态变为「收尾中/已完成」后再关分析；未确认回合会在收尾 OCR 后再导出。'
                : '关闭则为单次分析并导出所有选中房间'}
            </span>
          </div>
        </div>
      </Modal>
    </div>
  )
}
