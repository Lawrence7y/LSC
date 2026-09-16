/**
 * Timeline replay settings shared by the settings page, timeline and MSE player.
 *
 * A zero value disables user-visible DVR history but keeps a small playback
 * safety buffer so the live preview does not become fragile.
 */
export const REPLAY_BUFFER_OPTIONS = [0, 120, 300, 600] as const
export type ReplayBufferSeconds = typeof REPLAY_BUFFER_OPTIONS[number]

export const DEFAULT_TIMELINE_REPLAY_SECONDS = 300
/**
 * 播放缓冲硬下限（秒）。
 *
 * 同时是「关闭回放」档的安全缓冲与 MSE trim 的最低保留量——两处必须同源：
 * 旧实现前者 15s、后者 `Math.max(30, …)`，于是"关闭回放"实际保留 30s 而
 * 常量写着 15s（2026-09-15 排查发现的口径不一致）。
 */
export const MIN_PLAYBACK_BUFFER_SECONDS = 30
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
