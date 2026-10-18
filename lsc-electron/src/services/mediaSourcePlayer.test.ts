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

  it('resume 不受节流限制：暂停后队列排空必须立即送出（后端不得停留在暂停态）', () => {
    // 2026-09-24 修复：旧实现先翻 _backpressurePaused 再过 500ms 节流，
    // resume 被节流丢弃时前端认为已恢复、后端仍在该房丢弃所有 media 段，
    // 直到 20s 自愈 —— §7.2.2 事故签名的弱化复现。
    const events: string[] = []
    const player = new MsePlayer({
      videoElement: fakeVideo() as unknown as HTMLVideoElement,
      onBackpressure: (state) => events.push(state),
    })
    const internals = player as unknown as {
      _maybeEmitBackpressure(): void
      _pendingSegments: unknown[]
      _backpressurePauseAt: number
      _backpressureResumeAt: number
    }

    internals._pendingSegments = new Array(internals._backpressurePauseAt).fill({})
    internals._maybeEmitBackpressure()
    expect(events).toEqual(['pause'])

    // 同一节流窗口（<500ms）内队列排空到 resumeAt：resume 必须照发
    events.length = 0
    internals._pendingSegments.length = internals._backpressureResumeAt
    internals._maybeEmitBackpressure()
    expect(events).toEqual(['resume'])
  })

  it('pause 仍受节流约束：恢复后的瞬时再暂停不得刷屏后端', () => {
    const events: string[] = []
    const player = new MsePlayer({
      videoElement: fakeVideo() as unknown as HTMLVideoElement,
      onBackpressure: (state) => events.push(state),
    })
    const internals = player as unknown as {
      _maybeEmitBackpressure(): void
      _pendingSegments: unknown[]
      _backpressurePauseAt: number
      _backpressureResumeAt: number
    }

    internals._pendingSegments = new Array(internals._backpressurePauseAt).fill({})
    internals._maybeEmitBackpressure()
    internals._pendingSegments.length = internals._backpressureResumeAt
    internals._maybeEmitBackpressure()
    // 同一 500ms 窗口内再次越限：pause 被节流拦下（状态保持与后端一致）
    internals._pendingSegments = new Array(internals._backpressurePauseAt).fill({})
    internals._maybeEmitBackpressure()

    expect(events).toEqual(['pause', 'resume'])
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

  it('冻结过程要周期性 WARN，且报的是累计冻结时长', () => {
    // 现场 debug:false 时 _log 全被吞，只有 _handleError 那条 console.error 进
    // debug.log —— 它记录死亡瞬间，拿不到致死曲线（2026-09-15 滞后 -113s→-158s）。
    const video = fakeVideo()
    const player = new MsePlayer({ videoElement: video as unknown as HTMLVideoElement })
    const internals = player as unknown as {
      _warnFrozenProgression: () => void
      _frozenSinceMs: number
      _lastBufferEndTime: number
      _lastFrozenWarnAtMs: number
    }
    video.buffered = { length: 1, start: () => 4, end: () => 40 } as unknown as { length: number }
    video.currentTime = 12
    internals._frozenSinceMs = Date.now() - 60_000
    internals._lastBufferEndTime = Date.now() // 缓冲仍在增长 ⇒ 渲染冻结，不是断流

    const warns: string[] = []
    const spy = vi.spyOn(console, 'warn').mockImplementation((...args: unknown[]) => {
      warns.push(String(args[0]))
    })

    internals._warnFrozenProgression()
    expect(warns).toHaveLength(1)
    expect(warns[0]).toContain('[MsePlayer] WARN: clock frozen')
    expect(warns[0]).toContain('mode=render-frozen')
    const total = Number((warns[0].match(/total=([\d.]+)s/) ?? [])[1])
    expect(total).toBeGreaterThanOrEqual(59)

    // 500ms tick 下不得刷屏
    internals._warnFrozenProgression()
    expect(warns).toHaveLength(1)

    // 缓冲 8s 不增长 ⇒ 分型为数据饥饿
    internals._lastBufferEndTime = Date.now() - 30_000
    internals._lastFrozenWarnAtMs = 0
    internals._warnFrozenProgression()
    expect(warns).toHaveLength(2)
    expect(warns[1]).toContain('mode=data-starved')
    spy.mockRestore()
  })
})

