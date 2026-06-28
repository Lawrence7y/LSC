import { memo, useMemo, useState, useEffect } from 'react'
import { Space, Button, Tooltip } from 'antd'
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
import { RoomSession, ClipSegment } from '@/types'
import { Timeline } from '@/components/Timeline'
import { formatTime } from '@/utils/time'

interface ControlBarProps {
  room: RoomSession | undefined
  multiSelectCount?: number
  loopPreview?: boolean
  clips?: ClipSegment[]
  previewPos?: number
  onSeek: (time: number) => void
  onPlayPause: () => void
  onSeekBack: () => void
  onSeekFwd: () => void
  onMarkIn: () => void
  onMarkOut: () => void
  onAddClip: () => void
  onToggleLoop?: () => void
  onGoLive?: () => void
  zoomLevel?: number
  onZoomChange?: (zoom: number) => void
  onMarkerDrag?: (type: 'in' | 'out', time: number) => void
  onDeleteMarker?: (type: 'in' | 'out') => void
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
  if (prev.zoomLevel !== next.zoomLevel) return false
  if (prev.onZoomChange !== next.onZoomChange) return false
  if (prev.onGoLive !== next.onGoLive) return false
  if (prev.onMarkerDrag !== next.onMarkerDrag) return false
  if (prev.onDeleteMarker !== next.onDeleteMarker) return false

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
    a.mark_out === b.mark_out
  )
}

export const ControlBar = memo(function ControlBar({
  room,
  multiSelectCount = 0,
  loopPreview = false,
  clips = [],
  previewPos = 0,
  onSeek,
  onPlayPause,
  onSeekBack,
  onSeekFwd,
  onMarkIn,
  onMarkOut,
  onAddClip,
  onToggleLoop,
  onGoLive,
  zoomLevel = 1,
  onZoomChange,
  onMarkerDrag,
  onDeleteMarker,
}: ControlBarProps) {
  // 录制中时每秒刷新一次时间显示，非录制时不触发
  const [tick, setTick] = useState(0)
  useEffect(() => {
    if (!room?.is_recording) return
    const id = setInterval(() => setTick(t => t + 1), 1000)
    return () => clearInterval(id)
  }, [room?.is_recording])

  const hasSelection = useMemo(() =>
    room?.mark_in !== null && room?.mark_out !== null && room?.mark_in !== undefined && room?.mark_out !== undefined
    && room.mark_in < room.mark_out
  , [room?.mark_in, room?.mark_out])

  // 播放状态：预览已启用目未暂停时才显示为播放中
  const isPlaying = room ? (room.preview_enabled && !room.preview_paused) : false
  const isDisabled = !room && (multiSelectCount ?? 0) === 0

  // 时间线总时长：默认4小时窗口，录制超过4小时后自动滚动
  const TIMELINE_WINDOW = 14400 // 4 小时
  const { duration, currentTime, windowStart } = useMemo(() => {
    let dur = TIMELINE_WINDOW
    let cur = 0
    let elapsed = 0
    if (room?.mark_out !== null && room?.mark_out !== undefined && room.mark_out > 0) {
      elapsed = room.mark_out
    }
    if (room?.is_recording && room?.record_started_at) {
      elapsed = Math.max(elapsed, (Date.now() - new Date(room.record_started_at).getTime()) / 1000)
    }
    // 录制时长超过窗口时，自动滚动窗口跟随播放头
    let ws = 0
    if (elapsed > TIMELINE_WINDOW) {
      ws = elapsed - TIMELINE_WINDOW
    } else {
      dur = Math.max(TIMELINE_WINDOW, elapsed)
    }
    // 优先使用 MSE player 实际播放位置，回退到入点
    if (previewPos > 0) {
      cur = previewPos
    } else if (room?.mark_in !== null && room?.mark_in !== undefined && room.mark_in > 0) {
      cur = room.mark_in
    }
    return { duration: dur, currentTime: cur, windowStart: ws }
  }, [room?.mark_out, room?.is_recording, room?.record_started_at, room?.mark_in, previewPos, tick])

  const roomClips = useMemo(() =>
    clips
      .filter(c => c.room_id === room?.room_id && c.end > c.start)
      .map(c => ({ start: c.start, end: c.end }))
  , [clips, room?.room_id])

  return (
    <div style={{
      padding: '12px 24px',
      background: 'var(--bg-secondary)',
      borderTop: '1px solid var(--border-default)',
      display: 'flex',
      flexDirection: 'column',
      gap: 12,
    }}>
      {/* 时间线 */}
      {/* Multi-select indicator */}
      {multiSelectCount > 0 && (
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '4px 12px',
          background: 'rgba(255, 149, 0, 0.08)',
          borderRadius: 6,
          fontSize: 12,
          color: 'var(--state-warning)',
          fontWeight: 500,
          marginBottom: -4,
        }}>
          <span style={{ display: 'inline-block', width: 6, height: 6, borderRadius: '50%', background: 'var(--state-warning)' }} />
          {multiSelectCount} 个房间同步 — 时间线 / 入出点 / 播放控制全局生效
        </div>
      )}
      <Timeline
        duration={duration}
        currentTime={Math.max(0, currentTime - windowStart)}
        markIn={room?.mark_in != null ? Math.max(0, room.mark_in - windowStart) : null}
        markOut={room?.mark_out != null ? Math.max(0, room.mark_out - windowStart) : null}
        buffered={Math.max(0, currentTime - windowStart)}
        clips={roomClips.map(c => ({ start: Math.max(0, c.start - windowStart), end: Math.max(0, c.end - windowStart) }))}
        windowStart={windowStart}
        onSeek={onSeek}
        onMarkIn={onMarkIn}
        onMarkOut={onMarkOut}
        onMarkerDrag={onMarkerDrag}
        onDeleteMarker={onDeleteMarker}
        height={80}
        zoomLevel={zoomLevel}
        onZoomChange={onZoomChange}
      />

      {/* 控制按钮 */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
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
            {formatTime(currentTime)}
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
          <Tooltip title="添加到切片列表">
            <Button 
              type="text" size="small"
              icon={<ScissorOutlined />}
              onClick={onAddClip}
              disabled={!room || !hasSelection}
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
