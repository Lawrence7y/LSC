/**
 * 预览自愈（后端 sink 重建）的房间级预算。
 *
 * 背景：播放器恢复阶梯耗尽后，可以向 UI 层请求「重开后端预览 sink」（enable_preview
 * {restart:true}）。后端重建会换 preview_epoch_id → 前端重建播放器 → 播放器实例内的计数
 * 归零；若只按实例计数，「重建后再次卡死」就能无限重启后端 FFmpeg（每次都要重连 CDN 并起
 * 新转码进程）。因此预算按房间记在模块级：窗口内最多 SINK_RESTART_MAX_PER_WINDOW 次。
 */

export const SINK_RESTART_WINDOW_MS = 10 * 60 * 1000
export const SINK_RESTART_MAX_PER_WINDOW = 2

const _attempts = new Map<string, number[]>()

function _prune(roomId: string, now: number): number[] {
  const kept = (_attempts.get(roomId) || []).filter((ts) => now - ts < SINK_RESTART_WINDOW_MS)
  if (kept.length) _attempts.set(roomId, kept)
  else _attempts.delete(roomId)
  return kept
}

/** 是否还允许为本房间请求一次后端 sink 重建（只读，不消耗预算）。 */
export function shouldRequestSinkRestart(roomId: string, now = Date.now()): boolean {
  return _prune(roomId, now).length < SINK_RESTART_MAX_PER_WINDOW
}

/** 记一次已发出的重建请求（调用方在 send 成功后再调用）。 */
export function noteSinkRestart(roomId: string, now = Date.now()): void {
  const kept = _prune(roomId, now)
  kept.push(now)
  _attempts.set(roomId, kept)
}

/** 测试与房间移除时清零，避免长跑会话里 Map 无界增长。 */
export function clearSinkRestartBudget(roomId?: string): void {
  if (roomId === undefined) _attempts.clear()
  else _attempts.delete(roomId)
}
