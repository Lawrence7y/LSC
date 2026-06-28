import { useMemo, useState } from 'react'
import { Empty, Button, Progress, Input, Select, Alert, Row, Col } from 'antd'
import {
  DatabaseOutlined,
  VideoCameraOutlined,
  SettingOutlined,
  CheckCircleOutlined,
  ExclamationCircleOutlined,
  SearchOutlined,
} from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { useAppStore } from '@/store/appStore'
import { RoomSession } from '@/types'

export default function Dashboard() {
  const navigate = useNavigate()
  const rooms = useAppStore((state) => state.rooms)
  const diskUsage = useAppStore((state) => state.diskUsage)
  const setSelectedRoomId = useAppStore((state) => state.setSelectedRoomId)
  const [searchText, setSearchText] = useState('')
  const [platformFilter, setPlatformFilter] = useState<string>('all')

  const totalRooms = rooms.length
  const recordingCount = rooms.filter((r: RoomSession) => r.is_recording).length
  const connectedCount = rooms.filter((r: RoomSession) => r.is_connected).length
  const failedCount = rooms.filter((r: RoomSession) => !r.is_connected && r.last_error).length

  const recentHistory = useMemo(() => {
    return [...rooms]
      .filter((r) => r.record_started_at)
      .sort((a, b) => {
        const ta = new Date(a.record_started_at!).getTime()
        const tb = new Date(b.record_started_at!).getTime()
        return tb - ta
      })
      .filter(r => {
        const matchSearch = !searchText || (
          (r.streamer_name || '').toLowerCase().includes(searchText.toLowerCase()) ||
          (r.stream_title || '').toLowerCase().includes(searchText.toLowerCase())
        )
        const matchPlatform = platformFilter === 'all' || r.platform_name === platformFilter
        return matchSearch && matchPlatform
      })
      .slice(0, 20)
  }, [rooms, searchText, platformFilter])

  const handleHistoryClick = (roomId: string) => {
    setSelectedRoomId(roomId)
    navigate('/workbench')
  }

  const formatDate = (iso: string | null) => {
    if (!iso) return '--'
    const d = new Date(iso)
    return `${d.getMonth() + 1}/${d.getDate()} ${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}`
  }

  const bytesToGB = (bytes: number) => bytes / (1024 ** 3)

  const formatSize = (mb: number) => {
    if (!mb) return '--'
    if (mb >= 1024) return `${(mb / 1024).toFixed(1)} GB`
    return `${Math.round(mb)} MB`
  }

  const diskPercent = diskUsage && diskUsage.total > 0
    ? Math.min(100, Math.max(0, (diskUsage.used / diskUsage.total) * 100))
    : 0

  const statCards = [
    {
      icon: <DatabaseOutlined style={{ fontSize: 20 }} />,
      value: totalRooms,
      label: '房间总数',
      color: 'var(--brand-500)',
      bg: 'rgba(0, 122, 255, 0.12)',
      onClick: () => navigate('/workbench'),
    },
    {
      icon: <VideoCameraOutlined style={{ fontSize: 20 }} />,
      value: recordingCount,
      label: '录制中',
      color: 'var(--state-success)',
      bg: 'rgba(52, 199, 89, 0.12)',
      onClick: () => navigate('/workbench'),
    },
    {
      icon: <CheckCircleOutlined style={{ fontSize: 20 }} />,
      value: connectedCount,
      label: '已连接',
      color: 'var(--state-warning)',
      bg: 'rgba(255, 149, 0, 0.12)',
    },
    {
      icon: <ExclamationCircleOutlined style={{ fontSize: 20 }} />,
      value: failedCount,
      label: '连接失败',
      color: 'var(--state-error)',
      bg: 'rgba(255, 59, 48, 0.12)',
    },
  ]

  return (
    <div style={{ padding: 20 }}>
      {/* Stats Row */}
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        {statCards.map((card, idx) => (
          <Col key={idx} xs={24} sm={12} md={6}>
            <div
              onClick={card.onClick}
              style={{
                height: '100%',
                background: 'var(--background-800)',
                border: '1px solid var(--border-default)',
                borderRadius: 14,
                padding: 16,
                display: 'flex',
                alignItems: 'flex-start',
                gap: 12,
                cursor: card.onClick ? 'pointer' : 'default',
                transition: 'background 0.2s',
              }}
              onMouseEnter={(e) => {
                if (card.onClick) e.currentTarget.style.background = 'var(--background-700)'
              }}
              onMouseLeave={(e) => {
                if (card.onClick) e.currentTarget.style.background = 'var(--background-800)'
              }}
            >
              <div style={{
                width: 36,
                height: 36,
                borderRadius: 10,
                background: card.bg,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                color: card.color,
              }}>
                {card.icon}
              </div>
              <div>
                <div style={{ fontSize: 22, fontWeight: 700, color: 'var(--text-50)', lineHeight: 1.2 }}>
                  {card.value}
                </div>
                <div style={{ fontSize: 12, color: 'var(--text-400)' }}>{card.label}</div>
              </div>
            </div>
          </Col>
        ))}
      </Row>

      {/* Quick Actions Row */}
      <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
        <Col xs={24} md={12}>
          <div style={{
            background: 'var(--background-800)',
            border: '1px solid var(--border-default)',
            borderRadius: 14,
            padding: 16,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
          }}>
            <div>
              <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-50)' }}>管理房间</div>
              <div style={{ fontSize: 12, color: 'var(--text-400)' }}>管理多个直播房间的录制配置</div>
            </div>
            <Button
              type="primary"
              icon={<SettingOutlined />}
              onClick={() => navigate('/workbench')}
            >
              管理房间
            </Button>
          </div>
        </Col>
      </Row>

      {/* Disk Usage */}
      <div style={{
        background: 'var(--background-800)',
        border: '1px solid var(--border-default)',
        borderRadius: 14,
        padding: 16,
        marginBottom: 24,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
          <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-50)' }}>磁盘存储</div>
          {diskUsage ? (
            <div style={{ fontSize: 12, color: 'var(--text-400)' }}>
              已用 {bytesToGB(diskUsage.used).toFixed(1)} GB / 共 {bytesToGB(diskUsage.total).toFixed(1)} GB，剩余 {bytesToGB(diskUsage.free).toFixed(1)} GB
            </div>
          ) : (
            <div style={{ fontSize: 12, color: 'var(--text-400)' }}>磁盘信息加载中…</div>
          )}
        </div>
        {diskUsage ? (
          <Progress
            percent={Number(diskPercent.toFixed(1))}
            showInfo={false}
            strokeColor={{ from: 'var(--brand-500)', to: 'var(--brand-400)' }}
            trailColor="var(--background-700)"
            strokeLinecap="round"
          />
        ) : (
          <div style={{ height: 8, background: 'var(--background-700)', borderRadius: 4 }} />
        )}
        {diskUsage && diskPercent > 90 && (
          <Alert
            type="warning"
            message="磁盘空间不足，请及时清理"
            banner
            style={{ marginTop: 12 }}
          />
        )}
      </div>

      {/* Recent Sessions Section */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        marginBottom: 12,
      }}>
        <h2 style={{ fontSize: 15, fontWeight: 600, color: 'var(--text-50)', margin: 0 }}>
          最近录制历史
        </h2>
        <div style={{ display: 'flex', gap: 8 }}>
          <Input
            prefix={<SearchOutlined />}
            placeholder="搜索主播/标题..."
            value={searchText}
            onChange={e => setSearchText(e.target.value)}
            style={{ width: 200 }}
            size="small"
            allowClear
          />
          <Select
            value={platformFilter}
            onChange={setPlatformFilter}
            style={{ width: 110 }}
            size="small"
            options={[
              { value: 'all', label: '全部平台' },
              ...Array.from(new Set(rooms.map(r => r.platform_name).filter(Boolean))).map(p => ({
                value: p,
                label: p,
              })),
            ]}
          />
        </div>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {recentHistory.length === 0 ? (
          <div style={{
            background: 'var(--background-800)',
            border: '1px solid var(--border-default)',
            borderRadius: 10,
            padding: 40,
            textAlign: 'center',
          }}>
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description="暂无录制记录"
            />
          </div>
        ) : (
          recentHistory.map((room) => (
            <div
              key={room.room_id}
              role="button"
              tabIndex={0}
              onClick={() => handleHistoryClick(room.room_id)}
              onKeyDown={(e) => {
                // Enter/Space 触发与点击相同的跳转逻辑
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  handleHistoryClick(room.room_id)
                }
              }}
              style={{
                background: 'var(--background-800)',
                border: '1px solid var(--border-default)',
                borderRadius: 10,
                padding: '12px 16px',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                cursor: 'pointer',
                transition: 'background 0.2s',
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.background = 'var(--background-700)'
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.background = 'var(--background-800)'
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <div style={{
                  width: 36,
                  height: 36,
                  borderRadius: 8,
                  background: 'var(--background-700)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  color: 'var(--text-400)',
                }}>
                  <VideoCameraOutlined />
                </div>
                <div>
                  <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--text-50)' }}>
                    {room.streamer_name || '未知主播'}
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--text-400)' }}>
                    {room.platform_name} · {formatDate(room.record_started_at)}
                  </div>
                </div>
              </div>

              <div style={{ display: 'flex', alignItems: 'center', gap: 20 }}>
                <div style={{ textAlign: 'center' }}>
                  <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--text-50)' }}>
                    {formatSize(room.record_size_mb)}
                  </div>
                  <div style={{ fontSize: 10, color: 'var(--text-400)' }}>大小</div>
                </div>
                <div style={{ textAlign: 'center' }}>
                  <div style={{
                    fontSize: 13,
                    fontWeight: 500,
                    color: room.is_recording ? 'var(--state-success)' : 'var(--text-50)',
                  }}>
                    {room.is_recording ? '录制中' : '已停止'}
                  </div>
                  <div style={{ fontSize: 10, color: 'var(--text-400)' }}>状态</div>
                </div>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  )
}
