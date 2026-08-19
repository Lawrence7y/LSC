import { useState, useEffect, useMemo, memo, useRef } from 'react'
import { Card, Button, Tooltip, Modal, Select } from 'antd'
import {
  PlayCircleOutlined,
  PauseCircleOutlined,
  StepBackwardOutlined,
  StepForwardOutlined,
  DeleteOutlined,
  LinkOutlined,
  DisconnectOutlined,
  VideoCameraOutlined,
  StopOutlined,
  SoundOutlined,
  MutedOutlined,
  FullscreenOutlined,
  FullscreenExitOutlined,
  ShrinkOutlined,
} from '@ant-design/icons'
import { RoomSession } from '@/types'
import { VideoPreview } from '@/components/VideoPreview'
import { formatTime } from '@/utils/time'
import { computeExpandedPreviewWindow } from '@/utils/timelineWindow'
import { isNoDvrPreviewMode } from '@/utils/timelineCoords'
import { readPlayhead, retainClockLoop, subscribeClock } from '@/utils/playheadStore'
import { useAppStore } from '@/store/appStore'
import { useI18n, t } from '@/i18n'

function openCredentialSettings(e: React.MouseEvent) {
  e.stopPropagation()
  useAppStore.getState().setSettingsDrawerOpen(true)
}

/** 根据统一 pipeline health 提示任何平台的凭据失效。 */
function isCredentialError(room: RoomSession): boolean {
  const health = room.pipeline_health
  const failureKind = String(health?.failure_kind || '').toUpperCase()
  if (failureKind === 'AUTH_REQUIRED' || failureKind === 'AUTH_EXPIRED') return true
  const credentialStatus = String(health?.credential_status || '').toUpperCase()
  if (
    ['NOT_CONFIGURED', 'INVALID', 'EXPIRED', 'INTERACTION_REQUIRED'].includes(credentialStatus) &&
    (health?.credential_kinds || []).length > 0
  ) return true
  const text = `${room.last_error || ''} ${room.mse_error || ''}`.toLowerCase()
  if (!text.trim()) return false
  return (
    text.includes('cookie') ||
    text.includes('验证中间页') ||
    text.includes('验证码') ||
    text.includes('login required') ||
    text.includes('auth_required') ||
    text.includes('auth expired') ||
    text.includes('需要登录') ||
    text.includes('登录态已过期')
  )
}

function credentialSettingsAvailable(room: RoomSession): boolean {
  // The capability declaration, rather than a platform-name branch, owns
  // whether the configured Cookie/credential provider has a settings surface.
  return (room.pipeline_health?.credential_kinds || []).length > 0
}

function credentialErrorTitle(room: RoomSession): string {
  const label = room.platform_name || room.platform || t('平台')
  return t('需要{label}凭据', { label })
}

function readMseBuffered(video: HTMLVideoElement | undefined | null): { start: number; end: number } {
  try {
    const ranges = video?.buffered
    if (ranges && ranges.length > 0) {
      return { start: ranges.start(0), end: ranges.end(ranges.length - 1) }
    }
  } catch {
    // SourceBuffer 更新中可能抛 InvalidStateError
  }
  return { start: 0, end: 0 }
}

function expandedWindowFromVideo(opts: {
  liveDvr: boolean
  previewPos: number
  previewDuration: number
  markIn?: number | null
  markOut?: number | null
  video?: HTMLVideoElement | null
  followLive?: boolean
}) {
  const buffered = readMseBuffered(opts.video)
  const duration = opts.video?.duration
  return computeExpandedPreviewWindow({
    liveDvr: opts.liveDvr,
    previewPos: opts.previewPos,
    bufferedStart: buffered.start,
    bufferedEnd: buffered.end,
    previewDuration: opts.previewDuration,
    fileDuration: Number.isFinite(duration) ? duration as number : 0,
    markIn: opts.markIn,
    markOut: opts.markOut,
    followLive: opts.followLive,
  })
}

interface RoomCardProps {
  room: RoomSession
  selected: boolean
  multiSelected?: boolean
  send: (type: string, data: any) => void
  onSelect: (roomId: string, e: React.MouseEvent) => void
  onConnect: (roomId: string) => void
  onDisconnect: (roomId: string) => void
  onStartRecord: (roomId: string) => void
  onStopRecord: (roomId: string) => void
  onRemove: (roomId: string) => void
  onTogglePreview: (roomId: string, enabled: boolean) => void
  onToggleMute: (roomId: string) => void
  onFullscreen: (roomId: string) => void
  /** 浏览器原生全屏（video.requestFullscreen / exitFullscreen）切换 */
  onBrowserFullscreen?: (roomId: string) => void
  /** 点击 checkbox 切换多选状态（无需 Ctrl 键） */
  onToggleMultiSelect?: (roomId: string, e: React.MouseEvent) => void
  /** 当前区域放大的 roomId */
  expandedRoomId?: string | null
  /** 退出区域放大 */
  onCollapse?: (roomId: string) => void
  /** 父级共享录制计时 tick（秒级），避免每卡独立 setInterval */
  recordingTick?: number
  /** 放大态播放器控制：预览播放位置（秒，仅放大房间传入实时值） */
  previewPos?: number
  previewDuration?: number
  /** 放大预览条跟播：true 时播放头钉在右沿 */
  followLive?: boolean
  onPlayPause?: () => void
  onSeekBack?: () => void
  onSeekFwd?: () => void
  onSeekTo?: (roomId: string, time: number) => void
  /** AI 检测到的回合列表（用于在预览时间线上显示） */
  detectedRounds?: Array<{ start: number; end: number; confirm_status?: string }>
  /** 主时间线 windowStart（common 模式下用于同步指示） */
  mainWindowStart?: number | null
  /** 是否处于 common 模式 */
  isCommonMode?: boolean
}

