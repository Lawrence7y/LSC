/**
 * Timeline replay settings shared by the settings page, timeline and MSE player.
 *
 * A zero value disables user-visible DVR history but keeps a small playback
 * safety buffer so the live preview does not become fragile.
 */
export const REPLAY_BUFFER_OPTIONS = [0, 120, 300, 600] as const
export type ReplayBufferSeconds = typeof REPLAY_BUFFER_OPTIONS[number]

export const DEFAULT_TIMELINE_REPLAY_SECONDS = 300
export const MIN_PLAYBACK_BUFFER_SECONDS = 15
export const REPLAY_TRIM_HEADROOM_SECONDS = 20

export function normalizeReplayBufferSeconds(value: unknown): ReplayBufferSeconds {
  if (value === null || value === undefined || value === '' || typeof value === 'boolean') {
    return DEFAULT_TIMELINE_REPLAY_SECONDS
  }
  const seconds = typeof value === 'number' ? value : Number(value)
  if (!Number.isInteger(seconds)) return DEFAULT_TIMELINE_REPLAY_SECONDS
  const normalized = seconds
  return (REPLAY_BUFFER_OPTIONS as readonly number[]).includes(normalized)
    ? normalized as ReplayBufferSeconds
    : DEFAULT_TIMELINE_REPLAY_SECONDS
}

/** Actual MSE retention. Disabled DVR still needs a short buffer for playback. */
export function effectivePlaybackBufferSeconds(value: unknown): number {
  const seconds = normalizeReplayBufferSeconds(value)
  return seconds > 0 ? seconds : MIN_PLAYBACK_BUFFER_SECONDS
}
