import { beforeEach, describe, expect, it } from 'vitest'
import {
  SINK_RESTART_MAX_PER_WINDOW,
  SINK_RESTART_WINDOW_MS,
  clearSinkRestartBudget,
  noteSinkRestart,
  shouldRequestSinkRestart,
} from './previewRecovery'

describe('预览自救重开的房间级预算', () => {
  beforeEach(() => clearSinkRestartBudget())

  it('初始允许请求', () => {
    expect(shouldRequestSinkRestart('r1', 0)).toBe(true)
  })

  it('窗口内用满后拒绝（防止重建后再次卡死导致无限重启后端 FFmpeg）', () => {
    const now = 1_000_000
    for (let i = 0; i < SINK_RESTART_MAX_PER_WINDOW; i += 1) {
      expect(shouldRequestSinkRestart('r1', now)).toBe(true)
      noteSinkRestart('r1', now)
    }
    expect(shouldRequestSinkRestart('r1', now)).toBe(false)
  })

  it('窗口滑过后恢复预算', () => {
    const now = 5_000_000
    for (let i = 0; i < SINK_RESTART_MAX_PER_WINDOW; i += 1) noteSinkRestart('r1', now)
    expect(shouldRequestSinkRestart('r1', now)).toBe(false)
    expect(shouldRequestSinkRestart('r1', now + SINK_RESTART_WINDOW_MS + 1)).toBe(true)
  })

  it('房间之间互不影响', () => {
    for (let i = 0; i < SINK_RESTART_MAX_PER_WINDOW; i += 1) noteSinkRestart('r1', 0)
    expect(shouldRequestSinkRestart('r1', 0)).toBe(false)
    expect(shouldRequestSinkRestart('r2', 0)).toBe(true)
  })

  it('按房间/全部清零', () => {
    noteSinkRestart('r1', 0)
    noteSinkRestart('r2', 0)
    clearSinkRestartBudget('r1')
    expect(shouldRequestSinkRestart('r1', 0)).toBe(true)
    expect(shouldRequestSinkRestart('r2', 0)).toBe(true)
    clearSinkRestartBudget()
    expect(shouldRequestSinkRestart('r1', 0)).toBe(true)
  })

  it('只读检查不消耗预算', () => {
    for (let i = 0; i < SINK_RESTART_MAX_PER_WINDOW + 3; i += 1) {
      expect(shouldRequestSinkRestart('r1', 0)).toBe(true)
    }
  })
})
