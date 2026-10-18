import { describe, expect, it } from 'vitest'
import type { I18nT } from '@/i18n'
import { previewPhaseCopy, shouldRebuildLivePlayer } from './previewLifecycle'

const t: I18nT = (key: string, params?: Record<string, string | number>) =>
  (params ? `${key}|${JSON.stringify(params)}` : key)

describe('shouldRebuildLivePlayer', () => {
  it('首次记录预览源时不重建（挂载即拆播放器会造成一次无谓重连）', () => {
    expect(shouldRebuildLivePlayer(null, { mode: 'live_mse', epoch: '' })).toBe(false)
    expect(shouldRebuildLivePlayer(null, { mode: 'live_mse', epoch: 'e1' })).toBe(false)
  })

  it('同 epoch 的重复广播不重建（心跳/重排不得清缓存重来）', () => {
    expect(shouldRebuildLivePlayer(
      { mode: 'live_mse', epoch: 'e1' },
      { mode: 'live_mse', epoch: 'e1' },
    )).toBe(false)
  })

  it('拿到第一个非空 epoch 时重建一次（从 "" 到 "e1"）', () => {
    expect(shouldRebuildLivePlayer(
      { mode: 'live_mse', epoch: '' },
      { mode: 'live_mse', epoch: 'e1' },
    )).toBe(true)
  })

  it('epoch 变化才重建；后端还没给 epoch（空串）时一律不重建', () => {
    expect(shouldRebuildLivePlayer(
      { mode: 'live_mse', epoch: 'e1' },
      { mode: 'live_mse', epoch: 'e2' },
    )).toBe(true)
    expect(shouldRebuildLivePlayer(
      { mode: 'live_mse', epoch: 'e1' },
      { mode: 'live_mse', epoch: '' },
    )).toBe(false)
  })

  it('live ↔ recording_review 切换不拆 LivePlayer（回看是另一个播放器）', () => {
    expect(shouldRebuildLivePlayer(
      { mode: 'live_mse', epoch: 'e1' },
      { mode: 'recording_review', epoch: 'e1' },
    )).toBe(false)
  })
})

describe('previewPhaseCopy', () => {
  it('刷新流地址：专属文案 + 首次等待提示', () => {
    expect(previewPhaseCopy('refreshing_url', t)).toEqual({
      text: '正在刷新流地址…',
      hint: '首次刷新可能需要 10–30 秒',
    })
  })

  it('探测阶段：有文案但没有"首次 10–30 秒"提示', () => {
    expect(previewPhaseCopy('probing', t)).toEqual({
      text: '正在探测/转码…',
      hint: null,
    })
  })

  it('其余（含 phase 缺失/idle/streaming/error）落到默认拉流文案', () => {
    for (const phase of [undefined, 'idle', 'streaming', 'error', 'whatever']) {
      expect(previewPhaseCopy(phase, t)).toEqual({ text: '正在拉流/转码…', hint: null })
    }
  })

  it('任何阶段的文案都不得按平台特化（预览链路对各平台同构）', () => {
    const phases = ['refreshing_url', 'probing', 'streaming', 'idle', 'error', undefined]
    for (const phase of phases) {
      const copy = previewPhaseCopy(phase, t)
      const blob = `${copy.text}${copy.hint ?? ''}`.toLowerCase()
      for (const platform of ['bilibili', 'huya', 'douyu', 'douyin', 'kuaishou', 'weibo', 'xiaohongshu']) {
        expect(blob).not.toContain(platform)
      }
    }
  })
})
