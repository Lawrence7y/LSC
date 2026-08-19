import { useMemo } from 'react'
import { Button, Tooltip } from 'antd'
import { CloseOutlined, LoadingOutlined } from '@ant-design/icons'
import type { ClipSegment } from '@/types'
import type { ExportProgressInfo } from './ClipList'
import { useI18n } from '@/i18n'

interface ExportQueuePanelProps {
  clips: ClipSegment[]
  exportProgress: Record<string, ExportProgressInfo>
  onCancelExport?: (jobId: string) => void
}

function clampPct(v: number): number {
  return Math.max(0, Math.min(100, v))
}

/**
 * 导出队列全局面板。
 *
 * 仅在存在进行中（queued/exporting）的导出任务时显示，提供批量导出的全局视角：
 * - 总体进度（已完成 + 进行中加权 / 本批次总数）
 * - 进行中任务清单（标签 + 百分比）
 * - 一键取消全部
 *
 * 单条进度仍在切片列表内展示；本面板聚焦「整批走到哪了」。
 */
export function ExportQueuePanel({ clips, exportProgress, onCancelExport }: ExportQueuePanelProps) {
  const { t } = useI18n()
  const { active, completed, failed } = useMemo(() => {
    const active: { clip: ClipSegment; percent: number }[] = []
    let completed = 0
    let failed = 0
    for (const c of clips) {
      const status = c.export_status ?? (c.exported ? 'completed' : undefined)
      if (status === 'queued' || status === 'exporting') {
        const prog = c.job_id ? exportProgress[c.job_id] : undefined
        active.push({ clip: c, percent: clampPct(prog?.percent ?? 0) })
      } else if (status === 'completed') {
        completed += 1
      } else if (status === 'failed') {
        failed += 1
      }
    }
    return { active, completed, failed }
  }, [clips, exportProgress])

  // 无进行中任务时不渲染（避免常驻占位）
  if (active.length === 0) return null

  const batchTotal = active.length + completed + failed
  const activeSum = active.reduce((acc, item) => acc + item.percent, 0)
  const overallPct = batchTotal > 0
    ? clampPct((completed * 100 + activeSum) / batchTotal)
    : 0

  const handleCancelAll = () => {
    for (const item of active) {
      const jobId = item.clip.job_id
      if (jobId) onCancelExport?.(jobId)
    }
  }

  return (
    <div className="export-queue-panel">
      <div className="export-queue-panel__head">
        <span className="export-queue-panel__title">
          <LoadingOutlined style={{ marginRight: 6 }} />
          {t('导出队列')}
          <span className="export-queue-panel__count">
            {t('{active} 进行中 · {completed} 完成', { active: active.length, completed })}
            {failed > 0 ? t(' · {failed} 失败', { failed }) : ''}
          </span>
        </span>
        <Tooltip title={t('取消全部进行中的导出')}>
          <Button
            type="text"
            size="small"
            danger
            icon={<CloseOutlined />}
            onClick={handleCancelAll}
          >
            {t('全部取消')}
          </Button>
        </Tooltip>
      </div>

      <div className="export-queue-panel__overall">
        <span className="export-queue-panel__overall-track">
          <span className="export-queue-panel__overall-fill" style={{ width: `${overallPct}%` }} />
        </span>
        <span className="export-queue-panel__overall-pct">{overallPct.toFixed(0)}%</span>
      </div>

      <div className="export-queue-panel__list">
        {active.map(({ clip, percent }) => (
          <div key={clip.job_id || `${clip.room_id}-${clip.start}-${clip.end}`} className="export-queue-panel__item">
            <span className="export-queue-panel__item-label" title={clip.label}>{clip.label}</span>
            <span className="export-queue-panel__item-track">
              <span className="export-queue-panel__item-fill" style={{ width: `${percent}%` }} />
            </span>
            <span className="export-queue-panel__item-pct">
              {percent > 0 ? `${percent.toFixed(0)}%` : t('排队')}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}
