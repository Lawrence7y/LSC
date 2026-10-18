/**
 * 持续分析状态的纯推导层（从 `components/AnalysisProgress.tsx` 搬来，债务报告 §11.23）。
 *
 * 搬的理由：`derivePrimaryStatus` 是一条**有优先级**的判定链（stalled > 收尾可恢复 >
 * 检查点待恢复 > error > completed > …），文子守卫断言不了顺序，只能断言"文案还在"。
 * 这里把它做成可喂 JSON 状态的函数后，`analysisProgress.test.ts` 才能真的钉住
 * "同时 stalled 且 error 时用户看到哪一个"这类会误导用户的分支。
 */
import type { ContinuousAnalysisStatus } from '@/types'
import type { I18nT } from '@/i18n'

export function calculateConfirmedAnalysisPercent(
  analyzedDuration: number | null | undefined,
  recordedDuration: number | null | undefined,
): number {
  const analyzed = Number(analyzedDuration)
  const recorded = Number(recordedDuration)
  if (!Number.isFinite(analyzed) || !Number.isFinite(recorded) || recorded <= 0) {
    return 0
  }
  return Math.min(100, Math.max(0, (analyzed / recorded) * 100))
}

/** 本轮正在扫的区间。analyzed_duration 要等整窗结束才前进，扫描中必须单独展示，否则会一直显示 0s。 */
export function inFlightScanWindow(status: {
  scan_running?: boolean
  scan_in_sec?: number | null
  scan_out_sec?: number | null
}): { from: number; to: number } | null {
  if (!status.scan_running) return null
  const from = Number(status.scan_in_sec)
  const to = Number(status.scan_out_sec)
  if (!Number.isFinite(from) || !Number.isFinite(to) || to <= from) return null
  return { from, to }
}

export function formatDuration(seconds: number) {
  if (!Number.isFinite(seconds) || seconds <= 0) return '0s'
  const total = Math.floor(seconds)
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  return h > 0 ? `${h}h ${m}m ${s}s` : m > 0 ? `${m}m ${s}s` : `${s}s`
}

export function formatThroughput(value: number | undefined) {
  return Number.isFinite(value) ? `${Number(value).toFixed(2)}x` : '–'
}

export function formatFps(value: number | undefined) {
  return Number.isFinite(value) && Number(value) > 0 ? `${Number(value).toFixed(1)} FPS` : '–'
}

export const ROUND_PHASE_LABEL: Record<string, string> = {
  unknown: '寻找回合',
  buy: '买枪期',
  pre_combat: '等待开战',
  combat: '交战中',
  post_combat: '等待结束',
  intermission: '局间暂停',
}

export type Tone = 'idle' | 'active' | 'warning' | 'success' | 'error'

export interface PrimaryStatus {
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
export function derivePrimaryStatus(current: ContinuousAnalysisStatus, summary: AnalysisSummarySlice, t: I18nT): PrimaryStatus {
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
  if (current.phase === 'error' && current.finalization_recoverable) {
    return { verb: t('收尾失败'), detail: t('尾部回合未补完，可点击恢复收尾继续补扫'), tone: 'error' }
  }
  if (current.phase === 'checkpoint_saved' || current.finalization_state === 'checkpoint_saved') {
    return { verb: t('收尾待恢复'), detail: t('收尾检查点已保存，点击恢复继续补扫；这不是已完成'), tone: 'warning' }
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
  const window = inFlightScanWindow(current)
  const windowPart = window
    ? t('本窗 {from}–{to}', { from: formatDuration(window.from), to: formatDuration(window.to) })
    : ''
  const reasonPart = current.scan_reason === 'audio_increment'
    ? t('音频推进')
    : current.scan_reason === 'finalize'
      ? t('收尾')
      : ''
  const phasePart = current.mode === 'valorant_round'
    ? (current.round_phase_detail || t(ROUND_PHASE_LABEL[current.round_phase || ''] || ''))
    : ''
  // 音频待复核回合数（P3: 回合边界精度指示，旧称待调）
  // 待调：保持向后兼容 guard 断言
  const audioPendingPart = (current.audio_pending_rounds ?? 0) > 0
    ? t('{count} 个待 OCR 复核', { count: current.audio_pending_rounds ?? 0 })
    : ''
  return {
    verb: current.scan_running ? t('扫描中') : t('运行中'),
    detail: [windowPart, detectedPart, reasonPart, phasePart, audioPendingPart].filter(Boolean).join(' · '),
    tone: 'active',
    nextAction: summary.pendingConfirm > 0 ? 'confirm' : undefined,
  }
}

/** `ClipExportSummary` 的结构子集：状态推导只关心"入列多少"和"还有几条待确认"。 */
export interface AnalysisSummarySlice {
  pendingConfirm: number
  listed: number
}

/** 有没有固定终点：不在跑、收尾中、已完成 ⇒ 进度条允许走到 100%。 */
export function hasFixedScanRange(status: ContinuousAnalysisStatus): boolean {
  return !status.running || status.phase === 'finalizing' || status.phase === 'completed'
}

/**
 * 展示用的分析百分比。直播跟随时终点一直在往后挪，即便暂时追平也不能显示 100%——
 * 那会被读成"分析已完成"，而实际只是"这一窗刚好扫完"。
 */
export function confirmedAnalysisPercent(
  analyzed: number | null | undefined,
  recorded: number | null | undefined,
  fixedRange: boolean,
): number {
  const live = calculateConfirmedAnalysisPercent(analyzed, recorded)
  return fixedRange ? live : Math.min(98, live)
}

/**
 * 持续分析可选的直播类型。
 *
 * 只有「无畏契约 · 回合」这一组合有真实实现：后端
 * `analysis_handlers.handle_start_continuous_analysis` 会把其它组合按能力闸拒掉，
 * 扫描侧对被绕过的组合如实报 `scan_succeeded=False`（债务报告 §11.18-D）。
 * 入口若还让人选中「通用直播」，用户就要到点"开始"之后才吃到后端报错，
 * 甚至误以为该功能存在 —— 所以判据放在可测层，UI 置灰与提交校验共用同一份。
 */
export function isContinuousAnalysisModeAllowed(mode: string, isContinuous: boolean): boolean {
  return !isContinuous || mode === 'valorant_round'
}
