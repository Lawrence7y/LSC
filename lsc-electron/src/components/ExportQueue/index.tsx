import { Card, Progress, Button, Space, Tag } from 'antd'
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  LoadingOutlined,
  PauseCircleOutlined,
  FolderOpenOutlined,
  DeleteOutlined,
  ReloadOutlined,
} from '@ant-design/icons'

export type ExportJobStatus = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'

export interface ExportJob {
  /** Unique job ID */
  id: string
  /** Room name */
  roomName: string
  /** Clip label */
  label: string
  /** Start time formatted */
  startTime: string
  /** Original start time in seconds (for retry) */
  startSeconds: number
  /** Original end time in seconds (for retry) */
  endSeconds: number
  /** Duration in seconds */
  duration: number
  /** Progress percentage 0-100 */
  progress: number
  /** Current status */
  status: ExportJobStatus
  /** Output file path (when completed) */
  outputPath?: string
  /** Error message (when failed) */
  error?: string
  /** Room ID for reference */
  roomId: string
  /** Created timestamp */
  createdAt: number
}

interface ExportQueueProps {
  jobs: ExportJob[]
  onCancel?: (jobId: string) => void
  onRetry?: (jobId: string) => void
  onRemove?: (jobId: string) => void
  onOpenFolder?: (outputPath: string) => void
  onClearCompleted?: () => void
}

const statusConfig: Record<ExportJobStatus, { color: string; icon: React.ReactNode; label: string }> = {
  pending:   { color: '#8e8e93', icon: <PauseCircleOutlined />, label: '等待中' },
  running:   { color: '#007aff', icon: <LoadingOutlined />, label: '导出中' },
  completed: { color: '#34c759', icon: <CheckCircleOutlined />, label: '已完成' },
  failed:    { color: '#ff3b30', icon: <CloseCircleOutlined />, label: '失败' },
  cancelled: { color: '#ff9500', icon: <CloseCircleOutlined />, label: '已取消' },
}

function formatDuration(seconds: number): string {
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = Math.floor(seconds % 60)
  if (h > 0) return `${h}h ${m}m ${s}s`
  if (m > 0) return `${m}m ${s}s`
  return `${s}s`
}

export function ExportQueue({
  jobs,
  onCancel,
  onRetry,
  onRemove,
  onOpenFolder,
  onClearCompleted,
}: ExportQueueProps) {
  const activeJobs = jobs.filter(j => j.status === 'pending' || j.status === 'running')
  const completedJobs = jobs.filter(j => j.status === 'completed')
  const failedJobs = jobs.filter(j => j.status === 'failed')
  const hasCompleted = completedJobs.length > 0

  if (jobs.length === 0) {
    return null
  }

  return (
    <Card
      size="small"
      title={
        <Space>
          <span>导出队列</span>
          {activeJobs.length > 0 && (
            <Tag color="blue">{activeJobs.length} 进行中</Tag>
          )}
          {completedJobs.length > 0 && (
            <Tag color="green">{completedJobs.length} 已完成</Tag>
          )}
          {failedJobs.length > 0 && (
            <Tag color="red">{failedJobs.length} 失败</Tag>
          )}
        </Space>
      }
      extra={
        hasCompleted ? (
          <Button type="text" size="small" onClick={onClearCompleted}>
            清除已完成
          </Button>
        ) : null
      }
      style={{
        margin: '8px 16px',
        background: 'var(--bg-secondary)',
        maxHeight: 240,
        overflow: 'hidden',
      }}
      styles={{
        body: {
          padding: '8px 12px',
          maxHeight: 170,
          overflow: 'auto',
        },
      }}
    >
      <Space direction="vertical" style={{ width: '100%' }} size={8}>
        {jobs.map(job => {
          const cfg = statusConfig[job.status]
          return (
            <div
              key={job.id}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 10,
                padding: '6px 8px',
                borderRadius: 6,
                background: 'var(--bg-tertiary)',
              }}
            >
              {/* Status icon */}
              <span style={{ color: cfg.color, fontSize: 14, flexShrink: 0 }}>
                {cfg.icon}
              </span>

              {/* Info + Progress */}
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{
                  fontSize: 12,
                  fontWeight: 500,
                  color: 'var(--text-primary)',
                  whiteSpace: 'nowrap',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                }}>
                  {job.roomName} · {job.label}
                </div>
                <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 2 }}>
                  {job.startTime} · {formatDuration(job.duration)}
                  {job.status === 'failed' && job.error && (
                    <span style={{ color: 'var(--state-error)', marginLeft: 8 }}>
                      {job.error}
                    </span>
                  )}
                </div>
                {(job.status === 'running' || 'progress' in job) && (
                  <Progress
                    percent={Math.round(job.progress)}
                    size="small"
                    strokeColor={cfg.color}
                    style={{ marginTop: 4, marginBottom: 0 }}
                  />
                )}
              </div>

              {/* Actions */}
              <Space size={4} style={{ flexShrink: 0 }}>
                {job.status === 'running' && onCancel && (
                  <Button type="text" size="small" danger onClick={() => onCancel(job.id)}>
                    取消
                  </Button>
                )}
                {job.status === 'failed' && onRetry && (
                  <Button
                    type="text"
                    size="small"
                    icon={<ReloadOutlined />}
                    onClick={() => onRetry(job.id)}
                  />
                )}
                {job.status === 'completed' && job.outputPath && onOpenFolder && (
                  <Button
                    type="text"
                    size="small"
                    icon={<FolderOpenOutlined />}
                    onClick={() => onOpenFolder(job.outputPath!)}
                  />
                )}
                {(job.status === 'completed' || job.status === 'failed' || job.status === 'cancelled') && onRemove && (
                  <Button
                    type="text"
                    size="small"
                    icon={<DeleteOutlined />}
                    onClick={() => onRemove(job.id)}
                  />
                )}
              </Space>
            </div>
          )
        })}
      </Space>
    </Card>
  )
}
