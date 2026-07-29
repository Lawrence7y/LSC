import { useEffect, useState } from 'react'
import { Alert, Card, Typography } from 'antd'
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

const ROUND_PHASE_LABEL: Record<string, string> = {
  unknown: '寻找回合',
  buy: '买枪期',
  pre_combat: '等待开战',
  combat: '交战中',
  post_combat: '等待结束',
  intermission: '局间暂停',
}

type Tone = 'idle' | 'active' | 'warning' | 'success' | 'error'

interface PrimaryStatus {
  verb: string
  detail: string
  tone: Tone
  /** 引导动作：confirm=去确认切片 */
  nextAction?: 'confirm'
}

/**
 * 单一状态推导入口：把 phase / analysis_stage / scan_reason / round_phase
 * 多套后端状态收敛为「动词 + 一句人话 + 语气」，compact 与卡片模式共用。
 */
function derivePrimaryStatus(current: ContinuousAnalysisStatus, summary: ExportSummary): PrimaryStatus {
  const stage = current.analysis_stage ?? ''
  const listed = summary.listed || (current.total_highlights ?? 0)

  if (current.phase === 'error' || current.error) {
    return { verb: '持续分析异常', detail: current.error ?? '请重试或查看日志', tone: 'error' }
  }
  if (current.phase === 'completed') {
    if (summary.pendingConfirm > 0) {
      return { verb: '分析完成', detail: `入列 ${listed} 回合 · ${summary.pendingConfirm} 条待确认`, tone: 'warning', nextAction: 'confirm' }
    }
    return { verb: '已完成', detail: `入列 ${listed} 回合`, tone: 'success' }
  }
  if (current.phase === 'stopping' || stage === '停止中') {
    return { verb: '停止中…', detail: '等待扫描退出并释放任务槽', tone: 'idle' }
  }
  if (stage === '收尾失败') {
    return { verb: '收尾失败', detail: '可重新启动持续分析', tone: 'error' }
  }
  if (stage === '等待收尾') {
    return { verb: '等待收尾', detail: '请先停录后再完成收尾扫描', tone: 'idle', nextAction: undefined }
  }
  if (current.phase === 'finalizing' || stage === '收尾中') {
    const elapsed = Math.floor(current.scan_elapsed_sec ?? 0)
    return {
      verb: '收尾中',
      detail: elapsed > 0
        ? `最终回合确认 · 已运行 ${elapsed}s（首次约 1–2 分钟）`
        : '停录后做一次收尾扫描，尾部回合补入列表',
      tone: 'active',
    }
  }
  if (stage === '等待新录制') {
    return { verb: '等待录制', detail: '开始录制后自动跟进分析', tone: 'idle' }
  }
  if (stage === '等待可分析片段' || stage === '等待新片段') {
    return { verb: '等待片段', detail: '录制写入中，凑够窗口即扫描', tone: 'idle' }
  }
  const scanPart = (current.scan_range?.[1] ?? 0) > 0
    ? `扫描 ${formatDuration(current.scan_range?.[0] ?? 0)}–${formatDuration(current.scan_range?.[1] ?? 0)}`
    : ''
  const reasonPart = current.scan_reason === 'audio_increment'
    ? '音频推进'
    : current.scan_reason === 'finalize'
      ? '收尾'
      : ''
  const phasePart = current.mode === 'valorant_round'
    ? (current.round_phase_detail || ROUND_PHASE_LABEL[current.round_phase || ''] || '')
    : ''
  return {
    verb: current.scan_running ? '扫描中' : '运行中',
    detail: [scanPart, reasonPart, phasePart].filter(Boolean).join(' · '),
    tone: 'active',
    nextAction: summary.pendingConfirm > 0 ? 'confirm' : undefined,
  }
}

const TONE_COLOR: Record<Tone, string> = {
  idle: 'var(--text-400, #888780)',
  active: 'var(--brand-500, #31B3AE)',
  warning: 'var(--state-warning-dark, #ff9f0a)',
  success: 'var(--state-success, #1D9E75)',
  error: 'var(--state-error, #ff453a)',
}

