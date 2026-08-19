import { useEffect, useState } from 'react'
import { Alert, Card, Typography } from 'antd'
import { ContinuousAnalysisStatus } from '@/types'
import { calculateConfirmedAnalysisPercent } from '@/utils/analysisProgress'
import { useI18n, type I18nT } from '@/i18n'

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
 * 注意：`stage === 'xxx'` 等比较分支中的中文字符串是后端协议值，禁止翻译。
 */
function derivePrimaryStatus(current: ContinuousAnalysisStatus, summary: ExportSummary, t: I18nT): PrimaryStatus {
  const stage = current.analysis_stage ?? ''
  const listed = summary.listed || (current.total_highlights ?? 0)
  const cpuFallback = current.provider === 'CPUExecutionProvider'
    || current.provider_warning?.includes('CPUExecutionProvider')

  if (current.phase === 'stalled' || current.stalled) {
    return {
      verb: t('未检测到对局'),
      detail: current.round_phase_detail || t('连续重锚无 buy 信号，已暂停扫描'),
      tone: 'warning',
    }
  }
  if (current.phase === 'error' || current.error) {
    return { verb: t('持续分析异常'), detail: current.error ?? t('请重试或查看日志'), tone: 'error' }
  }
  if (current.phase === 'completed') {
    if (summary.pendingConfirm > 0) {
      return { verb: t('分析完成'), detail: t('入列 {listed} 回合 · {pending} 条待确认', { listed, pending: summary.pendingConfirm }), tone: 'warning', nextAction: 'confirm' }
    }
    return { verb: t('已完成'), detail: t('入列 {listed} 回合', { listed }), tone: 'success' }
  }
  if (current.phase === 'stopping' || stage === '停止中') {
    return { verb: t('停止中…'), detail: t('等待扫描退出并释放任务槽'), tone: 'idle' }
  }
  if (stage === '异常退出' || stage === '视觉模型不可用') {
    return { verb: t('分析异常退出'), detail: t('请重新启动持续分析，或查看日志排查原因'), tone: 'error' }
  }
  if (stage === '收尾失败') {
    return { verb: t('收尾失败'), detail: t('可重新启动持续分析'), tone: 'error' }
  }
  if (current.degraded_mode === 'audio_only' || stage === '降级追赶') {
    return {
      verb: t('降级追赶中'),
      detail: t('上一轮扫描超时，已切换短窗音频分析并继续推进'),
      tone: 'warning',
    }
  }
  if (current.last_scan_error) {
    return {
      verb: t('扫描恢复中'),
      detail: t('上一轮超时 · 已自动缩小分析窗口'),
      tone: 'warning',
    }
  }
  if (cpuFallback) {
    return {
      verb: t('CPU 慢速分析'),
      detail: t('GPU 推理不可用，分析可能逐渐落后录制'),
      tone: 'warning',
    }
  }
  if (stage === '等待收尾') {
    return { verb: t('等待收尾'), detail: t('请先停录后再完成收尾扫描'), tone: 'idle', nextAction: undefined }
  }
  if (current.phase === 'finalizing' || stage === '收尾中') {
    const elapsed = Math.floor(current.scan_elapsed_sec ?? 0)
    return {
      verb: t('收尾中'),
      detail: elapsed > 0
        ? t('最终回合确认 · 已运行 {elapsed}s（首次约 1–2 分钟）', { elapsed })
        : t('停录后做一次收尾扫描，尾部回合补入列表'),
      tone: 'active',
    }
  }
  if (stage === '等待新录制') {
    return { verb: t('等待录制'), detail: t('开始录制后自动跟进分析'), tone: 'idle' }
  }
  if (stage === '等待可分析片段' || stage === '等待新片段') {
    return { verb: t('等待片段'), detail: t('录制写入中，凑够窗口即扫描'), tone: 'idle' }
  }
  const detectedPart = Number.isFinite(current.last_detected_in_sec)
    && Number.isFinite(current.last_detected_out_sec)
    ? t('最近切片：入 {in} · 出 {out}', {
      in: formatDuration(current.last_detected_in_sec!),
      out: formatDuration(current.last_detected_out_sec!),
    })
    : ''
  const reasonPart = current.scan_reason === 'audio_increment'
    ? t('音频推进')
    : current.scan_reason === 'finalize'
      ? t('收尾')
      : ''
  const phasePart = current.mode === 'valorant_round'
    ? (current.round_phase_detail || t(ROUND_PHASE_LABEL[current.round_phase || ''] || ''))
    : ''
  // 音频待复核回合数（P3: 回合边界精度指示）
  const audioPendingPart = (current.audio_pending_rounds ?? 0) > 0
    ? t('{count} 个待 OCR 复核', { count: current.audio_pending_rounds ?? 0 })
    : ''
  return {
    verb: current.scan_running ? t('扫描中') : t('运行中'),
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
  const { t } = useI18n()
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
      ? <Typography.Text type="secondary">{t('持续分析未运行')}</Typography.Text>
      : <Card size="small" style={{ minWidth: 320 }}><Typography.Text type="secondary">{t('持续分析未运行')}</Typography.Text></Card>
  }

  const ps = derivePrimaryStatus(current, summary, t)
  const isFinalizing = current.phase === 'finalizing'
  if (current.phase === 'stopping') {
    ps.verb = t('停止中…')
    ps.detail = t('正在停止并等待扫描退出 · 已等待 {elapsed}s（通常 1–2 分钟）', { elapsed: stoppingElapsed })
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
  const modeLabel = current.mode === 'valorant_round' ? t('回合') : t('场景')
  const dotAnimated = ps.tone === 'active'
  const dot = (
    <span style={{
      width: 8, height: 8, borderRadius: '50%', background: TONE_COLOR[ps.tone], flexShrink: 0,
      animation: dotAnimated ? 'caPulse 1.8s ease-in-out infinite' : 'none',
    }} />
  )

  const actionChips = (
    <>
      <Chip title={t('已入列的回合切片（含各目标房间）')}>{t('入列 {count}', { count: listed })}</Chip>
      {pendingN > 0 && (
        <Chip tone="warning" title={t('有待确认的切片，点击前往切片列表')} onClick={onGoToClips}>
          {t('待调 {count}', { count: pendingN })}
        </Chip>
      )}
      {(current.confirmed_rounds ?? 0) > 0 && (
        <Chip tone="success" title={t('边界可信、可确认导出（不是「已全部导出」）')}>{t('可导 {count}', { count: current.confirmed_rounds ?? 0 })}</Chip>
      )}
      {current.pending_round && !isFinalizing && (
        <Chip tone="warning" title={current.pending_round_info?.waiting_for ? t('等待 {what}', { what: current.pending_round_info.waiting_for }) : undefined}>
          {current.pending_round_info?.phase
            ? t('等待{phase}', { phase: t(ROUND_PHASE_LABEL[current.pending_round_info.phase] || current.pending_round_info.phase) })
            : t('等待回合结束')}
          {current.pending_round_info?.since_sec ? ` ${Math.floor(current.pending_round_info.since_sec)}s` : ''}
        </Chip>
      )}
      {current.degraded_mode === 'audio_only' && (
        <Chip tone="warning" title={t('视觉扫描超时后自动切换为短窗音频追赶')}>{t('音频追赶')}</Chip>
      )}
      {current.provider === 'CPUExecutionProvider' && (
        <Chip tone="warning" title={current.provider_warning || t('GPU 推理不可用')}>{t('CPU 模式')}</Chip>
      )}
      {(current.audio_pending_rounds ?? 0) > 0 && (
        <Chip tone="default" title={t('音频路径检测到，待 OCR 复核边界')}>{t('音频待复核 {count}', { count: current.audio_pending_rounds ?? 0 })}</Chip>
      )}
      {current.mapping_error && (
        <Chip tone="warning" title={current.mapping_error}>{t('同步异常')}</Chip>
      )}
    </>
  )

  const exportChip = exportActive && (
    <Chip
      title={t('导出队列：排队 {queued} · 导出中 {exporting} · 已完成 {completed} · 失败 {failed}', {
        queued: summary.queued,
        exporting: summary.exporting,
        completed: summary.completed,
        failed: summary.failed,
      })}
      tone={summary.failed > 0 ? 'warning' : 'default'}
    >
      {summary.exporting > 0
        ? t('导出中 {count}', { count: summary.exporting })
        : summary.queued > 0
          ? t('排队 {count}', { count: summary.queued })
          : t('导出')}
    </Chip>
  )

  // compact 模式只保留核心：状态 + 进度 + 需行动的 Chip
  if (compact) {
    // hover 时展示完整信息
    const fullTitle = [
      ps.detail,
      roomLabel ? t('主房 {room}', { room: roomLabel }) : '',
      t('模式 {mode}', { mode: modeLabel }),
      listed > 0 ? t('入列 {count}', { count: listed }) : '',
      (current.confirmed_rounds ?? 0) > 0 ? t('可导 {count}', { count: current.confirmed_rounds ?? 0 }) : '',
      current.degraded_mode === 'audio_only' ? t('音频追赶') : '',
      current.provider === 'CPUExecutionProvider' ? t('CPU 模式') : '',
      (current.audio_pending_rounds ?? 0) > 0 ? t('音频待复核 {count}', { count: current.audio_pending_rounds ?? 0 }) : '',
      current.mapping_error ? `${t('同步异常')}: ${current.mapping_error}` : '',
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
            title={t('直播实时跟进中，录制的终点持续向后移动；完成后补扫尾部，进度不会显示 100%')}>
            <div style={{ flex: 1, maxWidth: 180, height: 4, background: 'var(--background-700)', borderRadius: 'var(--radius-xs, 6px)', overflow: 'hidden' }}>
              <div style={{ width: `${confirmedPercent}%`, height: '100%', background: TONE_COLOR[ps.tone === 'idle' ? 'active' : ps.tone], borderRadius: 'var(--radius-xs, 6px)', transition: 'width 0.5s ease' }} />
            </div>
            <span style={{ fontSize: 11, color: 'var(--text-400)', whiteSpace: 'nowrap' }}>
              {!hasFixedScanRange && `${t('实时跟进')} `}
              {formatDuration(analyzed)}/{formatDuration(recorded)}
              {lagSec > 5 && current.running ? ` · ${t('滞后{lag}', { lag: formatDuration(lagSec) })}` : ''}
            </span>
          </div>
        )}

        {/* ③ 行动 Chip：仅显示需要用户操作的 */}
        {pendingN > 0 && (
          <Chip tone="warning" title={t('有待确认的切片，点击前往切片列表')} onClick={onGoToClips}>
            {t('待调 {count}', { count: pendingN })}
          </Chip>
        )}
        {summary.failed > 0 && (
          <Chip tone="warning" title={t('导出失败 {count} 个', { count: summary.failed })}>
            {t('失败 {count}', { count: summary.failed })}
          </Chip>
        )}
        {ps.nextAction === 'confirm' && pendingN === 0 && (
          <Chip tone="warning" title={t('点击前往切片列表确认')} onClick={onGoToClips}>
            {t('去确认')}
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
          {roomLabel && <Chip title={current.room_id || undefined}>{t('主房 {room}', { room: roomLabel })}</Chip>}
          <Chip>{modeLabel}</Chip>
        </div>
        {ps.detail && <Typography.Text type="secondary">{ps.detail}</Typography.Text>}

        {current.phase !== 'completed' && (
          <>
            <div style={{ height: 5, borderRadius: 'var(--radius-xs, 6px)', background: 'var(--background-700)', overflow: 'hidden' }}>
              <div style={{ width: `${confirmedPercent}%`, height: '100%', background: TONE_COLOR[ps.tone === 'idle' ? 'active' : ps.tone], borderRadius: 'var(--radius-xs, 6px)', transition: 'width 0.5s ease' }} />
            </div>
            <Typography.Text type="secondary">
              {!hasFixedScanRange ? `${t('实时跟进')} · ` : ''}
              {t('后台已确认分析 {analyzed} / 已录 {recorded}', { analyzed: formatDuration(analyzed), recorded: formatDuration(recorded) })}
              {current.scan_running ? ` · ${t('本轮扫描已用 {sec}', { sec: formatDuration(current.scan_elapsed_sec ?? 0) })}` : ''}
              {lagSec > 1 && current.running ? ` · ${t('滞后 {lag}', { lag: formatDuration(lagSec) })}` : ''}
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
            message={isFinalizing ? t('正在进行最终回合确认（首次约 1–2 分钟）') : t('请先结束录制，并等待收尾完成')}
            description={t('停录后会做一次收尾扫描，把尾部回合补入列表（待确认）。收尾完成后回合仍需你确认/导出。')}
          />
        )}
        {(current.provider_warning || current.last_scan_error) && current.phase === 'running' && (
          <Alert
            type="warning"
            showIcon
            message={
              current.degraded_mode === 'audio_only'
                ? t('视觉扫描超时，已自动切换短窗音频追赶')
                : current.provider === 'CPUExecutionProvider'
                  ? t('GPU 推理不可用，当前使用 CPU 慢速分析')
                  : t('上一轮扫描超时，正在自动恢复')
            }
            description={
              current.provider === 'CPUExecutionProvider'
                ? t('分析仍会继续，但可能落后录制；新版安装器会自动安装并校验 DirectML。')
                : t('已连续超时 {count} 次，程序会缩小窗口后继续推进。', { count: current.consecutive_scan_timeouts ?? 1 })
            }
          />
        )}
        {current.running && !isFinalizing && current.phase === 'running' && (
          <Typography.Text type="secondary">
            {t('提示：结束时请先停录，再等分析收尾；回合入列后需确认再导出。')}
          </Typography.Text>
        )}
        {current.phase === 'completed' && (
          <Alert
            type={summary.pendingConfirm > 0 ? 'warning' : 'success'}
            showIcon
            message={
              summary.pendingConfirm > 0
                ? t('分析收尾已完成，还有 {count} 条待确认后再导出', { count: summary.pendingConfirm })
                : t('分析完成，共入列 {count} 个回合', { count: listed })
            }
          />
        )}
      </div>
    </Card>
  )
}
