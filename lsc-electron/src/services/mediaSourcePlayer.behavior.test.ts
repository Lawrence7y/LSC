import { afterEach, describe, expect, it, vi } from 'vitest'
import { MsePlayer } from './mediaSourcePlayer'

/** MsePlayer 生命周期 / 背压 / goLive 的可执行行为（真调用 + mock video）。 */

function fakeVideo() {
  return {
    play: vi.fn(async () => {}),
    pause: vi.fn(),
    load: vi.fn(),
    removeAttribute: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    currentTime: 0,
    readyState: 0,
    muted: false,
    volume: 1,
    buffered: { length: 0 },
  }
}

const SEGMENT = () => new ArrayBuffer(8)

afterEach(() => {
  vi.restoreAllMocks()
})

describe('MsePlayer 背压协议', () => {
  it('pending 达到 pause 阈值（10）时通知后端 pause', () => {
    const events: Array<[string, number]> = []
    const player = new MsePlayer({
      videoElement: fakeVideo() as unknown as HTMLVideoElement,
      onBackpressure: (state, pending) => events.push([state, pending]),
    })
    for (let i = 0; i < 10; i += 1) player.feedMedia(SEGMENT())
    expect(events.map(([s]) => s)).toContain('pause')
    const pause = events.find(([s]) => s === 'pause')
    expect(pause?.[1]).toBeGreaterThanOrEqual(10)
  })

  it('stop() 必须 resume，否则后端永远拿不到 media', () => {
    const events: string[] = []
    const player = new MsePlayer({
      videoElement: fakeVideo() as unknown as HTMLVideoElement,
      onBackpressure: (state) => events.push(state),
    })
    for (let i = 0; i < 12; i += 1) player.feedMedia(SEGMENT())
    expect(events).toContain('pause')

    events.length = 0
    player.stop()
    expect(events).toContain('resume')
  })

  it('未暂停时 stop() 不产生多余 resume', () => {
    const events: string[] = []
    const player = new MsePlayer({
      videoElement: fakeVideo() as unknown as HTMLVideoElement,
      onBackpressure: (state) => events.push(state),
    })
    player.stop()
    expect(events).toEqual([])
  })

  it('阈值常量：pause@10 / resume@3（改动会被这条抓到）', () => {
    const player = new MsePlayer({
      videoElement: fakeVideo() as unknown as HTMLVideoElement,
      onBackpressure: () => {},
    })
    const p = player as unknown as {
      _backpressurePauseAt: number
      _backpressureResumeAt: number
    }
    expect(p._backpressurePauseAt).toBe(10)
    expect(p._backpressureResumeAt).toBe(3)
  })
})

describe('MsePlayer 生命周期：cleanup 收口', () => {
  it('stop 后 abort 并清空 AbortController（旧会话回调不得再落地）', () => {
    const player = new MsePlayer({
      videoElement: fakeVideo() as unknown as HTMLVideoElement,
    })
    const p = player as unknown as {
      _abortController: AbortController | null
      _pendingSegments: Uint8Array[]
    }
    const ac = new AbortController()
    p._abortController = ac
    p._pendingSegments.push(Uint8Array.from([1]))

    player.stop()

    expect(ac.signal.aborted).toBe(true)
    expect(p._abortController).toBeNull()
    expect(p._pendingSegments).toEqual([])
  })

  it('stop 后 state 回 idle（从非 idle 停止时通知 onStateChange）', () => {
    const onStateChange = vi.fn()
    const player = new MsePlayer({
      videoElement: fakeVideo() as unknown as HTMLVideoElement,
      onStateChange,
    })
    ;(player as unknown as { _state: string })._state = 'playing'
    player.stop()
    expect(onStateChange).toHaveBeenCalledWith('idle')
  })
})

