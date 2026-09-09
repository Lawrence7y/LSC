import { describe, expect, it } from 'vitest'
import {
  DEFAULT_TIMELINE_REPLAY_SECONDS,
  effectivePlaybackBufferSeconds,
  MIN_PLAYBACK_BUFFER_SECONDS,
  normalizeReplayBufferSeconds,
} from './replaySettings'

describe('replay settings', () => {
  it('defaults to five minutes and accepts the supported options', () => {
    expect(DEFAULT_TIMELINE_REPLAY_SECONDS).toBe(300)
    expect(normalizeReplayBufferSeconds(0)).toBe(0)
    expect(normalizeReplayBufferSeconds(120)).toBe(120)
    expect(normalizeReplayBufferSeconds(300)).toBe(300)
    expect(normalizeReplayBufferSeconds(600)).toBe(600)
  })

  it('falls back to the safe default for invalid values', () => {
    expect(normalizeReplayBufferSeconds(undefined)).toBe(300)
    expect(normalizeReplayBufferSeconds(null)).toBe(300)
    expect(normalizeReplayBufferSeconds(240)).toBe(300)
    expect(normalizeReplayBufferSeconds(300.5)).toBe(300)
    expect(normalizeReplayBufferSeconds('not-a-duration')).toBe(300)
  })

  it('keeps a short playback buffer when DVR is disabled', () => {
    expect(effectivePlaybackBufferSeconds(0)).toBe(MIN_PLAYBACK_BUFFER_SECONDS)
    expect(effectivePlaybackBufferSeconds(300)).toBe(300)
  })
})
