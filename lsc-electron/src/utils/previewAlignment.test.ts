import { describe, expect, it, vi } from 'vitest'
import { applyAlignmentSeek, NEGLIGIBLE_ALIGN_OFFSET_SEC } from './previewAlignment'

type TestVideo = {
  currentTime: number
  play: ReturnType<typeof vi.fn>
} & HTMLVideoElement

function videoAt(
  currentTime: number,
  ranges: Array<[number, number]>,
  opts?: { throwOnSeek?: boolean },
): TestVideo {
  const video = {
    currentTime,
    play: vi.fn(async () => {}),
    buffered: {
      length: ranges.length,
      start: (i: number) => ranges[i][0],
      end: (i: number) => ranges[i][1],
    },
  }
  if (opts?.throwOnSeek) {
    Object.defineProperty(video, 'currentTime', {
      get: () => currentTime,
      set: () => {
        throw new Error('InvalidStateError')
      },
    })
  }
  return video as unknown as TestVideo
}

describe('applyAlignmentSeek 一键对齐落点（Workbench 内联逻辑外移，含可执行用例）', () => {
  it('正偏移：currentTime 后退 offset，并重新触发播放', () => {
    const video = videoAt(100, [[80, 120]])
    expect(applyAlignmentSeek(video, 2.5)).toBe('seeked')
    expect(video.currentTime).toBeCloseTo(97.5, 6)
    expect(video.play).toHaveBeenCalledTimes(1)
  })

  it('后退过头时夹到缓冲起点：不得 seek 进没有数据的区间', () => {
    const video = videoAt(81, [[80, 120]])
    expect(applyAlignmentSeek(video, 30)).toBe('seeked')
    expect(video.currentTime).toBe(80)
  })

  it('差值小于阈值判为 negligible：完全不写播放头', () => {
    const video = videoAt(100, [[80, 120]])
    expect(NEGLIGIBLE_ALIGN_OFFSET_SEC).toBe(0.05)
    expect(applyAlignmentSeek(video, 0.04)).toBe('negligible')
    expect(video.currentTime).toBe(100)
    expect(video.play).not.toHaveBeenCalled()
  })

  it('无缓冲 / 播放器还没建好：no-buffer，不写播放头也不 play', () => {
    const empty = videoAt(50, [])
    expect(applyAlignmentSeek(empty, 3)).toBe('no-buffer')
    expect(empty.currentTime).toBe(50)
    expect(empty.play).not.toHaveBeenCalled()
    expect(applyAlignmentSeek(null, 3)).toBe('no-buffer')
    expect(applyAlignmentSeek(undefined, 3)).toBe('no-buffer')
  })

  it('负偏移（前向修正）按符号求差并夹在缓冲末端内，而不是被当成 0 丢掉', () => {
    const video = videoAt(100, [[80, 120]])
    expect(applyAlignmentSeek(video, -4)).toBe('seeked')
    expect(video.currentTime).toBeCloseTo(104, 6)

    const clamped = videoAt(118, [[80, 120]])
    expect(applyAlignmentSeek(clamped, -40)).toBe('seeked')
    expect(clamped.currentTime).toBe(120)
  })

  it('多段缓冲以首段起点/末段末端为夹逼边界（与原内联实现同一口径）', () => {
    const video = videoAt(200, [[80, 120], [190, 240]])
    expect(applyAlignmentSeek(video, 20)).toBe('seeked')
    expect(video.currentTime).toBeCloseTo(180, 6)
  })

  it('偏移可忽略时优先于"没有缓冲"：播放器还没挂上的房间也算已对齐', () => {
    expect(applyAlignmentSeek(null, 0.01)).toBe('negligible')
    expect(applyAlignmentSeek(videoAt(50, []), 0.02)).toBe('negligible')
  })

  it('setter 抛异常时如实返回 seek-failed，但仍按原口径重新播放', () => {
    const video = videoAt(100, [[80, 120]], { throwOnSeek: true })
    expect(applyAlignmentSeek(video, 5)).toBe('seek-failed')
    expect(video.play).toHaveBeenCalledTimes(1)
  })
})
