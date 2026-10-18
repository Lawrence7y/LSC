import { useEffect, useState } from 'react'
import { Alert, Card, Typography, Popover } from 'antd'
import { ContinuousAnalysisStatus } from '@/types'
import {
  confirmedAnalysisPercent,
  derivePrimaryStatus,
  formatDuration,
  formatFps,
  formatThroughput,
  hasFixedScanRange,
  inFlightScanWindow,
  ROUND_PHASE_LABEL,
  type Tone,
} from '@/utils/analysisProgress'
import type { ClipExportSummary as ExportSummary } from '@/utils/clipExportPolicy'
import { useI18n } from '@/i18n'
import { HexParticleProgress } from '@/components/HexParticleProgress'


const TONE_COLOR: Record<Tone, string> = {
  idle: 'var(--text-tertiary)',
  active: 'var(--brand-500)',
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

export function AnalysisProgress({ status, compact = false, exportSummary, onGoToClips: _onGoToClips, onResumeFinalization }: {
  status: ContinuousAnalysisStatus | null
  compact?: boolean
  exportSummary?: ExportSummary
  onGoToClips?: () => void
  onResumeFinalization?: (roomId: string) => void
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
    || current.phase === 'checkpoint_saved'
    // 错误/停止态也必须展示：错误只在 toast 闪现无法排查，
    // 停止中与按钮 loading 同屏矛盾（详见 derivePrimaryStatus 分支）
    || current.phase === 'error'
    || current.phase === 'stopping'
  )
  if (!hasContent) {
    return compact ? (
      <div style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '3px 10px', borderRadius: 8, background: 'rgba(255,255,255,0.02)', border: '1px dashed rgba(255,255,255,0.08)' }}>
        <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--text-tertiary)' }} />
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>{t('持续分析未运行')}</Typography.Text>
      </div>
    ) : (
      <Card size="small" style={{ minWidth: 320 }}><Typography.Text type="secondary">{t('持续分析未运行')}</Typography.Text></Card>
    )
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
  const scanWindow = inFlightScanWindow(current)
  const fixedRange = hasFixedScanRange(current)
  // 直播录制的终点持续向后移动；即便暂时追平，也不能用 100% 暗示任务完成。
  const confirmedPercent = confirmedAnalysisPercent(analyzed, recorded, fixedRange)
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
      {(current.pending_review_count ?? 0) > 0 && (
        <Chip tone="warning" title={t('证据不足或审计未完成，不会自动导出')}>{t('待复核 {count}', { count: current.pending_review_count ?? 0 })}</Chip>
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
      current.model_infer_fps ? t('视觉 {fps}', { fps: formatFps(current.model_infer_fps) }) : '',
      current.net_coverage_throughput != null ? t('覆盖 {speed}', { speed: formatThroughput(current.net_coverage_throughput) }) : '',
      (current.pending_review_count ?? 0) > 0 ? t('待复核 {count}', { count: current.pending_review_count ?? 0 }) : '',
      (current.audio_pending_rounds ?? 0) > 0 ? t('音频待复核 {count}', { count: current.audio_pending_rounds ?? 0 }) : '',
      current.mapping_error ? `${t('同步异常')}: ${current.mapping_error}` : '',
    ].filter(Boolean).join(' · ')

    const detailsContent = (
      <div style={{ width: 280, fontSize: 12 }}>
        <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-tertiary)', textTransform: 'uppercase', marginBottom: 8, paddingBottom: 4, borderBottom: '1px solid var(--border-default)' }}>
          {t('底层扫描技术指标')}
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {roomLabel && (
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span style={{ color: 'var(--text-tertiary)' }}>{t('主房')}:</span>
              <span style={{ color: 'var(--text-primary)', fontFamily: 'var(--font-mono)' }}>{roomLabel}</span>
            </div>
          )}
          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
            <span style={{ color: 'var(--text-tertiary)' }}>{t('滑动窗口')}:</span>
            <span style={{ color: 'var(--text-primary)', fontFamily: 'var(--font-mono)' }}>
              {scanWindow ? `${formatDuration(scanWindow.from)} – ${formatDuration(scanWindow.to)}` : '–'}
            </span>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
            <span style={{ color: 'var(--text-tertiary)' }}>{t('推理设备')}:</span>
            <span style={{ color: current.provider === 'CPUExecutionProvider' ? 'var(--state-warning)' : 'var(--state-success)', fontFamily: 'var(--font-mono)' }}>
              {current.provider || 'DirectML'}
            </span>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
            <span style={{ color: 'var(--text-tertiary)' }}>{t('对齐滞后')}:</span>
            <span style={{ color: lagSec > 5 ? 'var(--state-warning)' : 'var(--text-primary)', fontFamily: 'var(--font-mono)' }}>
              {lagSec > 1 ? formatDuration(lagSec) : t('正常 (实时跟进)')}
            </span>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
            <span style={{ color: 'var(--text-tertiary)' }}>{t('实际覆盖速度')}:</span>
            <span style={{ color: (current.net_coverage_throughput ?? 0) < 1 ? 'var(--state-warning)' : 'var(--state-success)', fontFamily: 'var(--font-mono)' }}>
              {formatThroughput(current.net_coverage_throughput)}
            </span>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
            <span style={{ color: 'var(--text-tertiary)' }}>{t('采样 / 视觉推理')}:</span>
            <span style={{ color: 'var(--text-primary)', fontFamily: 'var(--font-mono)' }}>
              {formatFps(current.sample_fps)} / {formatFps(current.model_infer_fps)}
            </span>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
            <span style={{ color: 'var(--text-tertiary)' }}>{t('视觉延迟 P90')}:</span>
            <span style={{ color: 'var(--text-primary)', fontFamily: 'var(--font-mono)' }}>
              {Number.isFinite(current.model_infer_ms_p90) && (current.model_infer_ms_p90 ?? 0) > 0
                ? `${Number(current.model_infer_ms_p90).toFixed(1)} ms`
                : '–'}
            </span>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
            <span style={{ color: 'var(--text-tertiary)' }}>{t('质量状态')}:</span>
            <span style={{ color: current.analysis_quality_status === 'error' ? 'var(--state-error)' : current.analysis_quality_status === 'review' ? 'var(--state-warning)' : 'var(--state-success)' }}>
              {current.analysis_quality_status === 'review'
                ? t('待复核')
                : current.analysis_quality_status === 'degraded'
                  ? t('降级')
                  : current.analysis_quality_status === 'error'
                    ? t('异常')
                    : t('正常')}
            </span>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
            <span style={{ color: 'var(--text-tertiary)' }}>{t('粗扫吞吐')}:</span>
            <span style={{ color: 'var(--text-primary)', fontFamily: 'var(--font-mono)' }}>
              {formatThroughput(current.gross_scan_throughput)}
            </span>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
            <span style={{ color: 'var(--text-tertiary)' }}>{t('净覆盖速度')}:</span>
            <span style={{ color: (current.net_coverage_throughput ?? 0) < 1 ? 'var(--state-warning)' : 'var(--state-success)', fontFamily: 'var(--font-mono)' }}>
              {formatThroughput(current.net_coverage_throughput)}
            </span>
          </div>
          {Boolean(current.round_phase || current.round_phase_detail) && (
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span style={{ color: 'var(--text-tertiary)' }}>{t('对局阶段')}:</span>
              <span style={{ color: 'var(--brand-500)' }}>
                {current.round_phase_detail || t(ROUND_PHASE_LABEL[current.round_phase || ''] || '')}
              </span>
            </div>
          )}
          {(current.audio_pending_rounds ?? 0) > 0 && (
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span style={{ color: 'var(--text-tertiary)' }}>{t('音频待复核')}:</span>
              <span style={{ color: 'var(--state-warning)' }}>{current.audio_pending_rounds} {t('个')}</span>
            </div>
          )}
        </div>
      </div>
    )

    return (
      <div
        title={fullTitle}
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 12,
          fontVariantNumeric: 'tabular-nums',
          fontSize: 12,
          minWidth: 0,
          width: 'fit-content',
          background: 'var(--bg-secondary)',
          border: '1px solid var(--border-default)',
          borderRadius: 'var(--radius-xs, 6px)',
          padding: '4px 12px',
          boxShadow: 'var(--shadow-sm)',
          flexWrap: 'nowrap',
        }}
      >
        <style>{`
          @keyframes caPulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:0.4;transform:scale(0.85)}}
        `}</style>

        {/* ① 状态与核心战果 (Status & Highlights) */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
          {/* 微型状态指示点 + 阶段文本 */}
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 5,
              padding: '2px 8px',
              borderRadius: 4,
              background: 'var(--bg-tertiary)',
              border: '1px solid var(--border-default)',
              fontSize: 11,
              fontWeight: 600,
              color: 'var(--text-primary)',
            }}
          >
            <span
              style={{
                width: 6,
                height: 6,
                borderRadius: '50%',
                background: ps.tone === 'error' ? 'var(--state-error)' : ps.tone === 'warning' ? 'var(--state-warning)' : 'var(--brand-500)',
                flexShrink: 0,
                animation: dotAnimated ? 'caPulse 1.8s ease-in-out infinite' : 'none',
              }}
            />
            <span>{ps.verb}</span>
          </div>

          {/* 核心战果回合统计 */}
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 4 }}>
            <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)' }}>
              {listed}
            </span>
            <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>
              {t('回合入列')}
            </span>
            {(current.confirmed_rounds ?? 0) > 0 && (
              <span style={{ fontSize: 11, color: 'var(--brand-500)', fontWeight: 600, marginLeft: 2 }}>
                （{t('可导 {count}', { count: current.confirmed_rounds ?? 0 })}）
              </span>
            )}
          </div>
        </div>

        {/* 细分割线 */}
        <div style={{ width: 1, height: 14, background: 'var(--border-default)', flexShrink: 0 }} />

        {/* ② 实时时序与跟进窗口 (Timeline Duration) */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0, fontSize: 11 }}>
          <span style={{ color: 'var(--text-tertiary)' }}>
            {!fixedRange ? t('实时跟进') : t('扫描')}
          </span>
          <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 500, color: 'var(--text-secondary)' }}>
            {formatDuration(analyzed)} / {formatDuration(recorded)}
          </span>
        </div>

        {/* 细分割线 */}
        <div style={{ width: 1, height: 14, background: 'var(--border-default)', flexShrink: 0 }} />

        {/* ③ 紧凑 Hex 蜂窝粒子能量指示槽 (纯粒子能量槽，宽度 150px，高度 18px，数据完全外置) */}
        {current.phase !== 'completed' && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
            <div style={{ width: 150 }}>
              <HexParticleProgress
                percent={confirmedPercent}
                height={18}
                rows={3}
                dotRadius={1.8}
                tone={ps.tone === 'error' ? 'error' : ps.tone === 'warning' ? 'warning' : 'brand'}
                title={t('直播实时跟进中，录制的终点持续向后移动；完成后补扫尾部，进度不会显示 100%')}
              />
            </div>
            <span
              style={{
                fontFamily: 'var(--font-mono)',
                fontWeight: 700,
                fontSize: 11,
                color: 'var(--brand-500)',
                minWidth: 28,
              }}
            >
              {Math.round(confirmedPercent)}%
            </span>
          </div>
        )}

        {/* 细分割线（有行动项时显示） */}
        {(pendingN > 0 || summary.failed > 0) && (
          <div style={{ width: 1, height: 14, background: 'var(--border-default)', flexShrink: 0 }} />
        )}

        {/* ④ 行动按钮与收拢详情入口 */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
          {summary.failed > 0 && (
            <span
              style={{
                padding: '2px 7px',
                borderRadius: 4,
                background: 'var(--bg-tertiary)',
                border: '1px solid var(--border-default)',
                color: 'var(--state-error)',
                fontSize: 11,
                fontWeight: 600,
              }}
            >
              {t('失败 {count}', { count: summary.failed })}
            </span>
          )}

          {exportChip}

          {/* 收尾失败但可恢复：提供一键从断点恢复补扫尾部回合（resume_continuous_finalization） */}
          {(current.phase === 'error' || current.phase === 'checkpoint_saved')
            && (current.finalization_recoverable || current.phase === 'checkpoint_saved')
            && current.room_id && onResumeFinalization && (
            <Chip
              tone="brand"
              title={t('收尾超时，点击从断点恢复补扫尾部回合')}
              onClick={() => onResumeFinalization(current.room_id!)}
            >
              {t('恢复收尾')}
            </Chip>
          )}

          {/* 细分割线 */}
          <div style={{ width: 1, height: 14, background: 'var(--border-default)', flexShrink: 0, margin: '0 2px' }} />

          {/* 详情收拢浮层 Popover */}
          <Popover content={detailsContent} title={null} placement="bottomRight" trigger="hover">
            <span
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 4,
                padding: '2px 7px',
                borderRadius: 4,
                background: 'var(--bg-tertiary)',
                border: '1px solid var(--border-default)',
                color: 'var(--text-tertiary)',
                fontSize: 11,
                cursor: 'pointer',
                transition: 'all 0.2s',
                userSelect: 'none',
              }}
            >
              <span>ℹ</span>
              <span>{t('详情')}</span>
            </span>
          </Popover>
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
              {!fixedRange ? `${t('实时跟进')} · ` : ''}
              {t('后台已确认分析 {analyzed} / 已录 {recorded}', { analyzed: formatDuration(analyzed), recorded: formatDuration(recorded) })}
              {scanWindow ? ` · ${t('本窗 {from}–{to}', { from: formatDuration(scanWindow.from), to: formatDuration(scanWindow.to) })}` : ''}
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
