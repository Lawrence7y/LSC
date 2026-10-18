import { describe, expect, it } from 'vitest'
import type { I18nT } from '@/i18n'
import type { ContinuousAnalysisStatus } from '@/types'
import {
  calculateConfirmedAnalysisPercent,
  confirmedAnalysisPercent,
  derivePrimaryStatus,
  formatDuration,
  formatFps,
  formatThroughput,
  hasFixedScanRange,
  inFlightScanWindow,
  isContinuousAnalysisModeAllowed,
  type AnalysisSummarySlice,
} from './analysisProgress'

describe('isContinuousAnalysisModeAllowed（持续分析入口的直播类型判据）', () => {
  it('持续分析只放行「无畏契约 · 回合」', () => {
    expect(isContinuousAnalysisModeAllowed('valorant_round', true)).toBe(true)
    expect(isContinuousAnalysisModeAllowed('generic', true)).toBe(false)
  })

  it('一次性/多房同步分析不受限制，通用档照旧可选', () => {
    expect(isContinuousAnalysisModeAllowed('generic', false)).toBe(true)
    expect(isContinuousAnalysisModeAllowed('valorant_round', false)).toBe(true)
  })

  it('未知 mode 在持续分析下保守拒绝（后端能力闸只认回合档）', () => {
    expect(isContinuousAnalysisModeAllowed('', true)).toBe(false)
    expect(isContinuousAnalysisModeAllowed('scene', true)).toBe(false)
  })
})

describe('calculateConfirmedAnalysisPercent', () => {
  it('只按后台确认的已分析时长计算覆盖率', () => {
    expect(calculateConfirmedAnalysisPercent(25, 100)).toBe(25)
    expect(calculateConfirmedAnalysisPercent(95, 100)).toBe(95)
  })

  it('不会超过 100%，无有效录制时长时保持 0', () => {
    expect(calculateConfirmedAnalysisPercent(120, 100)).toBe(100)
    expect(calculateConfirmedAnalysisPercent(10, 0)).toBe(0)
    expect(calculateConfirmedAnalysisPercent(undefined, 100)).toBe(0)
  })
})

describe('inFlightScanWindow', () => {
  it('扫描中展示本窗区间，避免进度条只显示已确认的 0s', () => {
    expect(inFlightScanWindow({
      scan_running: true,
      scan_in_sec: 0,
      scan_out_sec: 45,
    })).toEqual({ from: 0, to: 45 })
  })

  it('未在扫描或区间无效时不展示', () => {
    expect(inFlightScanWindow({ scan_running: false, scan_in_sec: 0, scan_out_sec: 45 })).toBeNull()
    expect(inFlightScanWindow({ scan_running: true, scan_in_sec: 0, scan_out_sec: 0 })).toBeNull()
    expect(inFlightScanWindow({ scan_running: true, scan_in_sec: null, scan_out_sec: null })).toBeNull()
  })
})

describe('hasFixedScanRange / confirmedAnalysisPercent', () => {
  it('直播跟随中终点持续后移，暂时追平也不得显示 100%', () => {
    expect(hasFixedScanRange({ running: true, phase: 'running' } as ContinuousAnalysisStatus)).toBe(false)
    expect(confirmedAnalysisPercent(100, 100, false)).toBe(98)
  })

  it('未运行 / 收尾中 / 已完成才有固定终点，可走满 100%', () => {
    expect(hasFixedScanRange({ running: false } as ContinuousAnalysisStatus)).toBe(true)
    expect(hasFixedScanRange({ running: true, phase: 'finalizing' } as ContinuousAnalysisStatus)).toBe(true)
    expect(hasFixedScanRange({ running: true, phase: 'completed' } as ContinuousAnalysisStatus)).toBe(true)
    expect(confirmedAnalysisPercent(100, 100, true)).toBe(100)
  })

  it('未到上限时两条口径一致', () => {
    expect(confirmedAnalysisPercent(50, 100, true)).toBe(50)
    expect(confirmedAnalysisPercent(50, 100, false)).toBe(50)
  })
})

describe('formatDuration / formatThroughput / formatFps', () => {
  it('非有限值与负数一律显示 0s，不出现 NaN/Infinity', () => {
    expect(formatDuration(0)).toBe('0s')
    expect(formatDuration(-5)).toBe('0s')
    expect(formatDuration(Number.NaN)).toBe('0s')
    expect(formatDuration(Number.POSITIVE_INFINITY)).toBe('0s')
  })

  it('按量级选择单位，丢掉没有意义的段', () => {
    expect(formatDuration(45)).toBe('45s')
    expect(formatDuration(125)).toBe('2m 5s')
    expect(formatDuration(3661)).toBe('1h 1m 1s')
    expect(formatDuration(3600)).toBe('1h 0m 0s')
  })

  it('指标缺失或非法时用破折号，不显示 NaNx / NaN FPS', () => {
    expect(formatThroughput(undefined)).toBe('–')
    expect(formatThroughput(Number.NaN)).toBe('–')
    expect(formatThroughput(1.234)).toBe('1.23x')
    expect(formatFps(undefined)).toBe('–')
    expect(formatFps(0)).toBe('–')
    expect(formatFps(29.97)).toBe('30.0 FPS')
  })
})

