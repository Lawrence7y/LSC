/// <reference lib="webworker" />
/**
 * 时间线视图 Worker：主线程 postMessage(TimelineViewInput) → 回传 TimelineViewResult。
 * base64 已改二进制帧，Worker 专注批次时间线计算，解放主线程。
 */
import { computeTimelineViewModel, type TimelineViewInput } from '../utils/timelineViewModel'

const ctx: DedicatedWorkerGlobalScope = self as unknown as DedicatedWorkerGlobalScope

ctx.onmessage = (event: MessageEvent<TimelineViewInput>) => {
  try {
    const result = computeTimelineViewModel(event.data)
    ctx.postMessage({ ok: true, result })
  } catch (error) {
    ctx.postMessage({
      ok: false,
      error: error instanceof Error ? error.message : String(error),
    })
  }
}
