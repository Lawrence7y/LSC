import { useState, useEffect, useMemo, memo } from 'react'
import { Card, Tag, Space, Button, Tooltip, Modal } from 'antd'
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
  onTogglePreview: (roomId: string, enabled: boolean, quality?: string) => void
  onToggleMute: (roomId: string) => void
  onFullscreen: (roomId: string) => void
  previewQuality?: string
  /** 当前全屏的 roomId（用于 CSS 全屏切换：fixed 浮起 + 占位文字） */
  fullscreenRoomId?: string | null
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
  if (prev.fullscreenRoomId !== next.fullscreenRoomId) return false

  // room 字段级浅比较
  const a = prev.room
  const b = next.room
  if (a === b) return true
  return (
    a.room_id === b.room_id &&
    a.is_connected === b.is_connected &&
    a.is_connecting === b.is_connecting &&
    a.is_recording === b.is_recording &&
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
  previewQuality,
  fullscreenRoomId,
}: RoomCardProps) {
  const [tick, setTick] = useState(0)
  const [disconnecting, setDisconnecting] = useState(false)
  const [localQuality, setLocalQuality] = useState(previewQuality || '高清')
  const updateRoom = useAppStore((state) => state.updateRoom)

  // 全局设置变化时同步本地画质
  useEffect(() => {
    if (previewQuality) setLocalQuality(previewQuality)
  }, [previewQuality])

  useEffect(() => {
    if (!room.is_recording) return
    const id = setInterval(() => setTick((t) => t + 1), 1000)
    return () => clearInterval(id)
  }, [room.is_recording])

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
          ? '1px solid var(--state-warning)'
          : selected
            ? '1px solid var(--accent-primary)'
            : '1px solid transparent',
        boxShadow: multiSelected
          ? '0 0 0 2px rgba(255, 149, 0, 0.2), 0 0 12px rgba(255, 149, 0, 0.15)'
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
          height: 160,
          background: 'var(--background-900)',
          borderRadius: 6,
          marginBottom: 12,
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
              连接失败
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
                }}
              >
                {room.last_error}
              </div>
            </Tooltip>
          </div>
        ) : !room.is_connected ? (
          <div style={{ textAlign: 'center' }}>
            <VideoCameraOutlined style={{ fontSize: 32, color: 'var(--text-500)' }} />
            <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 6 }}>未连接</div>
          </div>
        ) : room.preview_enabled ? (
          <>
            {/* VideoPreview 容器：全屏时 position:fixed 浮起覆盖视口，非全屏时填充 120px 预览区 */}
            <div
              style={
                fullscreenRoomId === room.room_id
                  ? {
                      position: 'fixed',
                      inset: 0,
                      zIndex: 9999,
                      background: '#000',
                      borderRadius: 0,
                    }
                  : {
                      position: 'relative',
                      width: '100%',
                      height: '100%',
                    }
              }
            >
              <VideoPreview
                key={`preview-${room.room_id}`}
                roomId={room.room_id}
                active={true}
                send={send}
                controls={fullscreenRoomId === room.room_id}
                style={{ width: '100%', height: '100%' }}
                muted={room.preview_muted}
              />
              {/* 全屏时右上角"退出全屏"按钮浮层 */}
              {fullscreenRoomId === room.room_id && (
                <Button
                  icon={<CloseOutlined />}
                  style={{ position: 'absolute', top: 12, right: 12, zIndex: 10 }}
                  onClick={(e) => {
                    e.stopPropagation()
                    onFullscreen(room.room_id)
                  }}
                >
                  退出全屏
                </Button>
              )}
            </div>
            {/* 全屏时原 120px 预览区显示占位文字（VideoPreview 已 fixed 浮起，原位置空出） */}
            {fullscreenRoomId === room.room_id && (
              <div
                style={{
                  position: 'absolute',
                  inset: 0,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  color: 'var(--text-tertiary)',
                  fontSize: 12,
                }}
              >
                预览已全屏
              </div>
            )}
            {room.mse_error && (
              <div
                style={{
                  position: 'absolute',
                  inset: 0,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  background: 'rgba(0, 0, 0, 0.75)',
                  flexDirection: 'column',
                  gap: 8,
                  padding: 16,
                  zIndex: 2,
                }}
              >
                <span style={{ fontSize: 20 }}>⚠️</span>
                <span
                  style={{
                    fontSize: 12,
                    color: 'var(--state-error)',
                    textAlign: 'center',
                    wordBreak: 'break-all',
                  }}
                >
                  {room.mse_error}
                </span>
              </div>
            )}
            <Button
              size="small"
              style={{ position: 'absolute', bottom: 8, right: 8, zIndex: 3 }}
              onClick={(e) => {
                e.stopPropagation()
                updateRoom(room.room_id, { mse_error: undefined })
                onTogglePreview(room.room_id, false)
              }}
            >
              停止预览
            </Button>
            {previewQuality && (
              <select
                value={localQuality}
                onChange={(e) => {
                  e.stopPropagation()
                  setLocalQuality(e.target.value)
                  onTogglePreview(room.room_id, false)
                  setTimeout(() => onTogglePreview(room.room_id, true, e.target.value), 200)
                }}
                onClick={(e) => e.stopPropagation()}
                style={{
                  position: 'absolute',
                  bottom: 8,
                  right: 72,
                  zIndex: 3,
                  fontSize: 11,
                  padding: '1px 4px',
                  borderRadius: 4,
                  border: '1px solid var(--border-default)',
                  background: 'var(--bg-tertiary)',
                  color: 'var(--text-primary)',
                  cursor: 'pointer',
                }}
              >
                <option value="原画">原画</option>
                <option value="高清">高清</option>
                <option value="标清">标清</option>
                <option value="流畅">流畅</option>
              </select>
            )}
          </>
        ) : (
          <div style={{ textAlign: 'center' }}>
            <PlayCircleOutlined style={{ fontSize: 32, color: 'var(--accent-primary)' }} />
            <div style={{ marginTop: 6, display: 'flex', gap: 4, justifyContent: 'center', alignItems: 'center' }}>
              <Button
                size="small"
                onClick={(e) => {
                  e.stopPropagation()
                  onTogglePreview(room.room_id, true, localQuality)
                }}
              >
                启用预览
              </Button>
              {previewQuality && (
                <select
                  value={localQuality}
                  onChange={(e) => {
                    e.stopPropagation()
                    setLocalQuality(e.target.value)
                  }}
                  onClick={(e) => e.stopPropagation()}
                  style={{
                    fontSize: 11,
                    padding: '1px 4px',
                    borderRadius: 4,
                    border: '1px solid var(--border-default)',
                    background: 'var(--bg-tertiary)',
                    color: 'var(--text-primary)',
                    cursor: 'pointer',
                  }}
                >
                  <option value="原画">原画</option>
                  <option value="高清">高清</option>
                  <option value="标清">标清</option>
                  <option value="流畅">流畅</option>
                </select>
              )}
            </div>
          </div>
        )}

        {/* 状态 badge */}
        <div
          style={{
            position: 'absolute',
            top: 8,
            left: 8,
            display: 'flex',
            alignItems: 'center',
            gap: 6,
            background: 'var(--bg-tertiary)',
            padding: '2px 8px',
            borderRadius: 4,
          }}
        >
          <div
            style={{
              width: 8,
              height: 8,
              borderRadius: '50%',
              background: statusColors[status],
              boxShadow:
                status === 'recording'
                  ? `0 0 8px ${statusColors[status]}`
                  : 'none',
            }}
          />
          <span style={{ fontSize: 11, color: 'var(--text-50)' }}>
            {statusLabels[status]}
          </span>
        </div>

        {/* 房间信息 */}
        {room.is_recording && room.record_started_at && (
          <div
            style={{
              position: 'absolute',
              top: 8,
              right: 8,
              background: 'rgba(0,0,0,0.7)',
              padding: '2px 8px',
              borderRadius: 4,
              fontSize: 11,
              color: '#fff',
              fontFamily: 'monospace',
            }}
          >
            {formatTime(recordingElapsedSeconds)}
          </div>
        )}

        {/* 录制文件大小（录制中时显示） */}
        {room.is_recording && room.record_size_mb > 0 && (
          <div
            style={{
              position: 'absolute',
              top: 30,
              right: 8,
              background: 'rgba(0,0,0,0.5)',
              padding: '1px 6px',
              borderRadius: 4,
              fontSize: 10,
              color: 'rgba(255,255,255,0.8)',
              fontFamily: 'monospace',
            }}
          >
            {room.record_size_mb >= 1024
              ? `${(room.record_size_mb / 1024).toFixed(1)} GB`
              : `${room.record_size_mb.toFixed(0)} MB`}
          </div>
        )}

        {/* 平台标签 */}
        <Tag
          style={{
            position: 'absolute',
            bottom: 8,
            left: 8,
            margin: 0,
          }}
        >
          {room.platform_name || '未知平台'}
        </Tag>

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
      <div style={{ marginBottom: 8 }}>
        <div
          style={{
            fontWeight: 500,
            fontSize: 14,
            marginBottom: 4,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          <Tooltip title={room.streamer_name || '未知主播'}>
            <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {room.streamer_name || '未知主播'}
            </span>
          </Tooltip>
          {/* Multi-select sync badge */}
          {multiSelected && (
            <span style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 4,
              marginLeft: 8,
              padding: '1px 6px',
              borderRadius: 4,
              fontSize: 10,
              fontWeight: 600,
              background: 'rgba(255, 149, 0, 0.15)',
              color: 'var(--state-warning)',
              border: '1px solid rgba(255, 149, 0, 0.3)',
            }}>
              <span style={{ display: 'inline-block', width: 6, height: 6, borderRadius: '50%', background: 'var(--state-warning)' }} />
              已同步
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

      {/* 操作按钮 */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%' }}>
        <Space size={2}>
          {!room.is_connected ? (
            <Tooltip title="连接">
              <Button
                type="text"
                size="small"
                icon={<LinkOutlined />}
                onClick={(e) => {
                  e.stopPropagation()
                  onConnect(room.room_id)
                }}
                disabled={room.is_connecting}
                loading={room.is_connecting}
              />
            </Tooltip>
          ) : (
            <Tooltip title="断开">
              <Button
                type="text"
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
                    // send 为同步发送消息，1.5s 后重置以提供视觉反馈
                    setTimeout(() => setDisconnecting(false), 1500)
                  }
                }}
              />
            </Tooltip>
          )}

          {room.is_connected && !room.is_recording && (
            <Tooltip title="开始录制">
              <Button
                type="text"
                size="small"
                icon={<PlayCircleOutlined />}
                style={{ color: 'var(--state-success)' }}
                onClick={(e) => {
                  e.stopPropagation()
                  onStartRecord(room.room_id)
                }}
              />
            </Tooltip>
          )}

          {room.is_recording && (
            <Tooltip title="停止录制">
              <Button
                type="text"
                size="small"
                icon={<StopOutlined />}
                danger
                onClick={(e) => {
                  e.stopPropagation()
                  Modal.confirm({
                    title: '确认停止录制',
                    content: `确定要停止房间"${room.streamer_name || '未知主播'}"的录制吗？`,
                    okText: '确认停止',
                    cancelText: '取消',
                    okButtonProps: { danger: true },
                    onOk: () => onStopRecord(room.room_id),
                  })
                }}
              />
            </Tooltip>
          )}

          {room.preview_enabled && (
            <Tooltip title={room.preview_muted ? '取消静音' : '静音'}>
              <Button
                type="text"
                size="small"
                icon={room.preview_muted ? <MutedOutlined /> : <SoundOutlined />}
                onClick={(e) => {
                  e.stopPropagation()
                  onToggleMute(room.room_id)
                }}
              />
            </Tooltip>
          )}

          {room.preview_enabled && (
            <Tooltip title="全屏放大">
              <Button
                type="text"
                size="small"
                icon={<FullscreenOutlined />}
                onClick={(e) => {
                  e.stopPropagation()
                  onFullscreen(room.room_id)
                }}
              />
            </Tooltip>
          )}
        </Space>

        <Space size={2}>
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
                  content: `确定要删除房间"${room.streamer_name || '未知主播'}"吗？此操作不可撤销。`,
                  okText: '确认删除',
                  cancelText: '取消',
                  okButtonProps: { danger: true },
                  onOk: () => onRemove(room.room_id),
                })
              }}
            />
          </Tooltip>
        </Space>
      </div>
    </Card>
  )
}, areRoomPropsEqual)
