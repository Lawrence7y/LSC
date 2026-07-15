import { useEffect, useState, type CSSProperties } from 'react'
import { Alert, Badge, Card, Progress, Space, Tag, Typography } from 'antd'
import { ContinuousAnalysisStatus } from '@/types'

export interface ExportSummary {
  /** 切片列表中待确认/待调（不是导出入队） */
  pendingConfirm: number
  /** 已进入导出队列 */
  queued: number
  exporting: number
  completed: number
  failed: number
  /** 切片列表条数（入列） */
  listed: number
}

function formatDuration(seconds: number) {
  if (!Number.isFinite(seconds) || seconds <= 0) return '0s'
  const total = Math.floor(seconds)
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  return h > 0 ? `${h}h ${m}m ${s}s` : m > 0 ? `${m}m ${s}s` : `${s}s`
}

function formatRange(range?: [number, number]) {
  if (!range) return '—'
  return `${formatDuration(range[0])} → ${formatDuration(range[1])}`
}

const ROUND_PHASE_LABEL: Record<string, string> = {
  unknown: '寻找回合',
  buy: '买枪期',
  pre_combat: '等待开战',
  combat: '交战中',
  post_combat: '等待结束',
  intermission: '局间暂停',
}

export function AnalysisProgress({ status, compact = false, exportSummary }: { status: ContinuousAnalysisStatus | null; compact?: boolean; exportSummary?: ExportSummary }) {
  const current = status as ContinuousAnalysisStatus
  const summary = exportSummary ?? {
    pendingConfirm: 0, queued: 0, exporting: 0, completed: 0, failed: 0, listed: 0,
  }

  // 每秒 tick：驱动"x秒前更新"和录制时长乐观更新
  const [, setTick] = useState(0)
  useEffect(() => {
    if (!status?.running) return
    const timer = setInterval(() => setTick((t) => t + 1), 1000)
    return () => clearInterval(timer)
  }, [status?.running])

  if (!current) return null
  const hasContent = Boolean(current.running || current.phase === 'completed' || current.phase === 'finalizing')
  const waitingForRecording = current.analysis_stage === '等待新录制'
  const waitingForFinalize = current.analysis_stage === '等待收尾' || current.analysis_stage === '收尾中' || current.analysis_stage === '收尾失败' || current.phase === 'finalizing'
  const waitingForNextChunk = current.analysis_stage === '等待新片段' || current.analysis_stage === '等待可分析片段'
  const phaseDetailLabel = current.analysis_stage === '收尾失败'
    ? '收尾失败'
    : waitingForFinalize
      ? '全文件收尾精修'
      : (current.round_phase_detail || ROUND_PHASE_LABEL[current.round_phase || ''] || current.round_phase)
  const scanElapsed = current.scan_elapsed_sec ?? 0
  const isScanning = current.scan_running || current.analysis_stage === '扫描中'
  const scanningStatusText = scanElapsed > 0 ? `扫描中 ${Math.floor(scanElapsed)}s` : '扫描中'
  const statusText = waitingForRecording
    ? '等待录制'
    : current.analysis_stage === '收尾失败'
      ? '收尾失败'
    : waitingForFinalize
      ? (current.analysis_stage === '收尾中' || current.phase === 'finalizing' ? '收尾中' : '等待收尾')
    : waitingForNextChunk
      ? current.analysis_stage
    : isScanning
      ? scanningStatusText
    : current.phase === 'completed'
    ? (summary.pendingConfirm > 0 ? '分析完成·待确认' : '已完成')
    : current.phase === 'finalizing'
      ? '收尾中'
      : current.scan_reason === 'audio_increment'
        ? '音频推进中'
        : current.scan_reason === 'finalize'
          ? '收尾扫描中'
          : '运行中'

  if (!hasContent) {
    return compact ? <Typography.Text type="secondary">持续分析未运行</Typography.Text> : <Card size="small" style={{ minWidth: 320 }}><Typography.Text type="secondary">持续分析未运行</Typography.Text></Card>
  }

  if (current.phase === 'error' || current.error) {
    return <Alert type="error" showIcon message="持续分析异常" description={current.error ?? '请重试或查看日志'} />
  }

  const scanModeLabel = current.scan_mode === 'incremental' ? '增量扫描' : '全量重扫'
  const phaseLabel = current.scan_phase === 'incremental' ? '局部窗口' : current.scan_phase === 'full' ? '全量重扫' : scanModeLabel
  const reasonLabel = current.scan_reason === 'finalize' ? '收尾' : current.scan_reason === 'audio_increment' ? '音频推进' : current.scan_reason === 'scanning' ? '扫描中' : ''
  const isWorkerActive = current.phase === 'finalizing' || current.scan_running
  const percent = typeof current.progress === 'number'
    ? current.progress
    : Math.min(100, Math.max(0, ((current.analyzed_duration ?? 0) / Math.max((current.scan_range?.[1] ?? current.analyzed_duration ?? 1), 1)) * 100))
  const displayPercent = isWorkerActive ? Math.min(percent, 95) : percent
  const finalizeHint = (current.phase === 'finalizing' && scanElapsed > 0)
    ? `（已运行 ${Math.floor(scanElapsed)}s，首次约 1–2 分钟）`
    : ''
  const title = current.phase === 'completed'
    ? (summary.pendingConfirm > 0
      ? `分析已完成（${summary.pendingConfirm} 条待确认）`
      : '持续分析已完成')
    : current.phase === 'finalizing'
      ? `持续分析收尾中${finalizeHint}`
      : `${scanModeLabel}中`
  const statusTag = waitingForRecording || waitingForNextChunk
    ? <Tag>{statusText}</Tag>
    : current.phase === 'finalizing'
    ? <Tag color="gold">收尾中{finalizeHint}</Tag>
    : current.phase === 'completed'
      ? <Tag color="green">{summary.pendingConfirm > 0 ? '分析完成·待确认' : '已完成'}</Tag>
      : <Tag color="blue">{phaseLabel}{reasonLabel ? ` · ${reasonLabel}` : ''}</Tag>

  if (compact) {
    const updatedAt = current.updated_at ?? 0
    const liveAnalyzedDuration = current.analyzed_duration ?? 0
    const scanEnd = current.scan_range?.[1] ?? liveAnalyzedDuration
     const rawPercent = scanEnd > 0 ? Math.min(100, Math.max(0, (liveAnalyzedDuration / scanEnd) * 100)) : 0
     const livePercent = isWorkerActive ? Math.min(rawPercent, 95) : rawPercent
     const isIdle = waitingForRecording || waitingForNextChunk || (waitingForFinalize && current.analysis_stage === '等待收尾')
    const dotColor = isIdle ? 'var(--text-400, #888780)' : current.phase === 'completed' ? 'var(--state-success, #1D9E75)' : 'var(--brand-500, #378ADD)'
    const modeLabel = current.mode === 'scene' ? '场景' : current.mode === 'valorant_round' ? '回合' : (current.mode ?? '场景')
    const secondsAgo = updatedAt > 0 ? Math.max(0, Math.floor(Date.now() / 1000 - updatedAt)) : 0
    const showProgress = !isIdle && current.phase !== 'completed' && scanEnd > 0
    const dividerStyle: CSSProperties = { borderLeft: '0.5px solid var(--border-default, rgba(128,128,128,0.15))', paddingLeft: 14 }
    const roomLabel = current.room_id
      ? (current.room_id.length > 10 ? `${current.room_id.slice(0, 8)}…` : current.room_id)
      : null

    return (
      <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 14, fontVariantNumeric: 'tabular-nums', fontSize: 13, minWidth: 0 }}>
        <style>{`@keyframes caPulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:0.5;transform:scale(0.8)}}`}</style>

        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{
            width: 8, height: 8, borderRadius: '50%', background: dotColor, flexShrink: 0,
            animation: isIdle ? 'none' : 'caPulse 1.8s ease-in-out infinite',
          }} />
          <span style={{ fontWeight: 500, color: 'var(--text-50)' }}>{statusText}</span>
          {roomLabel && (
            <span style={{
              background: 'var(--background-700)',
              color: 'var(--text-300)',
              borderRadius: 4, padding: '1px 8px', fontSize: 11, fontWeight: 500,
            }} title={current.room_id || undefined}>主房 {roomLabel}</span>
          )}
          <span style={{
            background: 'var(--background-700)',
            color: 'var(--text-300)',
            borderRadius: 4, padding: '1px 8px', fontSize: 11, fontWeight: 500,
          }}>{modeLabel}</span>
        </div>

        {showProgress && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, ...dividerStyle }}>
            <div style={{ width: 100, height: 5, background: 'var(--background-700)', borderRadius: 3, overflow: 'hidden' }}>
              <div style={{ width: `${livePercent}%`, height: '100%', background: 'var(--brand-600)', borderRadius: 3, transition: 'width 0.5s ease' }} />
            </div>
            <span style={{ fontWeight: 500, color: 'var(--text-50)', minWidth: 32 }}>{Math.round(livePercent)}%</span>
            <span style={{ fontSize: 12, color: 'var(--text-400)' }}>{formatDuration(liveAnalyzedDuration)} / {formatDuration(scanEnd)}</span>
          </div>
        )}

        <div style={{ display: 'flex', alignItems: 'center', gap: 4, ...dividerStyle }}>
          <span style={{ fontWeight: 500, color: 'var(--text-50)', fontSize: 15 }}>
            {summary.listed > 0 ? summary.listed : (current.total_highlights ?? 0)}
          </span>
          <span style={{ fontSize: 12, color: 'var(--text-400)' }}>入列</span>
          {(current.total_highlights ?? 0) > 0
            && summary.listed > 0
            && (current.total_highlights ?? 0) !== summary.listed && (
            <span style={{ fontSize: 11, color: 'var(--text-400)' }} title="后端检出回合数（含未入列）">
              · 检出 {current.total_highlights}
            </span>
          )}
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 6, ...dividerStyle }}>
          {(summary.pendingConfirm > 0 || (current.pending_rounds ?? 0) > 0) && (
            <span style={{ background: 'var(--state-warning-surface)', color: 'var(--state-warning)', borderRadius: 'var(--radius-md)', padding: '2px 8px', fontSize: 12 }}>
              待调 {summary.pendingConfirm > 0 ? summary.pendingConfirm : current.pending_rounds}
            </span>
          )}
          {(current.confirmed_rounds ?? 0) > 0 && (
            <span
              style={{ background: 'var(--state-success-surface)', color: 'var(--state-success)', borderRadius: 'var(--radius-md)', padding: '2px 8px', fontSize: 12 }}
              title="OCR 边界可信、可确认导出（不是「已全部导出」）"
            >
              OCR可导 {current.confirmed_rounds}
            </span>
          )}
          {current.round_phase && current.mode === 'valorant_round' && (
            <span style={{ background: 'rgba(49,179,174,0.12)', color: 'var(--brand-700)', borderRadius: 'var(--radius-md)', padding: '2px 8px', fontSize: 12 }}>
              {phaseDetailLabel}
            </span>
          )}
          {current.pending_round && (
            <span style={{ background: 'var(--state-warning-surface)', color: 'var(--state-warning)', borderRadius: 'var(--radius-md)', padding: '2px 8px', fontSize: 12 }}>
              等待回合结束
            </span>
          )}
        </div>

        <div
          style={{ display: 'flex', alignItems: 'center', gap: 3, ...dividerStyle, fontSize: 12, color: 'var(--text-400)' }}
          title="导出队列：排队 / 导出中 / 已完成 / 失败（不含待确认）"
        >
          <span>导出</span>
          <span style={{ color: 'var(--text-200)', fontWeight: 500 }}>{summary.queued}</span>
          <span>/</span>
          <span style={{ color: 'var(--text-200)', fontWeight: 500 }}>{summary.exporting}</span>
          <span>/</span>
          <span style={{ color: 'var(--text-200)', fontWeight: 500 }}>{summary.completed}</span>
          <span>/</span>
          <span style={{ color: summary.failed > 0 ? 'var(--state-error, #c00)' : 'var(--text-200)', fontWeight: 500 }}>{summary.failed}</span>
        </div>

        {updatedAt > 0 && (
          <div style={{ ...dividerStyle, fontSize: 11, color: 'var(--text-400)' }}>
            {secondsAgo <= 1 ? '刚刚' : `${secondsAgo}秒前`}
          </div>
        )}
      </div>
    )
  }

  return (
    <Card size="small" style={{ minWidth: 320 }}>
      <Space direction="vertical" size={6} style={{ width: '100%' }}>
        <Space wrap>
          <Badge status={waitingForRecording || waitingForNextChunk ? 'default' : current.phase === 'completed' ? 'success' : 'processing'} />
          <Typography.Text strong>{title}</Typography.Text>
          <Tag>{current.mode ?? 'scene'}</Tag>
          {statusTag}
        </Space>
        <Typography.Text type="secondary">主房间：{current.room_id ?? '-'}</Typography.Text>
        <Typography.Text type="secondary">扫描范围：{formatRange(current.scan_range)}</Typography.Text>
        <Typography.Text type="secondary">扫描方式：{phaseLabel}{reasonLabel ? ` · ${reasonLabel}` : ''}{current.refine_with_ocr ? ' · OCR 校正' : ''}</Typography.Text>
        <Typography.Text type="secondary">建议间隔：{typeof current.effective_interval === 'number' ? `${current.effective_interval}s` : '—'}</Typography.Text>
        <Progress percent={Math.round(displayPercent)} status={current.phase === 'completed' ? 'success' : 'active'} showInfo />
        <Typography.Text type="secondary">进度：{Math.round(displayPercent)}%{isWorkerActive && percent >= 95 ? '（后台精修中，完成后自动更新）' : ''}</Typography.Text>
        <Typography.Text type="secondary">
          已分析：{formatDuration(current.analyzed_duration ?? 0)}
          {' · '}入列：{summary.listed || (current.total_highlights ?? 0)}
          {(current.total_highlights ?? 0) > 0 && summary.listed > 0 && (current.total_highlights ?? 0) !== summary.listed
            ? ` · 检出：${current.total_highlights}`
            : ''}
        </Typography.Text>
        <Typography.Text type="secondary">
          已录制：{formatDuration(current.recorded_duration ?? 0)}
          {' · '}OCR可导：{current.confirmed_rounds ?? 0}
          {' · '}待调：{summary.pendingConfirm || (current.pending_rounds ?? 0)}
        </Typography.Text>
        {current.round_phase && current.mode === 'valorant_round' && (
          <Typography.Text type="secondary">回合相位：{phaseDetailLabel}{current.pending_round && !waitingForFinalize ? ' · 等待回合结束' : ''}</Typography.Text>
        )}
        <Typography.Text type="secondary">当前阶段：{current.analysis_stage ?? phaseLabel}</Typography.Text>
        {(waitingForFinalize || current.phase === 'finalizing') && (
          <Alert
            type="info"
            showIcon
            message={current.phase === 'finalizing' ? '正在进行最终回合确认（首次约 1–2 分钟）' : '请先结束录制，并等待收尾完成'}
            description="持续分析会在停录后做一次全文件 OCR 精修，把待确认回合升格为「AI可导」。升格后仍需你确认/导出；收尾完成前请勿关闭分析。"
          />
        )}
        {current.running && !waitingForFinalize && current.phase === 'running' && (
          <Typography.Text type="secondary">
            提示：结束时请先停录，再等分析收尾；回合入列后需确认再导出（OCR 升格不会自动导出）。
          </Typography.Text>
        )}
        <Typography.Text type="secondary">
          导出队列：排队 {summary.queued} · 导出中 {summary.exporting} · 已完成 {summary.completed} · 失败 {summary.failed}
          {summary.pendingConfirm > 0 ? ` · 另有待调 ${summary.pendingConfirm}` : ''}
        </Typography.Text>
        {typeof current.scan_timeout === 'number' && (
          <Typography.Text type="secondary">超时：{current.scan_timeout}s · OCR：{current.refine_with_ocr ? '启用' : '未启用'}</Typography.Text>
        )}
        {current.updated_at && (
          <Typography.Text type="secondary">更新时间：{new Date(current.updated_at).toLocaleTimeString()}</Typography.Text>
        )}
        {current.phase === 'completed' && (
          <Alert
            type={summary.pendingConfirm > 0 ? 'warning' : 'success'}
            showIcon
            message={
              summary.pendingConfirm > 0
                ? `分析收尾已完成，还有 ${summary.pendingConfirm} 条待确认后再导出`
                : `分析完成，共入列 ${summary.listed || (current.total_highlights ?? 0)} 个回合`
            }
          />
        )}
      </Space>
    </Card>
  )
}
