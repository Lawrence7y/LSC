/// <reference lib="webworker" />
/**
 * 时间线视图 Worker：主线程 postMessage({ seq, input }) → 回传 { ok, seq, result }。
 * seq 为请求序号：主线程只采用最新 seq 的响应，丢弃中间过期结果。
 */
import { computeTimelineViewModel, type TimelineViewInput } from '../utils/timelineViewModel'

const ctx: DedicatedWorkerGlobalScope = self as unknown as DedicatedWorkerGlobalScope

ctx.onmessage = (event: MessageEvent<{ seq: number; input: TimelineViewInput }>) => {
  const { seq, input } = event.data
  try {
    const result = computeTimelineViewModel(input)
    ctx.postMessage({ ok: true, seq, result })
  } catch (error) {
    ctx.postMessage({
      ok: false,
      seq,
      error: error instanceof Error ? error.message : String(error),
    })
  }
}
