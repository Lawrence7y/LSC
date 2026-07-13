import { useState, useEffect, useMemo, memo, useRef } from 'react'
import { Card, Button, Tooltip, Modal, Select } from 'antd'
import {
  PlayCircleOutlined,
  DeleteOutlined,
  LinkOutlined,
  DisconnectOutlined,
  VideoCameraOutlined,
  StopOutlined,
  SoundOutlined,
  MutedOutlined,
  FullscreenOutlined,
  CloseOutlined,
} from '@ant-design/icons'
import { RoomSession } from '@/types'
import { VideoPreview } from '@/components/VideoPreview'
import { formatTime } from '@/utils/time'
import { useAppStore } from '@/store/appStore'

function openDouyinCookieSettings(e: React.MouseEvent) {
  e.stopPropagation()
  useAppStore.getState().setSettingsDrawerOpen(true)
}

/** 抖音缺 Cookie / 验证页类错误，引导用户去设置页配置 Cookie */
function isDouyinCookieError(room: RoomSession): boolean {
  const text = `${room.last_error || ''} ${room.mse_error || ''}`.toLowerCase()
  if (!text.trim()) return false
  const isDouyin = room.platform === 'douyin' || (room.platform_name || '').includes('抖音')
  if (!isDouyin) return false
  return (
    text.includes('cookie') ||
    text.includes('验证中间页') ||
    text.includes('验证码') ||
    text.includes('抖音 cookie') ||
    text.includes('设置 → 抖音') ||
    text.includes('设置页')
  )
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
  /** 点击 checkbox 切换多选状态（无需 Ctrl 键） */
  onToggleMultiSelect?: (roomId: string, e: React.MouseEvent) => void
  /** 当前区域放大的 roomId */
  expandedRoomId?: string | null
  /** 当前全屏的 roomId */
  fullscreenRoomId?: string | null
  /** 退出区域放大 */
  onCollapse?: (roomId: string) => void
  /** 退出全屏后回到区域放大 */
  onExitFullscreen?: (roomId: string) => void
}

type RoomStatus = 'recording' | 'connected' | 'connecting' | 'failed' | 'idle'

const statusColors: Record<RoomStatus, string> = {
  recording: 'var(--state-success)',
  connected: 'var(--state-success)',
  connecting: 'var(--state-warning)',
  failed: 'var(--state-error)',
  idle: 'var(--text-tertiary)',
}

