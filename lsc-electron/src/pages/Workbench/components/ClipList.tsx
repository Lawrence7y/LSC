import { Card, List, Button, Space, Tag, Empty, Progress } from 'antd'
import { DeleteOutlined, ExportOutlined, FolderOpenOutlined, FolderOutlined, CloseCircleOutlined } from '@ant-design/icons'
import { ClipSegment } from '@/types'
import { formatTime } from '@/utils/time'

export interface ExportProgressInfo {
  percent: number
  elapsed: number
  total: number
}

interface ClipListProps {
  clips: ClipSegment[]
  onDelete: (index: number) => void
  onExport: (clip: ClipSegment, index: number) => void
  onExportMany?: (clips: ClipSegment[]) => void
  onOpenFile?: (path: string) => void
  onOpenFolder?: (path: string) => void
  onCancelExport?: (jobId: string) => void
  exportProgress?: Record<string, ExportProgressInfo>
}

function formatDuration(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  if (m > 0) return `${m}分${s}秒`
  return `${s}秒`
}

export function ClipList({ clips, onDelete, onExport, onExportMany, onOpenFile, onOpenFolder, onCancelExport, exportProgress }: ClipListProps) {
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
        <Space size={8}>
          {clips.length > 1 && onExportMany && (
            <Button type="link" size="small" onClick={() => onExportMany(clips)}>
              导出全部
            </Button>
          )}
          <span style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>
            {clips.length} 个切片
          </span>
        </Space>
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
          renderItem={(clip, index) => {
            const prog = clip.job_id ? exportProgress?.[clip.job_id] : undefined
            const isExporting = !!prog
            return (
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
                      {/* 已导出：打开文件 + 打开文件夹 + 重新导出 */}
                      {clip.exported && clip.outputPath && (
                        <>
                          <Button
                            type="text"
                            size="small"
                            icon={<FolderOpenOutlined />}
                            onClick={() => onOpenFile?.(clip.outputPath!)}
                          />
                          <Button
                            type="text"
                            size="small"
                            icon={<FolderOutlined />}
                            onClick={() => onOpenFolder?.(clip.outputPath!)}
                          />
                        </>
                      )}
                      {/* 导出中：取消按钮 */}
                      {isExporting && onCancelExport && clip.job_id && (
                        <Button
                          type="text"
                          size="small"
                          icon={<CloseCircleOutlined />}
                          danger
                          onClick={() => { if (clip.job_id) onCancelExport(clip.job_id) }}
                        />
                      )}
                      {/* 导出按钮：未导出或已导出（重新导出）都显示 */}
                      {!isExporting && (
                        <Button
                          type="text"
                          size="small"
                          icon={<ExportOutlined />}
                          onClick={() => onExport(clip, index)}
                        />
                      )}
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
                    alignItems: 'center',
                  }}>
                    <span>{formatTime(clip.start)} → {formatTime(clip.end)}</span>
                    <Tag style={{ margin: 0 }}>
                      {formatDuration(clip.end - clip.start)}
                    </Tag>
                    {(clip.mark_precision === 'approximate' ||
                      (clip.mark_precision !== 'exact' &&
                        !clip.clip_snapshot_id &&
                        (clip.mark_in_wallclock == null || clip.mark_out_wallclock == null))) && (
                      <Tag color="orange" style={{ margin: 0 }} title="拖拽标记无墙钟，导出可能偏差数秒；精确请用 I/O 键">
                        近似
                      </Tag>
                    )}
                    {isExporting && (
                      <Tag color="blue" style={{ margin: 0 }}>
                        导出中 {prog.percent.toFixed(0)}%
                      </Tag>
                    )}
                    {clip.exported && !isExporting && (
                      <Tag color="green" style={{ margin: 0 }}>已导出</Tag>
                    )}
                  </div>
                  {/* 导出进度条 */}
                  {isExporting && (
                    <Progress
                      percent={prog.percent}
                      size="small"
                      status="active"
                      style={{ marginTop: 4, marginBottom: 0 }}
                    />
                  )}
                </div>
              </List.Item>
            )
          }}
        />
      )}
    </Card>
  )
}
