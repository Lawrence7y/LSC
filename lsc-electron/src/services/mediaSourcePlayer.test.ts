import { describe, expect, it, vi } from 'vitest'
import { MsePlayer } from './mediaSourcePlayer'
import { MIN_PLAYBACK_BUFFER_SECONDS } from '@/utils/replaySettings'

/** 最小 video 假件：构造/喂段/stop 路径都不需要真实 DOM 与 MSE。 */
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

describe('MsePlayer 预览 backpressure 收口（2026-09-15 现场事故）', () => {
  it('pending 超过阈值时向后端发 pause', () => {
    const events: Array<[string, number]> = []
    const player = new MsePlayer({
      videoElement: fakeVideo() as unknown as HTMLVideoElement,
      onBackpressure: (state, pending) => events.push([state, pending]),
    })

    for (let i = 0; i < 10; i += 1) player.feedMedia(SEGMENT())

    expect(events.map(([state]) => state)).toContain('pause')
  })

  it('stop() 必须收口：不能把后端留在暂停态（否则永远拿不到 media）', () => {
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

  it('无缓存 init 段时不做硬重建（避免吞掉终态错误路径）', () => {
    const player = new MsePlayer({
      videoElement: fakeVideo() as unknown as HTMLVideoElement,
      onBackpressure: () => {},
    })
    const internals = player as unknown as {
      _resetStreamPipeline: (reason: string, recovery?: boolean) => boolean
      _tryHardResetRecovery: (reason: string) => boolean
    }

    expect(internals._resetStreamPipeline('test', true)).toBe(false)
    expect(internals._tryHardResetRecovery('test')).toBe(false)
  })
})

describe('回放缓冲状态对外可见（2026-09-15：设置值 vs 实际可回放）', () => {
  function makePlayer(replayBufferSeconds?: number) {
    return new MsePlayer({
      videoElement: fakeVideo() as unknown as HTMLVideoElement,
      replayBufferSeconds,
    })
  }

  it('初始状态：实际=设置，未降级', () => {
    const status = makePlayer(300).getReplayBufferStatus()
    expect(status.configuredSeconds).toBe(300)
    expect(status.effectiveSeconds).toBe(300)
    expect(status.degraded).toBe(false)
  })

  it('关闭回放档位用统一硬下限（不再是 15s / trim 30s 两套口径）', () => {
    const status = makePlayer(0).getReplayBufferStatus()
    expect(status.configuredSeconds).toBe(MIN_PLAYBACK_BUFFER_SECONDS)
    expect(status.effectiveSeconds).toBe(MIN_PLAYBACK_BUFFER_SECONDS)
  })

  it('配额缩容后必须报 degraded，并给出真实保留量', () => {
    const player = makePlayer(300)
    const internals = player as unknown as {
      _handleAppendError: (e: unknown, seg: Uint8Array, context: string) => void
    }
    const quota = Object.assign(new Error('full'), { name: 'QuotaExceededError' })
    internals._handleAppendError(quota, new Uint8Array(8), 'test')

    const status = player.getReplayBufferStatus()
    expect(status.configuredSeconds).toBe(300)
    expect(status.effectiveSeconds).toBe(180) // 300 × 0.6
    expect(status.degraded).toBe(true)
  })

  it('用户改档即复位降级：否则升档后永远回不到设置值', () => {
    const player = makePlayer(300)
    const internals = player as unknown as {
      _handleAppendError: (e: unknown, seg: Uint8Array, context: string) => void
    }
    internals._handleAppendError(
      Object.assign(new Error('full'), { name: 'QuotaExceededError' }),
      new Uint8Array(8),
      'test',
    )
    expect(player.getReplayBufferStatus().degraded).toBe(true)

    player.setReplayBufferSeconds(300)
    const status = player.getReplayBufferStatus()
    expect(status.effectiveSeconds).toBe(300)
    expect(status.degraded).toBe(false)
  })

  it('错误诊断快照含关键字段（现场 debug:false 时唯一证据）', () => {
    const player = new MsePlayer({
      videoElement: fakeVideo() as unknown as HTMLVideoElement,
    })
    const snapshot = (player as unknown as { _healthSnapshot: () => string })._healthSnapshot()

    for (const field of ['state=', 'pending=', 'buffered=[', 'stall=', 'forced=', 'bpPaused=']) {
      expect(snapshot).toContain(field)
    }
  })
})