const statusLabels: Record<RoomStatus, string> = {
  recording: '录制中',
  connected: '已连接',
  connecting: '连接中',
  failed: '失败',
  idle: '未连接',
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
  if (prev.onToggleMultiSelect !== next.onToggleMultiSelect) return false
  if (prev.expandedRoomId !== next.expandedRoomId) return false

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
    a.stream_url === b.stream_url
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
  onToggleMultiSelect,
  expandedRoomId,
  fullscreenRoomId,
  onCollapse,
  onExitFullscreen,
}: RoomCardProps) {
  const [tick, setTick] = useState(0)
  const [disconnecting, setDisconnecting] = useState(false)
  const [localMuted, setLocalMuted] = useState(room.preview_muted)
  const disconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  // 放大状态：区域放大 / 真全屏
  const isExpanded = expandedRoomId === room.room_id
  const isFullscreen = fullscreenRoomId === room.room_id
  const isAnyExpanded = isExpanded || isFullscreen

  useEffect(() => {
    if (!room.is_recording) return
    const id = setInterval(() => setTick((t) => t + 1), 1000)
    return () => clearInterval(id)
  }, [room.is_recording])

  // 同步后端广播的实际静音状态（覆盖乐观更新）
  useEffect(() => {
    setLocalMuted(room.preview_muted)
  }, [room.preview_muted])

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
    `
    document.head.appendChild(style)
  }, [])

  const getStatus = (): RoomStatus => {
    if (room.is_recording) return 'recording'
    if (room.is_connecting) return 'connecting'
    if (room.is_connected) return 'connected'
    if (room.last_error) return 'failed'
    return 'idle'
  }

  const status = getStatus()

  const recordingElapsedSeconds = useMemo(() => {
    if (!room.is_recording || !room.record_started_at) return 0
    return (Date.now() - new Date(room.record_started_at).getTime()) / 1000
  }, [room.is_recording, room.record_started_at, tick])

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
          ? '0 0 0 2px rgba(0, 122, 255, 0.15), 0 0 12px rgba(0, 122, 255, 0.12)'
          : selected
          ? '0 0 0 3px rgba(0, 122, 255, 0.12), 0 0 16px rgba(0, 122, 255, 0.22)'
          : 'none',
        cursor: 'pointer',
      }}
      styles={{ body: { padding: 12 } }}
    >
      {/* 预览区域 */}
      <div
        style={{
          width: '100%',
          height: isAnyExpanded ? 'auto' : 180,
          aspectRatio: isAnyExpanded ? '16 / 9' : undefined,
          minHeight: isAnyExpanded ? 420 : undefined,
          background: '#0a0a0a',
          borderRadius: 8,
          marginBottom: 10,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          position: 'relative',
          overflow: 'hidden',
        }}
      >
        {/* 多选 Checkbox */}
        {onToggleMultiSelect && (
          <div
            role="checkbox"
            aria-checked={multiSelected || selected}
            onClick={(e) => {
              e.stopPropagation()
              onToggleMultiSelect(room.room_id, e)
            }}
            title={multiSelected || selected ? '取消选择' : '选择此房间'}
            style={{
              position: 'absolute',
              top: 8,
              left: 8,
              zIndex: 5,
              width: 22,
              height: 22,
              borderRadius: 6,
              border: `2px solid ${
                multiSelected || selected
                  ? 'var(--accent-primary)'
                  : 'rgba(255,255,255,0.45)'
              }`,
              background:
                multiSelected || selected
                  ? 'var(--accent-primary)'
                  : 'rgba(0,0,0,0.45)',
              backdropFilter: 'blur(8px)',
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              transition: 'all .15s ease',
              color: '#fff',
              fontSize: 13,
              fontWeight: 700,
              lineHeight: 1,
              userSelect: 'none',
            }}
          >
            {multiSelected || selected ? '✓' : ''}
          </div>
        )}
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
              {isDouyinCookieError(room) ? '需要抖音 Cookie' : '连接失败'}
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
                  marginBottom: isDouyinCookieError(room) ? 8 : 0,
                }}
              >
                {room.last_error}
              </div>
            </Tooltip>
            {isDouyinCookieError(room) && (
              <Button size="small" type="primary" onClick={openDouyinCookieSettings}>
                去设置 Cookie
              </Button>
            )}
          </div>
        ) : !room.is_connected ? (
          <div style={{ textAlign: 'center' }}>
            <VideoCameraOutlined style={{ fontSize: 36, color: 'rgba(255,255,255,0.3)' }} />
            <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.45)', marginTop: 6 }}>未连接</div>
          </div>
        ) : room.preview_enabled ? (
          <>
            {/* VideoPreview 实例始终保持挂载，区域放大时铺满卡片，
                全屏时通过 CSS position:fixed 覆盖视口，不销毁/重建 MsePlayer */}
            <div style={{ position: 'relative', width: '100%', height: '100%' }}>
              <VideoPreview
                key={`preview-${room.room_id}`}
                roomId={room.room_id}
                active={true}
                send={send}
                controls={isExpanded || isFullscreen}
                style={
                  isFullscreen
                    ? {
                        position: 'fixed',
                        inset: 0,
                        zIndex: 9999,
                        width: '100vw',
                        height: '100vh',
                        background: '#000',
                        borderRadius: 0,
                      }
                    : isExpanded
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
              {/* 放大时的退出按钮 */}
              {(isExpanded || isFullscreen) && (
                <Button
                  icon={<CloseOutlined />}
                  size="small"
                  style={{
                    position: 'absolute',
                    top: 12,
                    right: 12,
                    zIndex: 10000,
                    background: 'rgba(0, 0, 0, 0.65)',
                    backdropFilter: 'blur(8px)',
                    color: '#fff',
                    border: '1px solid rgba(255,255,255,0.15)',
                    borderRadius: 6,
                    boxShadow: '0 2px 12px rgba(0, 0, 0, 0.3)',
                  }}
                  onClick={(e) => {
                    e.stopPropagation()
                    if (isFullscreen) {
                      onExitFullscreen?.(room.room_id)
                      return
                    }
                    onCollapse?.(room.room_id)
                  }}
                >
                  {isFullscreen ? '退出全屏' : '缩小'}
                </Button>
              )}
            </div>
                        {/* 底部渐变栏：预览控制 */}
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
              {/* 预览画质选择 */}
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
                  { value: '原画', label: '原画' },
                  { value: '高清', label: '高清 720p' },
                  { value: '标清', label: '标清 480p' },
                  { value: '流畅', label: '流畅 360p' },
                ]}
              />
              <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                <Tooltip title={localMuted ? '取消静音' : '静音'}>
                  <Button
                    type="text"
                    size="small"
                    icon={localMuted ? <MutedOutlined /> : <SoundOutlined />}
                    style={{ color: '#fff', background: 'rgba(0,0,0,0.5)', backdropFilter: 'blur(8px)', borderRadius: 6 }}
                    onClick={(e) => {
                      e.stopPropagation()
                      // 本地图标即时翻转；store/后端由 onToggleMute 乐观更新
                      setLocalMuted(!localMuted)
                      onToggleMute(room.room_id)
                    }}
                  />
                </Tooltip>
                <Tooltip title={isFullscreen ? '退出全屏' : '放大'}>
                  <Button
                    type="text"
                    size="small"
                    icon={<FullscreenOutlined />}
                    style={{ color: '#fff', background: 'rgba(0,0,0,0.5)', backdropFilter: 'blur(8px)', borderRadius: 6 }}
                    onClick={(e) => {
                      e.stopPropagation()
                      onFullscreen(room.room_id)
                    }}
                  />
                </Tooltip>
              </div>
            </div>
            {/* 竖向音量滑块（放大/全屏时） */}
            {(isExpanded || isFullscreen) && (
              <div style={{
                position: 'absolute', right: 12, top: '50%',
                transform: 'translateY(-50%)', zIndex: 10001,
                display: 'flex', flexDirection: 'column',
                alignItems: 'center', gap: 4,
                background: 'rgba(0,0,0,0.5)', backdropFilter: 'blur(8px)',
                padding: '8px 4px', borderRadius: 8, pointerEvents: 'auto',
              }}>
                <SoundOutlined style={{ color: '#fff', fontSize: 11 }} />
                <input type="range" min="0" max="1" step="0.05" defaultValue="1"
                  onChange={(e) => {
                    e.stopPropagation()
                    const vol = parseFloat(e.target.value)
                    const registry = (window as any).__msePlayers
                    const video = registry?.[room.room_id]?.player?.videoElement
                    if (video) { video.volume = vol; video.muted = vol === 0 }
                  }}
                  onClick={(e) => e.stopPropagation()}
                  style={{
                    writingMode: 'vertical-lr', direction: 'rtl',
                    width: 4, height: 100, cursor: 'pointer', accentColor: '#007aff',
                    WebkitAppearance: 'slider-vertical' as any,
                  }}
                />
                <MutedOutlined style={{ color: '#fff', fontSize: 11, opacity: 0.5 }} />
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
                启用预览
              </Button>
            </div>
          </div>
        )}
      
        {/* 状态 badge（左上角，偏移到 checkbox 右侧） */}
        <div
          style={{
            position: 'absolute',
            top: 8,
            left: onToggleMultiSelect ? 38 : 8,
            display: 'flex',
            alignItems: 'center',
            gap: 6,
          }}
        >
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 5,
              background: 'rgba(0,0,0,0.65)',
              backdropFilter: 'blur(8px)',
              padding: '3px 8px',
              borderRadius: 6,
            }}
          >
            <div
              style={{
                width: 7,
                height: 7,
                borderRadius: '50%',
                background: statusColors[status],
                boxShadow:
                  status === 'recording'
                    ? `0 0 8px ${statusColors[status]}`
                    : 'none',
              }}
            />
            <span style={{ fontSize: 11, color: '#fff' }}>
              {statusLabels[status]}
            </span>
          </div>

        </div>
      
        {/* 录制时间（右上角，始终暗色） */}
        {room.is_recording && room.record_started_at && (
          <div
            style={{
              position: 'absolute',
              top: 8,
              right: 8,
              background: 'rgba(0,0,0,0.7)',
              backdropFilter: 'blur(8px)',
              padding: '2px 8px',
              borderRadius: 6,
              fontSize: 11,
              color: '#fff',
              fontFamily: 'monospace',
            }}
          >
            {formatTime(recordingElapsedSeconds)}
          </div>
        )}
      
        {/* 录制文件大小（右上角时间下方，始终暗色） */}
        {room.is_recording && room.record_size_mb > 0 && (
          <div
            style={{
              position: 'absolute',
              top: 30,
              right: 8,
              background: 'rgba(0,0,0,0.55)',
              backdropFilter: 'blur(8px)',
              padding: '1px 6px',
              borderRadius: 4,
              fontSize: 10,
              color: 'rgba(255,255,255,0.75)',
              fontFamily: 'monospace',
            }}
          >
            {room.record_size_mb >= 1024
              ? `${(room.record_size_mb / 1024).toFixed(1)} GB`
              : `${room.record_size_mb.toFixed(0)} MB`}
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
            }}
          />
        )}
      </div>
      
      {/* 房间信息 */}
      <div style={{ marginBottom: 10 }}>
        <div
          style={{
            fontWeight: 600,
            fontSize: 14,
            marginBottom: 2,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
            display: 'flex',
            alignItems: 'center',
            gap: 6,
          }}
        >
          <Tooltip title={room.streamer_name || '未知主播'}>
            <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>
              {room.streamer_name || '未知主播'}
            </span>
          </Tooltip>
          {/* Multi-select badge: "已选中" (blue) — not "已同步" which only applies after audio alignment */}
          {multiSelected && (
            <span style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 4,
              padding: '1px 6px',
              borderRadius: 4,
              fontSize: 9,
              fontWeight: 600,
              background: 'rgba(0, 122, 255, 0.12)',
              color: 'var(--accent-primary)',
              border: '1px solid rgba(0, 122, 255, 0.25)',
              flexShrink: 0,
            }}>
              <span style={{ display: 'inline-block', width: 5, height: 5, borderRadius: '50%', background: 'var(--accent-primary)' }} />
              已选中
            </span>
          )}
        </div>
        <Tooltip title={room.stream_title || '暂无标题'}>
          <div
            style={{
              fontSize: 12,
              color: 'var(--text-tertiary)',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {room.stream_title || '暂无标题'}
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
            连接
          </Button>
        ) : room.is_recording ? (
          <Button
            size="small"
            icon={<StopOutlined />}
            danger
            onClick={(e) => {
              e.stopPropagation()
              Modal.confirm({
                title: '确认停止录制',
                content: `将停止录制「${room.streamer_name || '未知主播'}」`,
                okText: '确认停止',
                cancelText: '取消',
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
            停止录制
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
              ? `排队中${room.recording_queue_position ? ` #${room.recording_queue_position}` : ''}`
              : room.is_recording_starting
                ? '启动中'
                : '开始录制'}
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
            断开
          </Button>
        )}
      
        {/* 删除按钮（角落） */}
        <Tooltip title="删除房间">
          <Button
            type="text"
            size="small"
            icon={<DeleteOutlined />}
            danger
            onClick={(e) => {
              e.stopPropagation()
              Modal.confirm({
                title: '确认删除',
                content: `确定要删除房间“${room.streamer_name || '未知主播'}”吗？此操作不可撤销。`,
                okText: '确认删除',
                cancelText: '取消',
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
