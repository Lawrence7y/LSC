import { Card, List, Button, Space, Tag, Empty } from 'antd'
import { DeleteOutlined, ExportOutlined } from '@ant-design/icons'
import { ClipSegment } from '@/types'
import { formatTime } from '@/utils/time'

interface ClipListProps {
  clips: ClipSegment[]
  onDelete: (index: number) => void
  onExport: (clip: ClipSegment) => void
}

// 本地独有：以「X分Y秒」形式展示时长（与 RoomCard 的 HH:MM:SS 用途不同，故未抽到 utils）
function formatDuration(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  if (m > 0) {
    return `${m}分${s}秒`
  }
  return `${s}秒`
}

export function ClipList({ clips, onDelete, onExport }: ClipListProps) {
  return (
    <Card 
      size="small" 
      title="切片列表"
      style={{ 
        margin: '8px 16px 16px',
        flex: 1,
        overflow: 'hidden',
        display: 'flex',
        flexDirection: 'column',
        background: 'var(--bg-secondary)',
      }}
      styles={{ 
        body: { 
          flex: 1, 
          overflow: 'auto',
          padding: '0 8px 8px',
        } 
      }}
      extra={
        <span style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>
          {clips.length} 个切片
        </span>
      }
    >
      {clips.length === 0 ? (
        <Empty 
          image={Empty.PRESENTED_IMAGE_SIMPLE} 
          description="暂无切片"
          style={{ margin: '20px 0' }}
        />
      ) : (
        <List
          dataSource={clips}
          renderItem={(clip, index) => (
            <List.Item
              style={{ 
                padding: '8px 12px',
                background: 'var(--bg-tertiary)',
                borderRadius: 6,
                marginBottom: 8,
              }}
            >
              <div style={{ width: '100%' }}>
                <div style={{ 
                  display: 'flex', 
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  marginBottom: 4,
                }}>
                  <span style={{ fontWeight: 500, fontSize: 13 }}>
                    {clip.label}
                  </span>
                  <Space size={4}>
                    <Button 
                      type="text" 
                      size="small" 
                      icon={<ExportOutlined />}
                      onClick={() => onExport(clip)}
                    />
                    <Button 
                      type="text" 
                      size="small" 
                      icon={<DeleteOutlined />}
                      danger
                      onClick={() => onDelete(index)}
                    />
                  </Space>
                </div>
                <div style={{ 
                  fontSize: 12, 
                  color: 'var(--text-tertiary)',
                  display: 'flex',
                  gap: 8,
                }}>
                  <span>{formatTime(clip.start)} → {formatTime(clip.end)}</span>
                  <Tag style={{ margin: 0 }}>
                    {formatDuration(clip.end - clip.start)}
                  </Tag>
                </div>
              </div>
            </List.Item>
          )}
        />
      )}
    </Card>
  )
}
