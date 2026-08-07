import { useEffect, useState } from 'react'
import { Alert, Card, Typography } from 'antd'
import { ContinuousAnalysisStatus } from '@/types'
import { calculateConfirmedAnalysisPercent } from '@/utils/analysisProgress'

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
  const cpuFallback = current.provider === 'CPUExecutionProvider'
    || current.provider_warning?.includes('CPUExecutionProvider')

  if (current.phase === 'stalled' || current.stalled) {
    return {
      verb: '未检测到对局',
      detail: current.round_phase_detail || '连续重锚无 buy 信号，已暂停扫描',
      tone: 'warning',
    }
  }
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
  if (stage === '异常退出' || stage === '视觉模型不可用') {
    return { verb: '分析异常退出', detail: '请重新启动持续分析，或查看日志排查原因', tone: 'error' }
  }
  if (stage === '收尾失败') {
    return { verb: '收尾失败', detail: '可重新启动持续分析', tone: 'error' }
  }
  if (current.degraded_mode === 'audio_only' || stage === '降级追赶') {
    return {
      verb: '降级追赶中',
      detail: '上一轮扫描超时，已切换短窗音频分析并继续推进',
      tone: 'warning',
    }
  }
  if (current.last_scan_error) {
    return {
      verb: '扫描恢复中',
      detail: `上一轮超时 · 已自动缩小分析窗口`,
      tone: 'warning',
    }
  }
  if (cpuFallback) {
    return {
      verb: 'CPU 慢速分析',
      detail: 'GPU 推理不可用，分析可能逐渐落后录制',
      tone: 'warning',
    }
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
  const detectedPart = Number.isFinite(current.last_detected_in_sec)
    && Number.isFinite(current.last_detected_out_sec)
    ? `最近切片：入 ${formatDuration(current.last_detected_in_sec!)} · 出 ${formatDuration(current.last_detected_out_sec!)}`
    : ''
  const reasonPart = current.scan_reason === 'audio_increment'
    ? '音频推进'
    : current.scan_reason === 'finalize'
      ? '收尾'
      : ''
  const phasePart = current.mode === 'valorant_round'
    ? (current.round_phase_detail || ROUND_PHASE_LABEL[current.round_phase || ''] || '')
    : ''
  // 音频待复核回合数（P3: 回合边界精度指示）
  const audioPendingPart = (current.audio_pending_rounds ?? 0) > 0
    ? `${current.audio_pending_rounds} 个待 OCR 复核`
    : ''
  return {
    verb: current.scan_running ? '扫描中' : '运行中',
    detail: [detectedPart, reasonPart, phasePart, audioPendingPart].filter(Boolean).join(' · '),
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
  // F4: 停止/收尾过程的本地计时（后端无 stopping 起始时间戳，前端自计时）
  // Hook 必须无条件声明：status 为 null / 无内容时提前 return 会造成 hook 数量跳变，
  // React 18 会抛 "Rendered more hooks than during the previous render" 崩溃。
  const [stoppingElapsed, setStoppingElapsed] = useState(0)
  useEffect(() => {
    if (status?.phase !== 'stopping' && status?.phase !== 'finalizing') return
    const startedAt = Date.now()
    setStoppingElapsed(0)
    const t = setInterval(() => setStoppingElapsed(Math.floor((Date.now() - startedAt) / 1000)), 1000)
    return () => clearInterval(t)
  }, [status?.phase])

  if (!status) return null
  const current = status
  const summary = exportSummary ?? {
    pendingConfirm: 0, queued: 0, exporting: 0, completed: 0, failed: 0, listed: 0,
  }
  const hasContent = Boolean(
    current.running
    || current.phase === 'completed'
    || current.phase === 'finalizing'
    // 错误/停止态也必须展示：错误只在 toast 闪现无法排查，
    // 停止中与按钮 loading 同屏矛盾（详见 derivePrimaryStatus 分支）
    || current.phase === 'error'
    || current.phase === 'stopping'
  )
  if (!hasContent) {
    return compact
      ? <Typography.Text type="secondary">持续分析未运行</Typography.Text>
      : <Card size="small" style={{ minWidth: 320 }}><Typography.Text type="secondary">持续分析未运行</Typography.Text></Card>
  }

  const ps = derivePrimaryStatus(current, summary)
  const isFinalizing = current.phase === 'finalizing'
  if (current.phase === 'stopping') {
    ps.verb = '停止中…'
    ps.detail = `正在停止并等待扫描退出 · 已等待 ${stoppingElapsed}s（通常 1–2 分钟）`
    ps.tone = 'idle'
  }
  const analyzed = current.analyzed_duration ?? 0
  const recorded = current.recorded_duration ?? 0
  const lagSec = current.analysis_lag_sec ?? Math.max(0, recorded - analyzed)
  const hasFixedScanRange = !current.running
    || current.phase === 'finalizing'
    || current.phase === 'completed'
  const livePercent = calculateConfirmedAnalysisPercent(analyzed, recorded)
  // 直播录制的终点持续向后移动；即便暂时追平，也不能用 100% 暗示任务完成。
  const confirmedPercent = hasFixedScanRange ? livePercent : Math.min(98, livePercent)
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
      {current.pending_round && !isFinalizing && (
        <Chip tone="warning" title={current.pending_round_info?.waiting_for ? `等待 ${current.pending_round_info.waiting_for}` : undefined}>
          {current.pending_round_info?.phase
            ? `等待${ROUND_PHASE_LABEL[current.pending_round_info.phase] || current.pending_round_info.phase}`
            : '等待回合结束'}
          {current.pending_round_info?.since_sec ? ` ${Math.floor(current.pending_round_info.since_sec)}s` : ''}
        </Chip>
      )}
      {current.degraded_mode === 'audio_only' && (
        <Chip tone="warning" title="视觉扫描超时后自动切换为短窗音频追赶">音频追赶</Chip>
      )}
      {current.provider === 'CPUExecutionProvider' && (
        <Chip tone="warning" title={current.provider_warning || 'GPU 推理不可用'}>CPU 模式</Chip>
      )}
      {(current.audio_pending_rounds ?? 0) > 0 && (
        <Chip tone="default" title="音频路径检测到，待 OCR 复核边界">音频待复核 {current.audio_pending_rounds}</Chip>
      )}
      {current.mapping_error && (
        <Chip tone="warning" title={current.mapping_error}>同步异常</Chip>
      )}
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

  // compact 模式只保留核心：状态 + 进度 + 需行动的 Chip
  if (compact) {
    // hover 时展示完整信息
    const fullTitle = [
      ps.detail,
      roomLabel ? `主房 ${roomLabel}` : '',
      `模式 ${modeLabel}`,
      listed > 0 ? `入列 ${listed}` : '',
      (current.confirmed_rounds ?? 0) > 0 ? `可导 ${current.confirmed_rounds}` : '',
      current.degraded_mode === 'audio_only' ? '音频追赶' : '',
      current.provider === 'CPUExecutionProvider' ? 'CPU 模式' : '',
      (current.audio_pending_rounds ?? 0) > 0 ? `音频待复核 ${current.audio_pending_rounds}` : '',
      current.mapping_error ? `同步异常: ${current.mapping_error}` : '',
    ].filter(Boolean).join(' · ')

    return (
      <div title={fullTitle} style={{ display: 'flex', alignItems: 'center', gap: 10, fontVariantNumeric: 'tabular-nums', fontSize: 13, minWidth: 0, flex: 1, flexWrap: 'wrap' }}>
        <style>{`@keyframes caPulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:0.5;transform:scale(0.8)}}`}</style>

        {/* ① 状态圆点 + 动词 */}
        {dot}
        <span style={{ fontWeight: 600, color: 'var(--text-50)', whiteSpace: 'nowrap', flexShrink: 0 }}>{ps.verb}</span>

        {/* ② 进度条 + 时间 */}
        {current.phase !== 'completed' && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, flex: '0 1 260px', minWidth: 100 }}
            title="直播实时跟进中，录制的终点持续向后移动；完成后补扫尾部，进度不会显示 100%">
            <div style={{ flex: 1, maxWidth: 180, height: 4, background: 'var(--background-700)', borderRadius: 'var(--radius-xs, 6px)', overflow: 'hidden' }}>
              <div style={{ width: `${confirmedPercent}%`, height: '100%', background: TONE_COLOR[ps.tone === 'idle' ? 'active' : ps.tone], borderRadius: 'var(--radius-xs, 6px)', transition: 'width 0.5s ease' }} />
            </div>
            <span style={{ fontSize: 11, color: 'var(--text-400)', whiteSpace: 'nowrap' }}>
              {!hasFixedScanRange && '实时跟进 '}
              {formatDuration(analyzed)}/{formatDuration(recorded)}
              {lagSec > 5 && current.running ? ` · 滞后${formatDuration(lagSec)}` : ''}
            </span>
          </div>
        )}

        {/* ③ 行动 Chip：仅显示需要用户操作的 */}
        {pendingN > 0 && (
          <Chip tone="warning" title="有待确认的切片，点击前往切片列表" onClick={onGoToClips}>
            待调 {pendingN}
          </Chip>
        )}
        {summary.failed > 0 && (
          <Chip tone="warning" title={`导出失败 ${summary.failed} 个`}>
            失败 {summary.failed}
          </Chip>
        )}
        {ps.nextAction === 'confirm' && pendingN === 0 && (
          <Chip tone="warning" title="点击前往切片列表确认" onClick={onGoToClips}>
            去确认
          </Chip>
        )}
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
            <div style={{ height: 5, borderRadius: 'var(--radius-xs, 6px)', background: 'var(--background-700)', overflow: 'hidden' }}>
              <div style={{ width: `${confirmedPercent}%`, height: '100%', background: TONE_COLOR[ps.tone === 'idle' ? 'active' : ps.tone], borderRadius: 'var(--radius-xs, 6px)', transition: 'width 0.5s ease' }} />
            </div>
            <Typography.Text type="secondary">
              {!hasFixedScanRange ? '实时跟进 · ' : ''}
              后台已确认分析 {formatDuration(analyzed)} / 已录 {formatDuration(recorded)}
              {current.scan_running ? ` · 本轮扫描已用 ${formatDuration(current.scan_elapsed_sec ?? 0)}` : ''}
              {lagSec > 1 && current.running ? ` · 滞后 ${formatDuration(lagSec)}` : ''}
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
        {(current.provider_warning || current.last_scan_error) && current.phase === 'running' && (
          <Alert
            type="warning"
            showIcon
            message={
              current.degraded_mode === 'audio_only'
                ? '视觉扫描超时，已自动切换短窗音频追赶'
                : current.provider === 'CPUExecutionProvider'
                  ? 'GPU 推理不可用，当前使用 CPU 慢速分析'
                  : '上一轮扫描超时，正在自动恢复'
            }
            description={
              current.provider === 'CPUExecutionProvider'
                ? '分析仍会继续，但可能落后录制；新版安装器会自动安装并校验 DirectML。'
                : `已连续超时 ${current.consecutive_scan_timeouts ?? 1} 次，程序会缩小窗口后继续推进。`
            }
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