// ────────────────── derivePrimaryStatus：一条有优先级的判定链 ──────────────────
// `t` 用恒等桩：断言直接落在中文协议串上，同时能看见插值参数有没有真的传进去。
const t: I18nT = (key: string, params?: Record<string, string | number>) =>
  (params ? `${key}|${JSON.stringify(params)}` : key)

const emptySummary: AnalysisSummarySlice = { pendingConfirm: 0, listed: 0 }

function derive(
  status: Partial<ContinuousAnalysisStatus>,
  summary: Partial<AnalysisSummarySlice> = {},
) {
  return derivePrimaryStatus(
    status as ContinuousAnalysisStatus,
    { ...emptySummary, ...summary },
    t,
  )
}

describe('derivePrimaryStatus 的优先级（同时成立时用户看到哪一个）', () => {
  it('stalled 赢过 error：先说"未检测到对局"，不要把重锚状态报成异常', () => {
    const r = derive({ phase: 'stalled', error: 'boom', stalled: true })
    expect(r.verb).toBe('未检测到对局')
    expect(r.tone).toBe('warning')
  })

  it('只有 stalled 布尔位（后端未同步 phase 字符串）也要认出该状态', () => {
    expect(derive({ stalled: true, error: 'boom' }).verb).toBe('未检测到对局')
  })

  it('收尾可恢复的 error 显示"收尾失败 + 恢复入口"，不是通用异常', () => {
    const r = derive({ phase: 'error', error: 'boom', finalization_recoverable: true })
    expect(r.verb).toBe('收尾失败')
    expect(r.detail).toContain('恢复收尾')
    expect(r.tone).toBe('error')
  })

  it('检查点待恢复赢过 completed：绝不能把"没扫完"显示成"已完成"', () => {
    const byPhase = derive({ phase: 'checkpoint_saved', total_highlights: 3 })
    const byFinalization = derive({ phase: 'completed', finalization_state: 'checkpoint_saved' })
    expect(byPhase.verb).toBe('收尾待恢复')
    expect(byFinalization.verb).toBe('收尾待恢复')
    expect(byFinalization.tone).toBe('warning')
    expect(byFinalization.detail).toContain('这不是已完成')
  })

  it('普通 error 优先于 completed（带 error 的完成态仍按完成显示，因为 completed 分支在前）', () => {
    expect(derive({ phase: 'error', error: 'boom' }).verb).toBe('持续分析异常')
    expect(derive({ phase: 'error' }).detail).toBe('请重试或查看日志')
  })

  it('只置了 error 字段、phase 还在运行态时也要报异常', () => {
    // 后端不保证 phase 与 error 同步落地；只看 phase 会让异常被"运行中"文案盖掉。
    const r = derive({ running: true, scan_running: true, error: 'boom' })
    expect(r.verb).toBe('持续分析异常')
    expect(r.tone).toBe('error')
  })

  it('CPU 兜底让位于"上一轮超时"：先报最紧迫的那件事', () => {
    const r = derive({
      running: true,
      last_scan_error: 'timeout',
      provider: 'CPUExecutionProvider',
    })
    expect(r.verb).toBe('扫描恢复中')
  })
})