/**
 * rooms_updated 广播每次都会创建新的 room 对象引用，即使字段值没有变化，
 * 也会导致 React.memo 默认浅比较认为 props 变了而触发重渲染。
 * 此比较器对 room 做字段级浅比较，只有真正影响渲染的字段变化时才重新渲染。
 */
function areRoomPropsEqual(prev: RoomCardProps, next: RoomCardProps): boolean {
  if (prev.selected !== next.selected) return false
  if (prev.multiSelected !== next.multiSelected) return false
  if (prev.send !== next.send) return false
  if (prev.onSelect !== next.onSelect) return false
  if (prev.onConnect !== next.onConnect) return false
  if (prev.onDisconnect !== next.onDisconnect) return false
  if (prev.onStartRecord !== next.onStartRecord) return false
  if (prev.onStopRecord !== next.onStopRecord) return false
  if (prev.onRemove !== next.onRemove) return false
  if (prev.onTogglePreview !== next.onTogglePreview) return false
  if (prev.onToggleMute !== next.onToggleMute) return false
  if (prev.onFullscreen !== next.onFullscreen) return false
  if (prev.onBrowserFullscreen !== next.onBrowserFullscreen) return false
  if (prev.onToggleMultiSelect !== next.onToggleMultiSelect) return false
  if (prev.expandedRoomId !== next.expandedRoomId) return false
  if (prev.onCollapse !== next.onCollapse) return false
  if (prev.recordingTick !== next.recordingTick) return false
  if (prev.onPlayPause !== next.onPlayPause) return false
  if (prev.onSeekBack !== next.onSeekBack) return false
  if (prev.onSeekFwd !== next.onSeekFwd) return false
  if (prev.onSeekTo !== next.onSeekTo) return false
  if (prev.previewDuration !== next.previewDuration) return false
  if (prev.followLive !== next.followLive) return false
  if (prev.detectedRounds !== next.detectedRounds) return false
  // previewPos 高频变化：仅当本卡处于放大态才参与比较，避免普通卡片每秒重渲染
  if (prev.previewPos !== next.previewPos && next.expandedRoomId != null && next.expandedRoomId === next.room.room_id) return false

  // room 字段级浅比较
  const a = prev.room
  const b = next.room
  if (a === b) return true
  return (
    a.room_id === b.room_id &&
    a.is_connected === b.is_connected &&
    a.is_connecting === b.is_connecting &&
    a.is_recording === b.is_recording &&
    a.is_recording_starting === b.is_recording_starting &&
    a.is_recording_queued === b.is_recording_queued &&
    a.recording_queue_position === b.recording_queue_position &&
    a.preview_enabled === b.preview_enabled &&
    a.preview_phase === b.preview_phase &&
    a.preview_paused === b.preview_paused &&
    a.preview_muted === b.preview_muted &&
    a.streamer_name === b.streamer_name &&
    a.stream_title === b.stream_title &&
    a.platform_name === b.platform_name &&
    a.last_error === b.last_error &&
    a.mse_error === b.mse_error &&
    a.record_started_at === b.record_started_at &&
    a.record_size_mb === b.record_size_mb &&
    a.mark_in === b.mark_in &&
    a.mark_out === b.mark_out &&
    a.stream_url === b.stream_url &&
    a.preview_mode === b.preview_mode
    && a.pipeline_health?.platform_id === b.pipeline_health?.platform_id
    && a.pipeline_health?.pipeline_mode === b.pipeline_health?.pipeline_mode
    && a.pipeline_health?.platform === b.pipeline_health?.platform
    && a.pipeline_health?.support_level === b.pipeline_health?.support_level
    && a.pipeline_health?.connection_policy === b.pipeline_health?.connection_policy
    && a.pipeline_health?.credential_status === b.pipeline_health?.credential_status
    && JSON.stringify(a.pipeline_health?.credential_kinds) === JSON.stringify(b.pipeline_health?.credential_kinds)
    && a.pipeline_health?.resolver === b.pipeline_health?.resolver
    && a.pipeline_health?.ingest === b.pipeline_health?.ingest
    && a.pipeline_health?.recording === b.pipeline_health?.recording
    && a.pipeline_health?.preview === b.pipeline_health?.preview
    && a.pipeline_health?.error === b.pipeline_health?.error
    && a.pipeline_health?.failure_kind === b.pipeline_health?.failure_kind
    && a.pipeline_health?.lease_id === b.pipeline_health?.lease_id
    && a.pipeline_health?.candidate_id === b.pipeline_health?.candidate_id
    && a.pipeline_health?.generation === b.pipeline_health?.generation
    && a.pipeline_health?.recovery_attempt === b.pipeline_health?.recovery_attempt
    && a.pipeline_health?.max_recovery_attempts === b.pipeline_health?.max_recovery_attempts
    && JSON.stringify(a.pipeline_health?.resources) === JSON.stringify(b.pipeline_health?.resources)
  )
}

