const _pendingCounts = new Map<string, number>()
const _pendingTimers = new Map<string, ReturnType<typeof setTimeout>>()
const _pendingMeta = new Map<string, Record<string, unknown>>()
const _pendingFlush = new Map<string, (count: number, meta: Record<string, unknown>) => void>()

/** 取出并清空某 key 的挂起状态，返回待 flush 的数据（无挂起时返回 null）。 */
function _drain(key: string): { count: number; meta: Record<string, unknown> } | null {
  const timer = _pendingTimers.get(key)
  if (timer) clearTimeout(timer)
  _pendingTimers.delete(key)
  const count = _pendingCounts.get(key) ?? 0
  const meta = _pendingMeta.get(key) ?? {}
  _pendingCounts.delete(key)
  _pendingMeta.delete(key)
  return count > 0 ? { count, meta } : null
}

/**
 * 合并短时间内的同类 toast，避免 clip_queued / continuous_highlights 刷屏。
 */
export function scheduleBatchedToast(
  key: string,
  onFlush: (count: number, meta: Record<string, unknown>) => void,
  delayMs = 800,
  meta?: Record<string, unknown>,
): void {
  _pendingCounts.set(key, (_pendingCounts.get(key) ?? 0) + 1)
  _pendingFlush.set(key, onFlush)
  if (meta) {
    const prev = _pendingMeta.get(key) ?? {}
    _pendingMeta.set(key, { ...prev, ...meta })
  }
  const existing = _pendingTimers.get(key)
  if (existing) clearTimeout(existing)
  _pendingTimers.set(
    key,
    setTimeout(() => {
      const drained = _drain(key)
      if (drained) onFlush(drained.count, drained.meta)
    }, delayMs),
  )
}

/** 立即 flush 某 key 的挂起 toast（跳过防抖等待）。 */
export function flushBatchedToast(key: string): void {
  const onFlush = _pendingFlush.get(key)
  _pendingFlush.delete(key)
  const drained = _drain(key)
  if (drained && onFlush) onFlush(drained.count, drained.meta)
}