function Chip({ children, tone, title, onClick }: {
  children: React.ReactNode
  tone?: 'default' | 'warning' | 'success' | 'brand'
  title?: string
  onClick?: () => void
}) {
  const palette: Record<string, { bg: string; fg: string }> = {
    default: { bg: 'var(--background-700)', fg: 'var(--text-300)' },
    warning: { bg: 'var(--state-warning-surface)', fg: 'var(--state-warning)' },
    success: { bg: 'var(--state-success-surface)', fg: 'var(--state-success)' },
    brand: { bg: 'rgba(49,179,174,0.12)', fg: 'var(--brand-700)' },
  }
  const c = palette[tone ?? 'default']
  return (
    <span
      title={title}
      onClick={onClick}
      style={{
        background: c.bg, color: c.fg, borderRadius: 'var(--radius-md)',
        padding: '2px 8px', fontSize: 12, whiteSpace: 'nowrap',
        cursor: onClick ? 'pointer' : undefined,
      }}
    >
      {children}
    </span>
  )
}

export function AnalysisProgress({ status, compact = false, exportSummary, onGoToClips }: {
  status: ContinuousAnalysisStatus | null
  compact?: boolean
  exportSummary?: ExportSummary
  /** 点击「待调 / 去确认」时跳转切片列表 */
  onGoToClips?: () => void
}) {
  const [, setTick] = useState(0)
  useEffect(() => {
    if (!status?.running) return
    const timer = setInterval(() => setTick((t) => t + 1), 1000)
    return () => clearInterval(timer)
  }, [status?.running])

  if (!status) return null
  const current = status
  const summary = exportSummary ?? {
    pendingConfirm: 0, queued: 0, exporting: 0, completed: 0, failed: 0, listed: 0,
  }
  const hasContent = Boolean(current.running || current.phase === 'completed' || current.phase === 'finalizing')
  if (!hasContent) {
    return compact
      ? <Typography.Text type="secondary">持续分析未运行</Typography.Text>
      : <Card size="small" style={{ minWidth: 320 }}><Typography.Text type="secondary">持续分析未运行</Typography.Text></Card>
  }

  const ps = derivePrimaryStatus(current, summary)
  const isFinalizing = current.phase === 'finalizing'
  const isWorkerActive = isFinalizing || !!current.scan_running
  const analyzed = current.analyzed_duration ?? 0
  const recorded = current.recorded_duration ?? 0
  const lagSec = current.analysis_lag_sec ?? Math.max(0, recorded - analyzed)
  const scanEnd = current.scan_range?.[1] ?? analyzed
  const hasFixedScanRange = Boolean(scanEnd > 0 && isWorkerActive)
  const rawPercent = scanEnd > 0 ? Math.min(100, Math.max(0, (analyzed / scanEnd) * 100)) : 0
  const livePercent = isWorkerActive ? Math.min(rawPercent, 95) : rawPercent
  const listed = summary.listed || (current.total_highlights ?? 0)
  const pendingN = summary.pendingConfirm > 0 ? summary.pendingConfirm : (current.pending_rounds ?? 0)
  const exportActive = summary.queued > 0 || summary.exporting > 0 || summary.failed > 0
  const roomLabel = current.room_id
    ? (current.room_id.length > 10 ? `${current.room_id.slice(0, 8)}…` : current.room_id)
    : null
  const modeLabel = current.mode === 'valorant_round' ? '回合' : '场景'
  const dotAnimated = ps.tone === 'active'
  const dot = (
    <span style={{
      width: 8, height: 8, borderRadius: '50%', background: TONE_COLOR[ps.tone], flexShrink: 0,
      animation: dotAnimated ? 'caPulse 1.8s ease-in-out infinite' : 'none',
    }} />
  )

  const actionChips = (
    <>
      <Chip title="已入列的回合切片（含各目标房间）">入列 {listed}</Chip>
      {pendingN > 0 && (
        <Chip tone="warning" title="有待确认的切片，点击前往切片列表" onClick={onGoToClips}>
          待调 {pendingN}
        </Chip>
      )}
      {(current.confirmed_rounds ?? 0) > 0 && (
        <Chip tone="success" title="边界可信、可确认导出（不是「已全部导出」）">可导 {current.confirmed_rounds}</Chip>
      )}
      {current.pending_round && !isFinalizing && <Chip tone="warning">等待回合结束</Chip>}
    </>
  )

  const exportChip = exportActive && (
    <Chip
      title={`导出队列：排队 ${summary.queued} · 导出中 ${summary.exporting} · 已完成 ${summary.completed} · 失败 ${summary.failed}`}
      tone={summary.failed > 0 ? 'warning' : 'default'}
    >
      导出 {summary.exporting > 0 ? `${summary.exporting}中` : ''}{summary.queued > 0 ? ` · 排队 ${summary.queued}` : ''}
    </Chip>
  )

  if (compact) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 14, fontVariantNumeric: 'tabular-nums', fontSize: 13, minWidth: 0, flex: 1 }}>
        <style>{`@keyframes caPulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:0.5;transform:scale(0.8)}}`}</style>

        {/* 主状态区：动词 + 一句人话 */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
          {dot}
          <span style={{ fontWeight: 600, color: 'var(--text-50)', whiteSpace: 'nowrap' }}>{ps.verb}</span>
          {roomLabel && <Chip title={current.room_id || undefined}>主房 {roomLabel}</Chip>}
          <Chip>{modeLabel}</Chip>
          {ps.detail && (
            <span style={{
              fontSize: 12, color: 'var(--text-400)', overflow: 'hidden',
              textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 360,
            }} title={ps.detail}>
              {ps.detail}
            </span>
          )}
        </div>

        {/* 进度区：条 + 已分析/已录/滞后 */}
        {current.phase !== 'completed' && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flex: '0 1 280px', minWidth: 140 }}>
            <div style={{ flex: 1, maxWidth: 240, height: 5, background: 'var(--background-700)', borderRadius: 3, overflow: 'hidden' }}>
              <div style={{ width: `${hasFixedScanRange ? livePercent : 0}%`, height: '100%', background: 'var(--brand-600)', borderRadius: 3, transition: 'width 0.5s ease' }} />
            </div>
            <span style={{ fontSize: 12, color: 'var(--text-400)', whiteSpace: 'nowrap' }}>
              {formatDuration(analyzed)} / {formatDuration(recorded || scanEnd)}
              {lagSec > 1 && current.running ? ` · 滞后 ${formatDuration(lagSec)}` : ''}
            </span>
          </div>
        )}

        {/* 行动区 */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
          {actionChips}
          {exportChip}
          {current.analysis_stage === '等待收尾' && (
            <Typography.Text type="secondary" style={{ fontSize: 12, whiteSpace: 'nowrap' }}>
              请先停录
            </Typography.Text>
          )}
        </div>
      </div>
    )
  }

  return (
    <Card size="small" style={{ minWidth: 320 }}>
      <style>{`@keyframes caPulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:0.5;transform:scale(0.8)}}`}</style>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          {dot}
          <Typography.Text strong style={{ fontSize: 15 }}>{ps.verb}</Typography.Text>
          {roomLabel && <Chip title={current.room_id || undefined}>主房 {roomLabel}</Chip>}
          <Chip>{modeLabel}</Chip>
        </div>
        {ps.detail && <Typography.Text type="secondary">{ps.detail}</Typography.Text>}

        {current.phase !== 'completed' && (
          <>
            <span className="pbar-line" style={{ height: 6, borderRadius: 3 }}>
              <span
                className="pbar-fill"
                style={{ width: `${Math.round(isWorkerActive ? Math.min(livePercent, 95) : livePercent)}%` }}
              />
            </span>
            <Typography.Text type="secondary">
              已分析 {formatDuration(analyzed)} / 已录 {formatDuration(recorded)}
              {lagSec > 1 && current.running ? ` · 滞后 ${formatDuration(lagSec)}` : ''}
              {isWorkerActive && livePercent >= 95 ? '（后台精修中，完成后自动更新）' : ''}
            </Typography.Text>
          </>
        )}

        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
          {actionChips}
          {exportChip}
        </div>

        {(isFinalizing || current.analysis_stage === '等待收尾') && (
          <Alert
            type="info"
            showIcon
            message={isFinalizing ? '正在进行最终回合确认（首次约 1–2 分钟）' : '请先结束录制，并等待收尾完成'}
            description="停录后会做一次收尾扫描，把尾部回合补入列表（待确认）。收尾完成后回合仍需你确认/导出。"
          />
        )}
        {current.running && !isFinalizing && current.phase === 'running' && (
          <Typography.Text type="secondary">
            提示：结束时请先停录，再等分析收尾；回合入列后需确认再导出。
          </Typography.Text>
        )}
        {current.phase === 'completed' && (
          <Alert
            type={summary.pendingConfirm > 0 ? 'warning' : 'success'}
            showIcon
            message={
              summary.pendingConfirm > 0
                ? `分析收尾已完成，还有 ${summary.pendingConfirm} 条待确认后再导出`
                : `分析完成，共入列 ${listed} 个回合`
            }
          />
        )}
      </div>
    </Card>
  )
}