describe('derivePrimaryStatus 的各个终态', () => {
  it('完成且有切片待确认 ⇒ 语气转 warning 并给出"去确认"动作', () => {
    const r = derive({ phase: 'completed', total_highlights: 6 }, { pendingConfirm: 2, listed: 6 })
    expect(r.verb).toBe('分析完成')
    expect(r.detail).toBe('入列 {listed} 回合 · {pending} 条待确认|{"listed":6,"pending":2}')
    expect(r.tone).toBe('warning')
    expect(r.nextAction).toBe('confirm')
  })

  it('完成且无待确认 ⇒ success 语气、不带引导动作', () => {
    const r = derive({ phase: 'completed' }, { listed: 4 })
    expect(r.verb).toBe('已完成')
    expect(r.detail).toBe('入列 {listed} 回合|{"listed":4}')
    expect(r.tone).toBe('success')
    expect(r.nextAction).toBeUndefined()
  })

  it('listed 取 summary，缺失时回落到 total_highlights', () => {
    expect(derive({ phase: 'completed', total_highlights: 9 }, { listed: 0 }).detail)
      .toContain('"listed":9')
  })

  it('停止中是 idle 语气（不是错误），且优先于"分析异常退出"', () => {
    const r = derive({ phase: 'stopping', analysis_stage: '异常退出' })
    expect(r.verb).toBe('停止中…')
    expect(r.tone).toBe('idle')
  })

  it('收尾中把已运行秒数带上；首次收尾没有秒数时给一句解释', () => {
    expect(derive({ phase: 'finalizing', scan_elapsed_sec: 71 }).detail)
      .toBe('最终回合确认 · 已运行 {elapsed}s（首次约 1–2 分钟）|{"elapsed":71}')
    expect(derive({ phase: 'finalizing' }).detail)
      .toBe('停录后做一次收尾扫描，尾部回合补入列表')
  })

  it('等待收尾提示要先停录，且不残留引导动作', () => {
    const r = derive({ analysis_stage: '等待收尾' }, { pendingConfirm: 3 })
    expect(r.verb).toBe('等待收尾')
    expect(r.tone).toBe('idle')
    expect(r.nextAction).toBeUndefined()
  })

  it('降级追赶与音频兜底同义', () => {
    expect(derive({ degraded_mode: 'audio_only' }).verb).toBe('降级追赶中')
    expect(derive({ analysis_stage: '降级追赶' }).verb).toBe('降级追赶中')
  })

  it('异常退出与视觉模型不可用都按 error 语气处理', () => {
    expect(derive({ analysis_stage: '异常退出' }).tone).toBe('error')
    expect(derive({ analysis_stage: '视觉模型不可用' }).verb).toBe('分析异常退出')
    expect(derive({ analysis_stage: '收尾失败' }).verb).toBe('收尾失败')
  })

  it('CPU 兜底两种来源都要认（provider 字符串 / 告警文案里含它）', () => {
    expect(derive({ running: true, provider: 'CPUExecutionProvider' }).verb).toBe('CPU 慢速分析')
    expect(derive({ running: true, provider_warning: '已回退 CPUExecutionProvider' }).verb)
      .toBe('CPU 慢速分析')
  })

  it('等待录制与等待片段是两种 idle 文案', () => {
    expect(derive({ analysis_stage: '等待新录制' }).verb).toBe('等待录制')
    expect(derive({ analysis_stage: '等待可分析片段' }).verb).toBe('等待片段')
    expect(derive({ analysis_stage: '等待新片段' }).verb).toBe('等待片段')
  })
})

describe('derivePrimaryStatus 的运行态细节拼接', () => {
  it('扫描中显示本窗、最近切片、推进原因、回合相位与待复核数', () => {
    const r = derive({
      running: true,
      scan_running: true,
      scan_in_sec: 10,
      scan_out_sec: 40,
      last_detected_in_sec: 12,
      last_detected_out_sec: 75,
      scan_reason: 'audio_increment',
      mode: 'valorant_round',
      round_phase: 'combat',
      audio_pending_rounds: 2,
    }, { pendingConfirm: 1 })
    expect(r.verb).toBe('扫描中')
    expect(r.tone).toBe('active')
    expect(r.nextAction).toBe('confirm')
    expect(r.detail).toBe(
      '本窗 {from}–{to}|{"from":"10s","to":"40s"}'
      + ' · 最近切片：入 {in} · 出 {out}|{"in":"12s","out":"1m 15s"}'
      + ' · 音频推进 · 交战中 · {count} 个待 OCR 复核|{"count":2}',
    )
  })

  it('没有相位细节时用内置中文标签；场景模式不拼相位段', () => {
    expect(derive({
      running: true, mode: 'valorant_round', round_phase: 'buy',
    }).detail).toBe('买枪期')
    expect(derive({ running: true, mode: 'scene', round_phase: 'buy' }).detail).toBe('')
  })

  it('round_phase 缺失时不拼相位段；显式 unknown 才显示"寻找回合"', () => {
    // 记为现状：`ROUND_PHASE_LABEL` 只有 `unknown` 键，字段整个缺席时得到空串。
    // 二者语义相同（都不知道相位），但改文案属于用户可见变化，留待产品确认（报告 §11.23）。
    expect(derive({ running: true, mode: 'valorant_round', round_phase: 'unknown' }).detail)
      .toBe('寻找回合')
    expect(derive({ running: true, mode: 'valorant_round' }).detail).toBe('')
  })

  it('待复核为 0 时不显示复核段；切片信息有一端缺失时整段不显示', () => {
    const r = derive({
      running: true,
      scan_reason: 'finalize',
      last_detected_in_sec: 5,
      last_detected_out_sec: Number.NaN,
    })
    expect(r.detail).toBe('收尾')
  })

  it('未扫描时动词是"运行中"而不是"扫描中"', () => {
    expect(derive({ running: true, scan_running: false }).verb).toBe('运行中')
  })
})
