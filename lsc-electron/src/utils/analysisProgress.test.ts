import { describe, expect, it } from 'vitest'
import { calculateConfirmedAnalysisPercent, inFlightScanWindow } from './analysisProgress'

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
