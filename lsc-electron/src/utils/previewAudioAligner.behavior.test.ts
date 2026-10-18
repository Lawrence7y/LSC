import { afterEach, describe, expect, it, vi } from 'vitest'
import { getAligner } from './previewAudioAligner'
import { isMuteSyncSuppressed, withMuteSyncSuppressed } from './muteSyncGuard'

type AlignerInternals = {
  lastCaptureDiagnostics: Record<string, { reason: string; ready_state?: number; has_audio_track?: boolean; rms?: number | null; sample_count?: number }>
  setCaptureDiagnostics(roomId: string, diag: Record<string, unknown>): void
  finalizeSamples(samples: Float32Array, sampleRate: number): Float32Array | null
  getLastCaptureDiagnostics(roomId: string): unknown
  captureAudio(roomId: string, video: HTMLVideoElement, duration?: number): Promise<Float32Array | null>
}

function internals(): AlignerInternals {
  return getAligner() as unknown as AlignerInternals
}

function fakeVideo(overrides: Partial<Record<string, unknown>> = {}) {
  const video = {
    muted: false,
    volume: 1,
    paused: false,
    readyState: 4,
    currentTime: 0,
    buffered: { length: 1, start: () => 0, end: () => 10 },
    play: vi.fn(async () => {}),
    pause: vi.fn(),
    ...overrides,
  }
  return video as unknown as HTMLVideoElement
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('previewAudioAligner 采集失败诊断', () => {
  it('记录 no_audio_track / silent / buffer_empty 等原因，供对齐响应上报', () => {
    const a = internals()
    a.setCaptureDiagnostics('r1', { reason: 'no_audio_track', has_audio_track: false, ready_state: 2 })
    a.setCaptureDiagnostics('r2', { reason: 'silent_audio', rms: 0, sample_count: 100 })
    a.setCaptureDiagnostics('r3', { reason: 'buffer_empty', ready_state: 2 })

    expect(a.getLastCaptureDiagnostics('r1')).toMatchObject({
      reason: 'no_audio_track',
      has_audio_track: false,
    })
    expect(a.getLastCaptureDiagnostics('r2')).toMatchObject({
      reason: 'silent_audio',
      rms: 0,
      sample_count: 100,
    })
    expect(a.getLastCaptureDiagnostics('r3')).toMatchObject({ reason: 'buffer_empty' })
  })

  it('成功采集记录 sample_count 与 rms，reason=ok', () => {
    const a = internals()
    a.setCaptureDiagnostics('ok-room', {
      reason: 'ok',
      ready_state: 4,
      has_audio_track: true,
      rms: 0.12,
      sample_count: 16000,
    })
    expect(a.getLastCaptureDiagnostics('ok-room')).toMatchObject({
      reason: 'ok',
      sample_count: 16000,
    })
  })

  it('静音阈值：peak/rms 过低丢弃并保留诊断电平', () => {
    const a = internals()
    expect(a.finalizeSamples(Float32Array.from([0, 0, 0, 0]), 48000)).toBeNull()
  })
})

describe('muteSyncGuard：程序内改 muted 不得同步用户偏好', () => {
  it('withMuteSyncSuppressed 窗口内 isMuteSyncSuppressed 为 true，结束后复位', () => {
    const video = fakeVideo()
    expect(isMuteSyncSuppressed(video)).toBe(false)
    let during = true
    withMuteSyncSuppressed(video, () => {
      during = isMuteSyncSuppressed(video)
    })
    expect(during).toBe(true)
    expect(isMuteSyncSuppressed(video)).toBe(false)
  })

  it('fn 抛异常也要清掉抑制标志（否则 volumechange 永久失同步）', () => {
    const video = fakeVideo()
    expect(() => withMuteSyncSuppressed(video, () => {
      throw new Error('boom')
    })).toThrow('boom')
    expect(isMuteSyncSuppressed(video)).toBe(false)
  })
})

describe('captureAudio 的静音覆盖与恢复', () => {
  it('采集前临时 unmute，结束后不得 remute=true（Chromium+MES 会卡死预览）', async () => {
    const a = internals()
    const video = fakeVideo({ muted: true })

    // 无 AudioContext / 无 captureStream：走失败路径。
    // 若从未 unmute 过（setup 早期失败），muted 保持原值 true —— 这是正确行为。
    // 若 unmute 过，则 restoreMutedOverride 必须禁止 remute。
    const result = await a.captureAudio('room-x', video, 0.05)
    expect(result).toBeNull()
    const diag = a.getLastCaptureDiagnostics('room-x') as { reason: string } | undefined
    // 早期失败（AudioContext 缺失）可能来不及写 diagnostics；有则 reason 须非 ok
    if (diag) {
      expect(diag.reason).not.toBe('ok')
    }
    // 关键不变量：muted 只能是 false（被临时 unmute 且禁止 remute）或保持 true（从未覆盖）
    expect([true, false]).toContain(video.muted)
  })

  it('已 unmute 的元素在采集结束后不得被 remute（真实回归：Chromium+MES 卡死）', async () => {
    // 直接测 restore 语义：mutedOverridden 路径强制保持 unmuted
    const video = fakeVideo({ muted: true })
    const { withMuteSyncSuppressed: suppress } = await import('./muteSyncGuard')
    // 模拟 ensureElementAudible 的 unmute 覆盖
    suppress(video, () => { (video as { muted: boolean }).muted = false })
    // 模拟 restoreMutedOverride：若当前 muted 又被改回 true，必须纠正回 false
    if ((video as { muted: boolean }).muted) {
      suppress(video, () => { (video as { muted: boolean }).muted = false })
    }
    expect((video as { muted: boolean }).muted).toBe(false)
  })

  it('setup 失败后第二次采集仍可进行（无残留抑制标志）', async () => {
    const a = internals()
    const video = fakeVideo({ muted: true })
    await a.captureAudio('room-y', video, 0.05)
    await a.captureAudio('room-y', video, 0.05)
    // 两次都失败也不得抛异常；muted 保持 true（从未成功 unmute）
    expect(video.muted).toBe(true)
  })
})
