import { describe, expect, it } from 'vitest'
import {
  formatAiRoundClipLabel,
  formatClipHoverTitle,
  formatManualClipLabel,
  sanitizeStreamerName,
} from './clipNaming'
import { countManualClips, distributeManualIndexes, buildManualClip } from './manualClip'

describe('clipNaming：手动 _M / AI _R 命名', () => {
  it('sanitizeStreamerName 用 _ 替换非法字符并截断', () => {
    // 非法字符替换为 _，再按 maxLen=6 截断（'主播A_B_C' 共 7 字 → '主播A_B_'）
    expect(sanitizeStreamerName('主播A/B:C')).toBe('主播A_B_')
    expect(sanitizeStreamerName('A/B')).toBe('A_B')
    expect(sanitizeStreamerName('x'.repeat(20)).length).toBeLessThanOrEqual(6)
    // 空名回落「未知」
    expect(sanitizeStreamerName('')).toBe('未知')
  })

  it('formatManualClipLabel 使用 _M 两位序号', () => {
    expect(formatManualClipLabel('主播A', 1)).toBe('主播A_M01')
    expect(formatManualClipLabel('主播A', 12)).toBe('主播A_M12')
  })

  it('formatAiRoundClipLabel 使用 _R 两位序号', () => {
    expect(formatAiRoundClipLabel('主播A', 3)).toBe('主播A_R03')
    expect(formatAiRoundClipLabel('主播A', 3)).not.toContain('_M')
  })

  it('hover 标题拼接房间名 / 标签 / 时间段', () => {
    const title = formatClipHoverTitle('主播A_M01', {
      roomName: '直播间',
      start: 10,
      end: 20,
      formatTime: (s: number) => `${s}s`,
    })
    expect(title).toContain('直播间')
    expect(title).toContain('主播A_M01')
    expect(title).toContain('10s – 20s')
  })
})

describe('manualClip：按房间计数与墙钟快照', () => {
  it('countManualClips 只数指定房间的 source=manual 切片', () => {
    const clips = [
      { room_id: 'r1', source: 'manual' },
      { room_id: 'r1', source: 'manual' },
      { room_id: 'r2', source: 'manual' },
      { room_id: 'r1', source: 'ai_highlight' },
    ] as never
    expect(countManualClips(clips, 'r1')).toBe(2)
    expect(countManualClips(clips, 'r2')).toBe(1)
    expect(countManualClips(clips, 'r3')).toBe(0)
  })

  it('多房同时添加不跳号：各自从本房 base 续', () => {
    const base = (roomId: string) => (roomId === 'r1' ? 2 : 0)
    const assigned = distributeManualIndexes([
      { room_id: 'r1' },
      { room_id: 'r1' },
      { room_id: 'r2' },
    ], base)
    expect(assigned).toEqual([3, 4, 1])
  })

  it('buildManualClip 快照墙钟三件套，precision=exact', () => {
    const c = buildManualClip({
      room_id: 'r1',
      streamer_name: '主播A',
      mark_in: 10,
      mark_out: 30,
      mark_in_wallclock: 100,
      mark_out_wallclock: 120,
      recording_start_mono: 50,
      recording_media_start_mono: 60,
      content_offset: 1,
    }, 3)
    expect(c).not.toBeNull()
    expect(c).toMatchObject({
      room_id: 'r1',
      start: 10,
      end: 30,
      source: 'manual',
      mark_in_wallclock: 100,
      mark_out_wallclock: 120,
      recording_start_mono: 50,
      recording_media_start_mono: 60,
      content_offset: 1,
    })
    expect(c!.label).toBe('主播A_M03')
    expect(c!.mark_precision).toBe('exact')
  })

  it('缺墙钟字段时不伪装精确切片（approximate）', () => {
    const c = buildManualClip({
      room_id: 'r1',
      streamer_name: 'X',
      mark_in: 1,
      mark_out: 2,
    }, 1)
    expect(c).not.toBeNull()
    expect(c!.mark_precision).toBe('approximate')
    expect(c!.clip_snapshot_id).toBeUndefined()
  })

  it('mark 入/出任一非有限值时返回 null', () => {
    expect(buildManualClip({ room_id: 'r1' }, 1)).toBeNull()
    expect(buildManualClip({ room_id: 'r1', mark_in: 1, mark_out: Number.NaN }, 1)).toBeNull()
    expect(buildManualClip({ room_id: 'r1', mark_in: Number.POSITIVE_INFINITY, mark_out: 2 }, 1)).toBeNull()
  })
})
