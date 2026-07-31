import { describe, expect, it } from 'vitest'
import { calculateConfirmedAnalysisPercent } from './analysisProgress'

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
