const _pendingCounts = new Map<string, number>()
const _pendingTimers = new Map<string, ReturnType<typeof setTimeout>>()
const _pendingMeta = new Map<string, Record<string, unknown>>()

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
  if (meta) {
    const prev = _pendingMeta.get(key) ?? {}
    _pendingMeta.set(key, { ...prev, ...meta })
  }
  const existing = _pendingTimers.get(key)
  if (existing) clearTimeout(existing)
  _pendingTimers.set(
    key,
    setTimeout(() => {
      const count = _pendingCounts.get(key) ?? 0
      const mergedMeta = _pendingMeta.get(key) ?? {}
      _pendingCounts.delete(key)
      _pendingTimers.delete(key)
      _pendingMeta.delete(key)
      if (count > 0) onFlush(count, mergedMeta)
    }, delayMs),
  )
}

export function flushBatchedToast(key: string): void {
  const timer = _pendingTimers.get(key)
  if (timer) {
    clearTimeout(timer)
    _pendingTimers.delete(key)
    const count = _pendingCounts.get(key) ?? 0
    const meta = _pendingMeta.get(key) ?? {}
    _pendingCounts.delete(key)
    _pendingMeta.delete(key)
    if (count > 0) {
      // no-op flush helper for tests; callers normally rely on timer
      void count
      void meta
    }
  }
}
