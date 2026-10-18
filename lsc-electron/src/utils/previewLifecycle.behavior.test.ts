import { describe, expect, it } from 'vitest'
import { previewPhaseCopy, shouldRebuildLivePlayer } from './previewLifecycle'
import type { I18nT } from '@/i18n'

const t = ((key: string) => key) as I18nT

describe('shouldRebuildLivePlayer：预览源切换才重建播放器', () => {
  it('首次记录预览源不重建（挂载即拆会造成无谓重连）', () => {
    expect(shouldRebuildLivePlayer(null, { mode: 'live_mse', epoch: 'e1' })).toBe(false)
  })

  it('同 epoch 重复广播不重建', () => {
    const prev = { mode: 'live_mse', epoch: 'e1' }
    expect(shouldRebuildLivePlayer(prev, { mode: 'live_mse', epoch: 'e1' })).toBe(false)
  })

  it('空 epoch → 非空 epoch 重建一次', () => {
    expect(shouldRebuildLivePlayer(
      { mode: 'live_mse', epoch: '' },
      { mode: 'live_mse', epoch: 'e1' },
    )).toBe(true)
  })

  it('epoch 变化才重建；后端还没给 epoch 时一律不重建', () => {
    expect(shouldRebuildLivePlayer(
      { mode: 'live_mse', epoch: 'e1' },
      { mode: 'live_mse', epoch: 'e2' },
    )).toBe(true)
    expect(shouldRebuildLivePlayer(
      { mode: 'live_mse', epoch: 'e1' },
      { mode: 'live_mse', epoch: '' },
    )).toBe(false)
    expect(shouldRebuildLivePlayer(
      { mode: 'live_mse', epoch: '' },
      { mode: 'live_mse', epoch: '' },
    )).toBe(false)
  })

  it('live ↔ recording_review 不拆 LivePlayer（回看是另一个播放器）', () => {
    expect(shouldRebuildLivePlayer(
      { mode: 'live_mse', epoch: 'e1' },
      { mode: 'recording_review', epoch: 'e1' },
    )).toBe(false)
    expect(shouldRebuildLivePlayer(
      { mode: 'recording_review', epoch: 'e1' },
      { mode: 'live_mse', epoch: 'e1' },
    )).toBe(false)
  })
})

describe('previewPhaseCopy：阶段文案平台无关', () => {
  it('刷新流地址：专属文案 + 首次等待提示', () => {
    const copy = previewPhaseCopy('refreshing_url', t)
    expect(copy.text).toBe('正在刷新流地址…')
    expect(copy.hint).toBe('首次刷新可能需要 10–30 秒')
  })

  it('探测阶段：有文案但没有首次 10–30 秒提示', () => {
    const copy = previewPhaseCopy('probing', t)
    expect(copy.text).toBe('正在探测/转码…')
    expect(copy.hint).toBeNull()
  })

  it('phase 缺失 / idle / streaming 落到默认拉流文案', () => {
    for (const phase of [undefined, null, '', 'idle', 'streaming', 'error']) {
      const copy = previewPhaseCopy(phase as never, t)
      expect(copy.text).toBe('正在拉流/转码…')
      expect(copy.hint).toBeNull()
    }
  })

  it('任何阶段文案不得出现平台名（预览链路对各平台同构）', () => {
    const platforms = ['bilibili', 'douyin', 'huya', 'douyu', 'twitch', 'youtube']
    for (const phase of ['refreshing_url', 'probing', 'streaming', undefined]) {
      const copy = previewPhaseCopy(phase as never, t)
      const blob = `${copy.text} ${copy.hint ?? ''}`.toLowerCase()
      for (const p of platforms) {
        expect(blob).not.toContain(p)
      }
    }
  })
})