describe('恢复阶梯最后一层：有界请求重建后端 sink（CLAUDE.md §7.2.2 遗留项）', () => {
  function makePlayer(onRequestSinkRestart?: (reason: string) => boolean, isFile = false) {
    return new MsePlayer({
      videoElement: fakeVideo() as unknown as HTMLVideoElement,
      isFile,
      onRequestSinkRestart,
    })
  }

  it('回调接单时返回 true 且不再进入 error 终态（等后端重建）', () => {
    const reasons: string[] = []
    const errors: string[] = []
    const player = new MsePlayer({
      videoElement: fakeVideo() as unknown as HTMLVideoElement,
      onRequestSinkRestart: (reason) => {
        reasons.push(reason)
        return true
      },
      onError: (msg) => errors.push(msg),
    })
    const internals = player as unknown as { _trySinkRestart: (r: string) => boolean }

    expect(internals._trySinkRestart('stall recovery exhausted')).toBe(true)
    expect(reasons).toEqual(['stall recovery exhausted'])
    expect(errors).toEqual([])
    expect(player.state).toBe('loading')
  })

  it('每个播放器实例最多请求一次（后端重建会换 epoch 并重建播放器）', () => {
    let calls = 0
    const player = makePlayer(() => {
      calls += 1
      return true
    })
    const internals = player as unknown as { _trySinkRestart: (r: string) => boolean }

    expect(internals._trySinkRestart('stall recovery exhausted')).toBe(true)
    expect(internals._trySinkRestart('forced seek recovery exhausted')).toBe(false)
    expect(calls).toBe(1)
  })

  it('房间级预算拒绝时返回 false，交由调用方按原口径报错', () => {
    const player = makePlayer(() => false)
    const internals = player as unknown as { _trySinkRestart: (r: string) => boolean }

    expect(internals._trySinkRestart('stall recovery exhausted')).toBe(false)
  })

  it('未接线（无回调）时不得吞掉终态错误', () => {
    const player = makePlayer(undefined)
    const internals = player as unknown as { _trySinkRestart: (r: string) => boolean }

    expect(internals._trySinkRestart('stall recovery exhausted')).toBe(false)
  })

  it('文件回看通道没有后端 sink：不问回调', () => {
    let calls = 0
    const player = makePlayer(() => {
      calls += 1
      return true
    }, true)
    const internals = player as unknown as { _trySinkRestart: (r: string) => boolean }

    expect(internals._trySinkRestart('stall recovery exhausted')).toBe(false)
    expect(calls).toBe(0)
  })

  it('回调抛异常按「不接单」处理，不得让异常穿透恢复路径', () => {
    const player = makePlayer(() => {
      throw new Error('boom')
    })
    const internals = player as unknown as { _trySinkRestart: (r: string) => boolean }

    expect(internals._trySinkRestart('stall recovery exhausted')).toBe(false)
  })
})

/**
 * 以下用例把 `tests/test_frontend_stability_guards.py` 里 6 条**只做源码子串匹配**的
 * 守卫换成对真实行为的可执行断言（审计 §5：141 条文子守卫里 137 条零真实断言 ——
 * 改个变量名就红、逻辑写反了却绿）。每条都做过负控：把被测行为改坏，用例必须红。
 */
function videoWithBuffer(start: number, end: number) {
  const v = fakeVideo()
  v.buffered = {
    length: 1,
    start: () => start,
    end: () => end,
  } as unknown as { length: number }
  return v
}

type PlayerInternals = {
  _state: string
  _liveEdgeAligned: boolean
  _playExhausted: boolean
  _forcedSeekRecoveryCount: number
  _lastSeekTime: number
  _lastStallPosition: number
  _lastStallTime: number
  _abortController: AbortController | null
  _setState: (s: string) => void
  _startStallDetection: () => void
}

function internals(player: MsePlayer): PlayerInternals {
  return player as unknown as PlayerInternals
}

/** play() 永不 settle —— 这才是 "media clock 冻结、play() 的 Promise 不回来" 的形状。 */
function hangingPlayVideo(start: number, end: number) {
  const video = videoWithBuffer(start, end)
  video.play = vi.fn(() => new Promise<never>(() => {})) as unknown as typeof video.play
  return video
}

