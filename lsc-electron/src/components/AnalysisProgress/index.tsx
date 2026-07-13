import { Space, Tag } from 'antd'
import type { ContinuousAnalysisStatus } from '@/types'

interface ExportSummary {
  queued: number
  exporting: number
  completed: number
  failed: number
}

interface AnalysisProgressProps {
  status: ContinuousAnalysisStatus | null | undefined
  compact?: boolean
  exportSummary?: ExportSummary
}

export function AnalysisProgress({ status, compact, exportSummary }: AnalysisProgressProps) {
  const running = status?.running || status?.phase === 'running' || status?.phase === 'finalizing'
  const totalHighlights = status?.total_highlights ?? 0
  const progress = status?.progress

  if (!running && !exportSummary) return null

  return (
    <Space size={8} wrap>
      {running && (
        <Tag color="processing">
          {compact ? '分析中' : `持续分析${progress != null ? ` ${Math.round(progress)}%` : ''}`}
          {totalHighlights > 0 ? ` · ${totalHighlights} 高光` : ''}
        </Tag>
      )}
      {exportSummary && (exportSummary.queued > 0 || exportSummary.exporting > 0) && (
        <Tag color="blue">
          导出队列 {exportSummary.queued + exportSummary.exporting}
        </Tag>
      )}
      {exportSummary && exportSummary.failed > 0 && (
        <Tag color="error">导出失败 {exportSummary.failed}</Tag>
      )}
    </Space>
  )
}