describe('MsePlayer goLive', () => {
  function playingPlayer(buffered: { length: number; start(i: number): number; end(i: number): number }) {
    const video = {
      ...fakeVideo(),
      buffered,
    }
    const player = new MsePlayer({
      videoElement: video as unknown as HTMLVideoElement,
    })
    ;(player as unknown as { _state: string })._state = 'playing'
    return { player, video }
  }

  it('有缓冲时播放头落到缓冲末端前 0.3s（live 沿）', () => {
    const { player, video } = playingPlayer({
      length: 1,
      start: () => 0,
      end: () => 20,
    })
    player.goLive()
    expect(video.currentTime).toBeCloseTo(19.7, 5)
  })

  it('缓冲为空时绝不写 currentTime', () => {
    const { player, video } = playingPlayer({
      length: 0,
      start: () => 0,
      end: () => 0,
    })
    video.currentTime = 5
    player.goLive()
    expect(video.currentTime).toBe(5)
  })

  it('idle 状态直接返回，不碰播放头', () => {
    const video = fakeVideo()
    video.currentTime = 3
    const player = new MsePlayer({
      videoElement: video as unknown as HTMLVideoElement,
    })
    player.goLive()
    expect(video.currentTime).toBe(3)
    expect(video.play).not.toHaveBeenCalled()
  })
})

describe('MsePlayer 缓冲区间查询', () => {
  it('getBufferedRanges 只保留 end>start 的真实区间', () => {
    const video = {
      ...fakeVideo(),
      buffered: {
        length: 3,
        start: (i: number) => [0, 5, 10][i],
        end: (i: number) => [0, 8, 12][i],
      },
    }
    const player = new MsePlayer({
      videoElement: video as unknown as HTMLVideoElement,
    })
    expect(player.getBufferedRanges()).toEqual([
      { start: 5, end: 8 },
      { start: 10, end: 12 },
    ])
  })

  it('无缓冲 getBufferedRange 返回 null', () => {
    const video = {
      ...fakeVideo(),
      buffered: { length: 0, start: () => 0, end: () => 0 },
    }
    const player = new MsePlayer({
      videoElement: video as unknown as HTMLVideoElement,
    })
    expect(player.getBufferedRange()).toBeNull()
    expect(player.getBufferedRanges()).toEqual([])
  })
})

describe('MsePlayer sink 重建请求', () => {
  it('接单时返回 true 且不再进 error 终态', () => {
    const onRequestSinkRestart = vi.fn(() => true)
    const player = new MsePlayer({
      videoElement: fakeVideo() as unknown as HTMLVideoElement,
      onRequestSinkRestart,
    })
    const internals = player as unknown as { _trySinkRestart: (r: string) => boolean }
    expect(internals._trySinkRestart('stall recovery exhausted')).toBe(true)
    expect(onRequestSinkRestart).toHaveBeenCalled()
  })

  it('每个实例最多请求一次', () => {
    const onRequestSinkRestart = vi.fn(() => true)
    const player = new MsePlayer({
      videoElement: fakeVideo() as unknown as HTMLVideoElement,
      onRequestSinkRestart,
    })
    const internals = player as unknown as { _trySinkRestart: (r: string) => boolean }
    expect(internals._trySinkRestart('stall recovery exhausted')).toBe(true)
    expect(internals._trySinkRestart('forced seek recovery exhausted')).toBe(false)
    expect(onRequestSinkRestart).toHaveBeenCalledTimes(1)
  })

  it('未接线时不吞掉终态错误（返回 false）', () => {
    const player = new MsePlayer({
      videoElement: fakeVideo() as unknown as HTMLVideoElement,
    })
    const internals = player as unknown as { _trySinkRestart: (r: string) => boolean }
    expect(internals._trySinkRestart('stall recovery exhausted')).toBe(false)
  })

  it('文件回看没有后端 sink：不问回调', () => {
    const onRequestSinkRestart = vi.fn(() => true)
    const player = new MsePlayer({
      videoElement: fakeVideo() as unknown as HTMLVideoElement,
      isFile: true,
      onRequestSinkRestart,
    })
    const internals = player as unknown as { _trySinkRestart: (r: string) => boolean }
    expect(internals._trySinkRestart('stall')).toBe(false)
    expect(onRequestSinkRestart).not.toHaveBeenCalled()
  })
})