export const RoomCard = memo(function RoomCard({
  room,
  selected,
  multiSelected = false,
  send,
  onSelect,
  onConnect,
  onDisconnect,
  onStartRecord,
  onStopRecord,
  onRemove,
  onTogglePreview,
  onToggleMute,
  onFullscreen,
  onBrowserFullscreen,
  onToggleMultiSelect,
  expandedRoomId,
  onCollapse,
  recordingTick = 0,
  previewPos = 0,
  previewDuration = 0,
  followLive = true,
  onPlayPause,
  onSeekBack,
  onSeekFwd,
  onSeekTo,
  detectedRounds = [],
  mainWindowStart = null,
  isCommonMode = false,
}: RoomCardProps) {
  const tick = recordingTick
  const { t } = useI18n()
  const [disconnecting, setDisconnecting] = useState(false)
  const [localMuted, setLocalMuted] = useState(room.preview_muted)
  const [localVolume, setLocalVolume] = useState(1.0)
  const [volumeOpen, setVolumeOpen] = useState(false)
  const [isBrowserFullscreen, setIsBrowserFullscreen] = useState(false)
  const disconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const isExpanded = expandedRoomId === room.room_id

  // 同步后端广播的实际静音状态（覆盖乐观更新）
  useEffect(() => {
    setLocalMuted(room.preview_muted)
  }, [room.preview_muted])

  // 音量本地控制：初始化/跟随 video.volume（后端无音量接口，仅前端生效）
  useEffect(() => {
    const video = window.__msePlayers?.[room.room_id]?.player?.videoElement
    if (video && typeof video.volume === 'number') {
      setLocalVolume(video.volume)
    }
  }, [room.room_id, room.preview_enabled])

  // 浏览器全屏状态监听（video 元素全屏时图标切换为“退出全屏”）
  useEffect(() => {
    const onChange = () => {
      const video = window.__msePlayers?.[room.room_id]?.player?.videoElement
      setIsBrowserFullscreen(Boolean(video && document.fullscreenElement === video))
    }
    document.addEventListener('fullscreenchange', onChange)
    return () => document.removeEventListener('fullscreenchange', onChange)
  }, [room.room_id])

  useEffect(() => {
    return () => {
      if (disconnectTimerRef.current) {
        clearTimeout(disconnectTimerRef.current)
      }
    }
  }, [])

  // 注入录制指示条脉冲动画 CSS（全局共享，仅注入一次）
  useEffect(() => {
    const styleId = 'room-card-recording-pulse-style'
    if (document.getElementById(styleId)) return
    const style = document.createElement('style')
    style.id = styleId
    style.textContent = `
      @keyframes roomCardPulse {
        0%, 100% { opacity: 1; }
        50% { opacity: 0.4; }
      }
      .room-card-recording-bar {
        animation: roomCardPulse 1.5s ease-in-out infinite;
      }
      @keyframes livePulse {
        0%, 100% { opacity: 1; transform: scale(1); }
        50% { opacity: 0.35; transform: scale(0.85); }
      }
    `
    document.head.appendChild(style)
  }, [])

  const isLive = !!(room.preview_enabled && room.preview_phase === 'streaming' && room.preview_mode !== 'recording_review')
  const isRecordingReview = room.preview_mode === 'recording_review'

  const recordingElapsedSeconds = useMemo(() => {
    if (!room.is_recording || !room.record_started_at) return 0
    return (Date.now() - new Date(room.record_started_at).getTime()) / 1000
  }, [room.is_recording, room.record_started_at, tick])

  const recordedLabelRef = useRef<HTMLSpanElement>(null)
  const expStartLabelRef = useRef<HTMLSpanElement>(null)
  const expEndLabelRef = useRef<HTMLSpanElement>(null)
  const expThumbRef = useRef<HTMLSpanElement>(null)
  const expFillRef = useRef<HTMLSpanElement>(null)
  useEffect(() => {
    if (!room.is_recording || !room.record_started_at) return
    const startedAt = new Date(room.record_started_at).getTime()
    const release = retainClockLoop()
    const unsub = subscribeClock(() => {
      const el = recordedLabelRef.current
      if (!el) return
      const sec = Math.max(0, (Date.now() - startedAt) / 1000)
      el.textContent = t('已录 {time}', { time: formatTime(sec) })
    })
    return () => {
      unsub()
      release()
    }
  }, [room.is_recording, room.record_started_at])

  /** 放大态播放控制所需派生量 */
  const isPreviewPlaying = room.preview_enabled && !room.preview_paused
  const videoElement = window.__msePlayers?.[room.room_id]?.player?.videoElement
  const supportsLiveDvr = !isNoDvrPreviewMode(room.preview_mode)
  const expandedWindow = expandedWindowFromVideo({
    liveDvr: supportsLiveDvr,
    previewPos,
    previewDuration,
    markIn: room.mark_in,
    markOut: room.mark_out,
    video: videoElement,
    followLive: supportsLiveDvr && followLive,
  })
  const expTimelineStart = expandedWindow.start
  const expTimelineEnd = expandedWindow.end
  const expTimelineSpan = Math.max(1e-6, expTimelineEnd - expTimelineStart)
  const replayBoundary = expandedWindow.purple
  const replayBoundaryPct = 0
  const expProgressPct = expandedWindow.playheadPct
  const progressFillLeftPct = expandedWindow.fillLeftPct
  const progressFillWidthPct = expandedWindow.fillWidthPct
  const hasLiveDvrRange = expandedWindow.hasLiveDvr

  useEffect(() => {
    if (!isExpanded) return
    const release = retainClockLoop()
    const unsub = subscribeClock(() => {
      const video = window.__msePlayers?.[room.room_id]?.player?.videoElement
      const pos = video && Number.isFinite(video.currentTime)
        ? Math.max(0, video.currentTime)
        : readPlayhead(room.room_id)
      const win = expandedWindowFromVideo({
        liveDvr: supportsLiveDvr,
        previewPos: pos,
        previewDuration,
        markIn: room.mark_in,
        markOut: room.mark_out,
        video,
        followLive: supportsLiveDvr && followLive,
      })
      if (expStartLabelRef.current) expStartLabelRef.current.textContent = formatTime(win.start)
      if (expEndLabelRef.current) expEndLabelRef.current.textContent = formatTime(win.end)
      if (expThumbRef.current) expThumbRef.current.style.left = `${win.playheadPct}%`
      if (expFillRef.current) {
        expFillRef.current.style.left = `${win.fillLeftPct}%`
        expFillRef.current.style.width = `${win.fillWidthPct}%`
      }
    })
    return () => {
      unsub()
      release()
    }
  }, [isExpanded, room.room_id, supportsLiveDvr, previewDuration, room.mark_in, room.mark_out, followLive])

  const seekExpandedTimeline = (clientX: number, track: HTMLElement) => {
    if (!onSeekTo) return
    const video = window.__msePlayers?.[room.room_id]?.player?.videoElement
    const pos = video && Number.isFinite(video.currentTime)
      ? Math.max(0, video.currentTime)
      : previewPos
    const win = expandedWindowFromVideo({
      liveDvr: supportsLiveDvr,
      previewPos: pos,
      previewDuration,
      markIn: room.mark_in,
      markOut: room.mark_out,
      video,
      followLive: supportsLiveDvr && followLive,
    })
    const span = Math.max(1e-6, win.end - win.start)
    const rect = track.getBoundingClientRect()
    const ratio = Math.max(0, Math.min(1, (clientX - rect.left) / Math.max(1, rect.width)))
    let target = win.start + ratio * span
    if (win.hasLiveDvr) {
      const snapRatio = Math.min(0.08, 14 / Math.max(1, rect.width))
      if (ratio <= snapRatio) {
        target = win.purple
      }
    }
    onSeekTo(room.room_id, target)
  }
  const overlayBtnStyle: React.CSSProperties = {
    color: 'var(--overlay-text, #f5f5f7)',
    background: 'var(--overlay-btn-bg, rgba(0,0,0,0.5))',
    backdropFilter: 'blur(8px)',
    borderRadius: 'var(--radius-md)',
  }
  /** 画质选择 / 静音 / 取消预览：放大与普通态共用，保证原有按键一个不少 */
  const qualitySelect = (
    <Select
      size="small"
      value={room.preview_quality || '高清'}
      onChange={(val) => {
        // 只发 set_preview_quality，后端负责保存 + 重启预览（避免前端 disable/enable 竞态）
        send('set_preview_quality', { room_id: room.room_id, quality: val })
      }}
      onClick={(e) => e.stopPropagation()}
      getPopupContainer={() => document.body}
      style={{ width: 88, fontSize: 11 }}
      options={[
        { value: '原画', label: t('原画') },
        { value: '高清', label: t('高清 720p') },
        { value: '标清', label: t('标清 480p') },
        { value: '流畅', label: t('流畅 360p') },
      ]}
    />
  )
  /** 音量控制：点击切换静音；悬停向上弹出竖向音量滑条（仅前端本地生效） */
  const muteBtn = (
    <div
      style={{ position: 'relative', display: 'flex', alignItems: 'center' }}
      onMouseEnter={() => setVolumeOpen(true)}
      onMouseLeave={() => setVolumeOpen(false)}
      onClick={(e) => e.stopPropagation()}
    >
      <Tooltip title={localMuted ? t('取消静音') : t('静音')}>
        <Button
          type="text"
          size="small"
          icon={localMuted ? <MutedOutlined /> : <SoundOutlined />}
          style={overlayBtnStyle}
          onClick={(e) => {
            e.stopPropagation()
            // 本地图标即时翻转；store/后端由 onToggleMute 乐观更新
            setLocalMuted(!localMuted)
            onToggleMute(room.room_id)
          }}
        />
      </Tooltip>
      {volumeOpen && (
        <div
          style={{
            position: 'absolute',
            bottom: 'calc(100% + 8px)',
            left: '50%',
            transform: 'translateX(-50%)',
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            gap: 5,
            background: 'rgba(20, 22, 26, 0.92)',
            backdropFilter: 'blur(10px)',
            border: '1px solid rgba(255,255,255,0.12)',
            borderRadius: 10,
            padding: '8px 6px',
            boxShadow: '0 8px 24px rgba(0,0,0,0.45)',
            zIndex: 30,
          }}
        >
          <SoundOutlined style={{ color: 'var(--overlay-text, #f5f5f7)', fontSize: 12 }} />
          {/* 竖向滑条：垂直方向调节音量 */}
          <input
            type="range"
            min={0}
            max={100}
            value={Math.round(localVolume * 100)}
            onChange={(e) => {
              e.stopPropagation()
              const v = Number(e.target.value) / 100
              setLocalVolume(v)
              const video = window.__msePlayers?.[room.room_id]?.player?.videoElement
              if (video) {
                video.volume = v
                if (localMuted) {
                  video.muted = false
                  setLocalMuted(false)
                  if (room.preview_muted) onToggleMute(room.room_id)
                }
              }
            }}
            style={{
              width: 4,
              height: 84,
              accentColor: 'var(--accent-primary, #4dc4bf)',
              cursor: 'pointer',
              writingMode: 'vertical-lr',
              direction: 'rtl',
            }}
          />
          <span style={{ color: 'var(--overlay-text, #f5f5f7)', fontSize: 11, minWidth: 30, textAlign: 'center', fontVariantNumeric: 'tabular-nums' }}>
            {Math.round(localVolume * 100)}%
          </span>
        </div>
      )}
    </div>
  )
  const stopPreviewBtn = (
    <Tooltip title={t('取消预览')}>
      <Button
        type="text"
        size="small"
        icon={<StopOutlined />}
        style={overlayBtnStyle}
        onClick={(e) => {
          e.stopPropagation()
          onTogglePreview(room.room_id, false)
        }}
      />
    </Tooltip>
  )

  return (
    <Card
      hoverable
      onClick={(e) => onSelect(room.room_id, e)}
      style={{
        background: selected ? 'var(--bg-tertiary)' : 'var(--bg-secondary)',
        border: multiSelected
          ? '1px solid var(--accent-primary)'
          : selected
            ? '1px solid var(--accent-primary)'
            : '1px solid transparent',
        boxShadow: multiSelected
          ? '0 0 0 2px rgba(77, 196, 191, 0.15), 0 0 12px rgba(77, 196, 191, 0.12)'
          : selected
          ? '0 0 0 3px rgba(77, 196, 191, 0.12), 0 0 16px rgba(77, 196, 191, 0.22)'
          : 'none',
        cursor: 'pointer',
      }}
      styles={{ body: { padding: 12 } }}
    >
      {/* Header：checkbox | 主播名 | LIVE */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          marginBottom: 8,
          minWidth: 0,
        }}
      >
        {onToggleMultiSelect && (
          <div
            role="checkbox"
            aria-checked={multiSelected || selected}
            tabIndex={0}
            onClick={(e) => {
              e.stopPropagation()
              onToggleMultiSelect(room.room_id, e)
            }}
            onKeyDown={(e) => {
              if (e.key === ' ' || e.key === 'Enter') {
                e.preventDefault()
                e.stopPropagation()
                onToggleMultiSelect(room.room_id, e as unknown as React.MouseEvent)
              }
            }}
            title={multiSelected || selected ? t('取消选择') : t('选择此房间')}
            style={{
              flexShrink: 0,
              width: 22,
              height: 22,
              borderRadius: 6,
              border: `2px solid ${
                multiSelected || selected
                  ? 'var(--accent-primary)'
                  : 'var(--text-primary, #1a1d23)'
              }`,
              background:
                multiSelected || selected
                  ? 'var(--accent-primary)'
                  : 'var(--bg-primary, #fff)',
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              transition: 'all .15s ease',
              color: multiSelected || selected ? 'var(--overlay-text, #f5f5f7)' : 'var(--text-primary, #1a1d23)',
              fontSize: 13,
              fontWeight: 700,
              lineHeight: 1,
              userSelect: 'none',
              boxSizing: 'border-box',
            }}
          >
            {multiSelected || selected ? '✓' : ''}
          </div>
        )}
        <Tooltip title={room.streamer_name || t('未知主播')}>
          <span
            style={{
              flex: 1,
              minWidth: 0,
              fontWeight: 600,
              fontSize: 14,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {room.streamer_name || t('未知主播')}
          </span>
        </Tooltip>
        {multiSelected && (
          <span
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 4,
              padding: '1px 6px',
              borderRadius: 4,
              fontSize: 9,
              fontWeight: 600,
              background: 'rgba(77, 196, 191, 0.12)',
              color: 'var(--accent-primary)',
              border: '1px solid rgba(77, 196, 191, 0.25)',
              flexShrink: 0,
            }}
          >
            <span
              style={{
                display: 'inline-block',
                width: 5,
                height: 5,
                borderRadius: '50%',
                background: 'var(--accent-primary)',
              }}
            />
            {t('已选中')}
          </span>
        )}
        {isRecordingReview && (
          <div
            className="room-card__review-badge"
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 4,
              flexShrink: 0,
              padding: '2px 8px',
              borderRadius: 6,
              background: 'rgba(175, 82, 222, 0.12)',
              border: '1px solid rgba(175, 82, 222, 0.25)',
            }}
          >
            <span
              style={{
                width: 6,
                height: 6,
                borderRadius: '50%',
                background: '#af52de',
              }}
            />
            <span
              style={{
                fontSize: 10,
                fontWeight: 700,
                letterSpacing: 0.8,
                color: '#af52de',
              }}
            >
              {t('回看')}
            </span>
          </div>
        )}
        {isLive && (
          <div
            className="room-card__live-badge"
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 4,
              flexShrink: 0,
              padding: '2px 8px',
              borderRadius: 6,
              background: 'rgba(48, 209, 88, 0.12)',
              border: '1px solid rgba(48, 209, 88, 0.25)',
            }}
          >
            <span
              style={{
                width: 6,
                height: 6,
                borderRadius: '50%',
                background: '#30d158',
                animation: 'livePulse 1.4s ease-in-out infinite',
              }}
            />
            <span
              style={{
                fontSize: 10,
                fontWeight: 700,
                letterSpacing: 0.8,
                color: '#30d158',
              }}
            >
              LIVE
            </span>
          </div>
        )}
      </div>

      {/* 预览区域 */}
      <div
        style={{
          width: '100%',
          height: isExpanded ? 'auto' : 180,
          aspectRatio: isExpanded ? '16 / 9' : undefined,
          minHeight: isExpanded ? 420 : undefined,
          background: '#0a0a0a',
          borderRadius: 8,
          marginBottom: 8,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          position: 'relative',
          overflow: 'hidden',
        }}
      >
        {room.last_error ? (
          <div style={{ textAlign: 'center', padding: '0 16px' }}>
            <div
              style={{
                color: 'var(--state-error)',
                fontSize: 13,
                fontWeight: 500,
                marginBottom: 4,
              }}
            >
              {isCredentialError(room) ? credentialErrorTitle(room) : t('连接失败')}
            </div>
            <Tooltip title={room.last_error}>
              <div
                style={{
                  color: 'var(--text-tertiary)',
                  fontSize: 11,
                  lineHeight: 1.4,
                  maxWidth: '100%',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                  marginBottom: isCredentialError(room) && credentialSettingsAvailable(room) ? 8 : 0,
                }}
              >
                {t(room.last_error)}
              </div>
            </Tooltip>
            {isCredentialError(room) && credentialSettingsAvailable(room) && (
              <Button size="small" type="primary" onClick={openCredentialSettings}>
                {t('去设置凭据')}
              </Button>
            )}
          </div>
        ) : !room.is_connected ? (
          <div style={{ textAlign: 'center' }}>
            <VideoCameraOutlined style={{ fontSize: 36, color: 'rgba(255,255,255,0.3)' }} />
            <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.45)', marginTop: 6 }}>{t('未连接')}</div>
          </div>
        ) : room.preview_enabled ? (
          <>
            {/* VideoPreview 实例始终保持挂载，区域放大时铺满卡片，不销毁/重建 MsePlayer */}
            <div style={{ position: 'relative', width: '100%', height: '100%' }}>
              <VideoPreview
                key={`preview-${room.room_id}`}
                roomId={room.room_id}
                active={true}
                send={send}
                controls={false}
                style={
                  isExpanded
                    ? {
                        position: 'absolute',
                        inset: 0,
                        zIndex: 8,
                        width: '100%',
                        height: '100%',
                        background: '#000',
                      }
                    : { width: '100%', height: '100%' }
                }
                muted={localMuted}
              />
            </div>
            {/* 底部控制区：普通态单行；放大态 = 动态进度条 + 播放控制行（原有按键全部保留、不被遮挡） */}
            {isExpanded ? (
              <div style={{ position: 'absolute', bottom: 0, left: 0, right: 0, display: 'flex', flexDirection: 'column', background: 'linear-gradient(transparent, rgba(0,0,0,0.78))', zIndex: 9 }}>
                <div className="room-card__expanded-timeline">
                  <span ref={expStartLabelRef} className="room-card__expanded-time">{formatTime(expTimelineStart)}</span>
                  <div
                    role="slider"
                    tabIndex={0}
                    aria-label={t('预览时间线')}
                    aria-valuemin={Math.round(replayBoundary)}
                    aria-valuemax={Math.round(expTimelineEnd)}
                    aria-valuenow={Math.round(previewPos)}
                    className="room-card__expanded-track"
                    onPointerDown={(e) => {
                      e.stopPropagation()
                      e.currentTarget.setPointerCapture(e.pointerId)
                      seekExpandedTimeline(e.clientX, e.currentTarget)
                    }}
                    onPointerMove={(e) => {
                      if (!e.currentTarget.hasPointerCapture(e.pointerId)) return
                      e.stopPropagation()
                      seekExpandedTimeline(e.clientX, e.currentTarget)
                    }}
                    onPointerUp={(e) => {
                      e.stopPropagation()
                      seekExpandedTimeline(e.clientX, e.currentTarget)
                      if (e.currentTarget.hasPointerCapture(e.pointerId)) {
                        e.currentTarget.releasePointerCapture(e.pointerId)
                      }
                    }}
                    onPointerCancel={(e) => {
                      if (e.currentTarget.hasPointerCapture(e.pointerId)) {
                        e.currentTarget.releasePointerCapture(e.pointerId)
                      }
                    }}
                    onKeyDown={(e) => {
                      if (!onSeekTo) return
                      if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
                        e.preventDefault()
                        e.stopPropagation()
                        onSeekTo(room.room_id, Math.max(replayBoundary, Math.min(
                          expTimelineEnd,
                          previewPos + (e.key === 'ArrowLeft' ? -10 : 10),
                        )))
                      }
                    }}
                  >
                    {hasLiveDvrRange && (
                      <>
                        <span
                          className="room-card__expanded-unavailable"
                          style={{ width: `${replayBoundaryPct}%` }}
                        />
                        <span
                          className="room-card__expanded-replay-boundary"
                          style={{ left: `${replayBoundaryPct}%` }}
                          title={t('DVR 左边界 {time}：约实时−2分钟，左侧不可回放，右侧可回放', { time: formatTime(replayBoundary) })}
                        />
                      </>
                    )}
                    {/* 主时间线位置指示器（P3: 预览时间线与主时间线同步滚动） */}
                    {isCommonMode && mainWindowStart != null && (() => {
                      const mwPct = Math.max(0, Math.min(100, ((mainWindowStart - expTimelineStart) / expTimelineSpan) * 100))
                      return (
                        <span
                          className="room-card__expanded-main-pos"
                          style={{ left: `${mwPct}%` }}
                          title={t('主时间线位置 {time}', { time: formatTime(mainWindowStart) })}
                        />
                      )
                    })()}
                    {/* AI 检测到的回合色带（P1: 预览时间线显示检测回合） */}
                    {detectedRounds.map((round, idx) => {
                      const rLeft = Math.max(0, Math.min(100, ((round.start - expTimelineStart) / expTimelineSpan) * 100))
                      const rWidth = Math.max(0, Math.min(100 - rLeft, ((round.end - round.start) / expTimelineSpan) * 100))
                      if (rWidth <= 0) return null
                      const isAudioPending = round.confirm_status === 'audio_pending'
                      const isConfirmed = round.confirm_status === 'vision_confirmed' || round.confirm_status === 'ocr_confirmed'
                      const roundClass = isAudioPending ? 'room-card__expanded-round--audio'
                        : isConfirmed ? 'room-card__expanded-round--confirmed'
                        : 'room-card__expanded-round--pending'
                      return (
                        <span
                          key={idx}
                          className={`room-card__expanded-round ${roundClass}`}
                          style={{ left: `${rLeft}%`, width: `${rWidth}%` }}
                          title={`${formatTime(round.start)}–${formatTime(round.end)}`}
                        />
                      )
                    })}
                    <span
                      ref={expFillRef}
                      className="room-card__expanded-track-fill"
                      style={{
                        left: `${progressFillLeftPct}%`,
                        width: `${progressFillWidthPct}%`,
                      }}
                    />
                    <span
                      ref={expThumbRef}
                      className="room-card__expanded-track-thumb"
                      style={{ left: `${expProgressPct}%` }}
                    />
                  </div>
                  <span ref={expEndLabelRef} className="room-card__expanded-time room-card__expanded-time--end">
                    {formatTime(expTimelineEnd)}
                  </span>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '4px 8px 6px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                    <Tooltip title={isPreviewPlaying ? t('暂停') : t('播放')}>
                      <Button type="text" size="small" icon={isPreviewPlaying ? <PauseCircleOutlined /> : <PlayCircleOutlined />} style={overlayBtnStyle} onClick={(e) => { e.stopPropagation(); onPlayPause?.() }} />
                    </Tooltip>
                    <Tooltip title={t('后退 10 秒（与总体时间线一致）')}>
                      <Button type="text" size="small" icon={<StepBackwardOutlined />} style={overlayBtnStyle} onClick={(e) => { e.stopPropagation(); onSeekBack?.() }}>10s</Button>
                    </Tooltip>
                    <Tooltip title={t('前进 10 秒（与总体时间线一致）')}>
                      <Button type="text" size="small" icon={<StepForwardOutlined />} style={overlayBtnStyle} onClick={(e) => { e.stopPropagation(); onSeekFwd?.() }}>10s</Button>
                    </Tooltip>
                    {qualitySelect}
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                    {muteBtn}
                    <Tooltip title={t('缩小（窗口播放）')}>
                      <Button type="text" size="small" icon={<ShrinkOutlined />} style={overlayBtnStyle} onClick={(e) => { e.stopPropagation(); onCollapse?.(room.room_id) }} />
                    </Tooltip>
                    <Tooltip title={isBrowserFullscreen ? t('退出全屏') : t('全屏放大')}>
                      <Button
                        type="text"
                        size="small"
                        icon={isBrowserFullscreen ? <FullscreenExitOutlined /> : <FullscreenOutlined />}
                        style={overlayBtnStyle}
                        onClick={(e) => { e.stopPropagation(); onBrowserFullscreen?.(room.room_id) }}
                      />
                    </Tooltip>
                    {stopPreviewBtn}
                  </div>
                </div>
              </div>
            ) : (
              <div
                style={{
                  position: 'absolute',
                  bottom: 0,
                  left: 0,
                  right: 0,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  padding: '6px 8px',
                  background: 'linear-gradient(transparent, rgba(0,0,0,0.7))',
                  zIndex: 3,
                }}
              >
                {qualitySelect}
                <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                  {muteBtn}
                  <Tooltip title={t('放大')}>
                    <Button
                      type="text"
                      size="small"
                      icon={<FullscreenOutlined />}
                      style={overlayBtnStyle}
                      onClick={(e) => {
                        e.stopPropagation()
                        onFullscreen(room.room_id)
                      }}
                    />
                  </Tooltip>
                  {stopPreviewBtn}
                </div>
              </div>
            )}
          </>
        ) : (
          <div style={{ textAlign: 'center' }}>
            <PlayCircleOutlined style={{ fontSize: 36, color: 'var(--accent-primary)' }} />
            <div style={{ marginTop: 6 }}>
              <Button
                size="small"
                onClick={(e) => {
                  e.stopPropagation()
                  onTogglePreview(room.room_id, true)
                }}
              >
                {t('启用预览')}
              </Button>
            </div>
          </div>
        )}
      
        {/* 录制中指示条（脉冲动画提示录制进行中） */}
        {room.is_recording && (
          <div
            className="room-card-recording-bar"
            style={{
              position: 'absolute',
              bottom: 0,
              left: 0,
              right: 0,
              height: 2,
              width: '100%',
              background: 'var(--accent-primary)',
              zIndex: 4,
            }}
          />
        )}
      </div>

      {/* Meta 行：已录墙钟时长（与时间线预览轴不同，可能差几秒） */}
      {room.is_recording && (
        <Tooltip title={t('「已录」为录制墙钟时长；时间线显示的是预览播放位置，二者可能相差数秒')}>
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 6,
              marginBottom: 6,
              fontSize: 12,
              color: 'var(--text-secondary)',
              minWidth: 0,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
              cursor: 'help',
            }}
          >
            <span style={{ color: 'var(--state-success)', fontWeight: 500, flexShrink: 0 }}>
              {t('录制中')}
            </span>
            {room.record_started_at && (
              <>
                <span style={{ color: 'var(--text-tertiary)', flexShrink: 0 }}>·</span>
                <span ref={recordedLabelRef} style={{ fontFamily: 'monospace', flexShrink: 0 }}>
                  {t('已录 {time}', { time: formatTime(recordingElapsedSeconds) })}
                </span>
              </>
            )}
            {room.record_size_mb > 0 && (
              <>
                <span style={{ color: 'var(--text-tertiary)', flexShrink: 0 }}>·</span>
                <span style={{ fontFamily: 'monospace', flexShrink: 0 }}>
                  {room.record_size_mb >= 1024
                    ? `${(room.record_size_mb / 1024).toFixed(1)} GB`
                    : `${room.record_size_mb.toFixed(0)} MB`}
                </span>
              </>
            )}
          </div>
        </Tooltip>
      )}

      {/* 标题行：仅 stream_title */}
      <div style={{ marginBottom: 10 }}>
        <Tooltip title={room.stream_title || t('暂无标题')}>
          <div
            style={{
              fontSize: 12,
              color: 'var(--text-tertiary)',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {room.stream_title || t('暂无标题')}
          </div>
        </Tooltip>
      </div>
      
      {/* 操作按钮：重新设计的布局 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        {/* 主操作按钮（占据主要宽度） */}
        {!room.is_connected ? (
          <Button
            type="primary"
            size="small"
            icon={<LinkOutlined />}
            onClick={(e) => {
              e.stopPropagation()
              onConnect(room.room_id)
            }}
            disabled={room.is_connecting}
            loading={room.is_connecting}
            style={{ flex: 1 }}
          >
            {t('连接')}
          </Button>
        ) : room.is_recording ? (
          <Button
            size="small"
            icon={<StopOutlined />}
            danger
            onClick={(e) => {
              e.stopPropagation()
              Modal.confirm({
                title: t('确认停止录制'),
                content: t('将停止录制「{streamer}」', { streamer: room.streamer_name || t('未知主播') }),
                okText: t('确认停止'),
                cancelText: t('取消'),
                okButtonProps: { danger: true },
                onOk: () => onStopRecord(room.room_id),
              })
            }}
            style={{
              flex: 1,
              background: 'rgba(255,59,48,0.12)',
              borderColor: 'rgba(255,59,48,0.3)',
              color: 'var(--state-error)',
            }}
          >
            {t('停止录制')}
          </Button>
        ) : (
          <Button
            type="primary"
            size="small"
            icon={<PlayCircleOutlined />}
            loading={!!room.is_recording_starting}
            disabled={!!room.is_recording_starting}
            onClick={(e) => {
              e.stopPropagation()
              onStartRecord(room.room_id)
            }}
            style={{ flex: 1 }}
          >
            {room.is_recording_queued
              ? `${t('排队中')}${room.recording_queue_position ? ` #${room.recording_queue_position}` : ''}`
              : room.is_recording_starting
                ? t('启动中')
                : t('开始录制')}
          </Button>
        )}
      
        {/* 断开按钮（已连接时显示） */}
        {room.is_connected && (
          <Button
            size="small"
            icon={<DisconnectOutlined />}
            loading={disconnecting}
            onClick={(e) => {
              e.stopPropagation()
              if (disconnecting) return
              setDisconnecting(true)
              try {
                onDisconnect(room.room_id)
              } finally {
                disconnectTimerRef.current = setTimeout(() => {
                  disconnectTimerRef.current = null
                  setDisconnecting(false)
                }, 1500)
              }
            }}
            style={{ flex: 1 }}
          >
            {t('断开')}
          </Button>
        )}
      
        {/* 删除按钮（角落） */}
        <Tooltip title={t('删除房间')}>
          <Button
            type="text"
            size="small"
            icon={<DeleteOutlined />}
            danger
            onClick={(e) => {
              e.stopPropagation()
              Modal.confirm({
                title: t('确认删除'),
                content: t('确定要删除房间“{streamer}”吗？此操作不可撤销。', { streamer: room.streamer_name || t('未知主播') }),
                okText: t('确认删除'),
                cancelText: t('取消'),
                okButtonProps: { danger: true },
                onOk: () => onRemove(room.room_id),
              })
            }}
            style={{ width: 36, height: 32, flexShrink: 0 }}
          />
        </Tooltip>
      </div>
    </Card>
  )
}, areRoomPropsEqual)