describe('MsePlayer goLive / 缓冲区间 / 停滞强扫（替换文子守卫）', () => {
  it('goLive：有缓冲时播放头落到 live 沿（缓冲末端前 0.3s），并撤掉对齐标记', () => {
    const video = videoWithBuffer(100, 120)
    video.currentTime = 105
    const player = new MsePlayer({ videoElement: video as unknown as HTMLVideoElement })
    const it = internals(player)
    it._setState('playing')
    it._liveEdgeAligned = true

    player.goLive()

    expect(video.currentTime).toBeCloseTo(119.7, 6)
    expect(it._liveEdgeAligned).toBe(false)
  })

  it('goLive：缓冲为空时绝不写 currentTime（写了就是往不存在的区间 seek）', async () => {
    vi.useFakeTimers()
    try {
      const video = fakeVideo()
      video.currentTime = 77
      const player = new MsePlayer({ videoElement: video as unknown as HTMLVideoElement })
      internals(player)._setState('playing')

      player.goLive()
      // 落点不动，但仍要试着播等新段到达 —— _tryPlay 是 50ms 后的延时
      await vi.advanceTimersByTimeAsync(60)

      expect(video.currentTime).toBe(77)
      expect(video.play).toHaveBeenCalled()
    } finally {
      vi.useRealTimers()
    }
  })

  it('goLive：idle 状态直接返回，不碰播放头也不发起播放', () => {
    const video = videoWithBuffer(0, 50)
    video.currentTime = 1
    const player = new MsePlayer({ videoElement: video as unknown as HTMLVideoElement })

    player.goLive()

    expect(video.currentTime).toBe(1)
    expect(video.play).not.toHaveBeenCalled()
  })

  it('getBufferedRanges 只保留 end>start 的真实区间；getBufferedRange 折叠首尾', () => {
    const video = fakeVideo()
    const starts = [10, 30, 60]
    const ends = [20, 30, 75] // 第二段 end===start：退化区间，不该进结果
    video.buffered = {
      length: 3,
      start: (i: number) => starts[i],
      end: (i: number) => ends[i],
    } as unknown as { length: number }
    const player = new MsePlayer({ videoElement: video as unknown as HTMLVideoElement })

    expect(player.getBufferedRanges()).toEqual([
      { start: 10, end: 20 },
      { start: 60, end: 75 },
    ])
    expect(player.getBufferedRange()).toEqual({ start: 10, end: 75 })
  })

  it('getBufferedRange：无缓冲返回 null（调用方据此区分"没有数据"与"0 秒"）', () => {
    const player = new MsePlayer({ videoElement: fakeVideo() as unknown as HTMLVideoElement })
    expect(player.getBufferedRange()).toBeNull()
  })

  it('_tryPlay：play() 持续失败要置 _playExhausted，成功一次就清掉', async () => {
    vi.useFakeTimers()
    try {
      const video = fakeVideo()
      video.play = vi.fn(async (): Promise<void> => {
        throw new Error('not allowed')
      })
      const player = new MsePlayer({ videoElement: video as unknown as HTMLVideoElement })
      const it = internals(player)
      it._setState('playing')
      player.goLive()
      for (let i = 0; i < 10; i += 1) await vi.advanceTimersByTimeAsync(600)
      expect(it._playExhausted).toBe(true)

      video.play = vi.fn(async () => {})
      it._setState('playing')
      player.goLive()
      for (let i = 0; i < 4; i += 1) await vi.advanceTimersByTimeAsync(600)
      expect(it._playExhausted).toBe(false)
    } finally {
      vi.useRealTimers()
    }
  })

  it('停滞检测：播放头出界且 media clock 冻结时强制 seek 回 live 沿，并递增独立预算', async () => {
    vi.useFakeTimers()
    try {
      const video = hangingPlayVideo(100, 120)
      // 落在 live 沿之后（> bufEnd-0.3）⇒ 目标 = max(bufStart, bufEnd-0.3)
      video.currentTime = 119.9
      const player = new MsePlayer({ videoElement: video as unknown as HTMLVideoElement })
      const it = internals(player)
      it._setState('playing')
      it._playExhausted = true
      it._lastSeekTime = 0
      it._lastStallPosition = 119.9
      it._lastStallTime = 0
      it._forcedSeekRecoveryCount = 0
      it._startStallDetection()

      // 第一 tick 只记录停滞起点；持续满 1.5s 才动手
      await vi.advanceTimersByTimeAsync(1000)
      expect(video.currentTime).toBe(119.9)
      expect(it._forcedSeekRecoveryCount).toBe(0)
      await vi.advanceTimersByTimeAsync(1000)
      expect(video.currentTime).toBeCloseTo(119.7, 6)
      expect(it._forcedSeekRecoveryCount).toBe(1)
      // 强扫后要撤掉对齐标记、清掉耗尽标志，等下一次 play 结果
      expect(it._liveEdgeAligned).toBe(false)
      expect(it._playExhausted).toBe(false)
    } finally {
      vi.useRealTimers()
    }
  })

  /**
   * 播放头**在**缓冲区间内（100–119.7）却冻着不动，是 media clock 冻结的唯一残形：
   * 出界三个条件全为假，只有 `_playExhausted` 能触发强扫。
   * 负控确认：把 `|| this._playExhausted` 去掉后本用例变红，而原出界用例仍绿。
   */
  it('停滞检测：播放头在缓冲区间内、media clock 冻结时也必须靠 _playExhausted 强扫', async () => {
    vi.useFakeTimers()
    try {
      const video = hangingPlayVideo(100, 120)
      video.currentTime = 110
      const player = new MsePlayer({ videoElement: video as unknown as HTMLVideoElement })
      const it = internals(player)
      it._setState('playing')
      it._lastSeekTime = 0
      it._lastStallPosition = 110
      it._lastStallTime = 0
      it._forcedSeekRecoveryCount = 0
      it._playExhausted = true
      it._startStallDetection()

      await vi.advanceTimersByTimeAsync(2000)
      // 回看态（离 live 沿 >3s）保持当前 range 内位置，不跳回 live 沿
      expect(it._forcedSeekRecoveryCount).toBe(1)
      expect(it._playExhausted).toBe(false)

      // 反证：同样的几何位置、没有 _playExhausted，就不该强扫（只重新触发 play）
      const idleVideo = hangingPlayVideo(100, 120)
      idleVideo.currentTime = 110
      const idlePlayer = new MsePlayer({ videoElement: idleVideo as unknown as HTMLVideoElement })
      const idle = internals(idlePlayer)
      idle._setState('playing')
      idle._lastSeekTime = 0
      idle._lastStallPosition = 110
      idle._lastStallTime = 0
      idle._forcedSeekRecoveryCount = 0
      idle._playExhausted = false
      idle._startStallDetection()
      await vi.advanceTimersByTimeAsync(2000)
      expect(idle._forcedSeekRecoveryCount).toBe(0)
      expect(idleVideo.currentTime).toBe(110)
    } finally {
      vi.useRealTimers()
    }
  })

  it('停滞检测：播放头确实前进后必须清零强扫预算（否则正常抖动会耗光预算提前报错）', async () => {
    vi.useFakeTimers()
    try {
      const video = hangingPlayVideo(100, 120)
      video.currentTime = 119.9
      const player = new MsePlayer({ videoElement: video as unknown as HTMLVideoElement })
      const it = internals(player)
      it._setState('playing')
      it._playExhausted = true
      it._lastSeekTime = 0
      it._lastStallPosition = 119.9
      it._lastStallTime = 0
      it._forcedSeekRecoveryCount = 0
      it._startStallDetection()

      await vi.advanceTimersByTimeAsync(1000)
      await vi.advanceTimersByTimeAsync(1000)
      expect(it._forcedSeekRecoveryCount).toBe(1)

      // media clock 恢复：播放头相对上次记录点位移 > 0.1s 即算"真的在播"。
      // 进度判定在停滞判定**之前**并直接 return，所以预算必须在这里被清零。
      video.currentTime = 119.0
      await vi.advanceTimersByTimeAsync(600)
      expect(it._forcedSeekRecoveryCount).toBe(0)
      expect(it._playExhausted).toBe(false)
      expect(it._lastStallPosition).toBe(119.0)
    } finally {
      vi.useRealTimers()
    }
  })

  it('stop() 必须 abort 并清空 AbortController（否则旧会话的异步回调还在落地）', () => {
    const player = new MsePlayer({ videoElement: fakeVideo() as unknown as HTMLVideoElement })
    const it = internals(player)
    let aborted = 0
    it._abortController = {
      signal: { aborted: false },
      abort: () => {
        aborted += 1
      },
    } as unknown as AbortController

    player.stop()

    expect(aborted).toBe(1)
    expect(it._abortController).toBeNull()
  })
})
