import { useEffect, useMemo, useRef, useState } from 'react'
import type { TimelineViewModel } from '@/pages/Workbench/components/ControlBar'
import {
  buildClipBlocks,
  computeTimelineViewModel,
  type TimelineViewInput,
  type TimelineViewResult,
} from '@/utils/timelineViewModel'

// Worker 收益阈值：computeTimelineViewModel 是 O(n) 纯算术，而每次 postMessage
// 都要 structured-clone 整个 input（rooms/clips/timelineContext/waveformPeaks），
// 低切片数下克隆+异步往返成本高于计算本身还白引入一帧延迟。
// 仅在切片量级真正大（>=500）时才走 Worker。
const WORKER_CLIP_THRESHOLD = 500

type WorkerResponse =
  | { ok: true; seq: number; result: TimelineViewResult }
  | { ok: false; seq: number; error: string }

/**
 * 切片量级大时把时间线视图计算丢到 Worker；否则同步计算（避免小数据 Worker 开销）。
 * clipBlocks 独立 memo：previewPositions 高频变化时不重跑 O(n) 坐标转换。
 */
export function useTimelineViewModel(
  input: Omit<TimelineViewInput, 'prevContentEnd' | 'prevWindowStart' | 'contentEdgeRoomId' | 'clipBlocks'> & {
    /** 触发重算的 tick（录制时长刷新） */
    timelineTick?: number
  },
): TimelineViewModel | null {
  const lastContentEndRef = useRef(1)
  const lastWindowStartRef = useRef(0)
  const contentEdgeRoomRef = useRef<string | null>(null)
  const [asyncView, setAsyncView] = useState<TimelineViewModel | null>(null)
  const workerRef = useRef<Worker | null>(null)
  // 请求序号：高频 postMessage 时只采用最新响应，丢弃中间过期结果
  const seqRef = useRef(0)
  const useWorker = input.clips.length >= WORKER_CLIP_THRESHOLD

  const clipBlocks = useMemo(() => {
    if (!input.commonMode || !input.timelineContext) return []
    return buildClipBlocks(input.clips, input.timelineContext)
  }, [input.commonMode, input.timelineContext, input.clips])

  /** 同步计算（sync 路径主用 / worker 失败时兜底），返回完整 result 供调用方写 ref */
  const computeSync = (): TimelineViewResult =>
    computeTimelineViewModel({
      ...input,
      clipBlocks,
      prevContentEnd: lastContentEndRef.current,
      prevWindowStart: lastWindowStartRef.current,
      contentEdgeRoomId: contentEdgeRoomRef.current,
    })

  const computeSyncRef = useRef(computeSync)
  computeSyncRef.current = computeSync

  useEffect(() => {
    if (!useWorker) {
      workerRef.current?.terminate()
      workerRef.current = null
      return
    }
    if (!workerRef.current) {
      const worker = new Worker(
        new URL('../workers/timelineView.worker.ts', import.meta.url),
        { type: 'module' },
      )
      worker.onmessage = (event: MessageEvent<WorkerResponse>) => {
        const msg = event.data
        if (!msg) return
        // 过期响应：已有更新的请求发出，丢弃避免多余渲染与乱序覆盖
        if (msg.seq !== seqRef.current) return
        if (!msg.ok) {
          // worker 计算失败：回退主线程同步计算，避免视图永久停留在旧值
          console.error('[useTimelineViewModel] worker compute failed, fallback to sync:', msg.error)
          applyResult(computeSyncRef.current())
          return
        }
        applyResult(msg.result)
      }
      worker.onerror = (event) => {
        console.error('[useTimelineViewModel] worker error, fallback to sync:', event.message)
        applyResult(computeSyncRef.current())
      }
      workerRef.current = worker
    }
    const applyResult = (result: TimelineViewResult) => {
      lastContentEndRef.current = result.contentEnd
      lastWindowStartRef.current = result.windowStart
      contentEdgeRoomRef.current = result.contentEdgeRoomId
      setAsyncView(result.view)
    }
    const seq = ++seqRef.current
    const payload: TimelineViewInput = {
      ...input,
      clipBlocks,
      prevContentEnd: lastContentEndRef.current,
      prevWindowStart: lastWindowStartRef.current,
      contentEdgeRoomId: contentEdgeRoomRef.current,
    }
    workerRef.current.postMessage({ seq, input: payload })
  }, [
    useWorker,
    clipBlocks,
    input.commonMode,
    input.timelineContext,
    input.referenceRoomId,
    input.rooms,
    input.previewPositions,
    input.commonMarkIn,
    input.commonMarkOut,
    input.clips,
    input.timelineHighlights,
    input.refiningClipId,
    input.waveformPeaks,
    input.timelineFollowLive,
    input.timelineScrubbing,
    input.frozenWindowStart,
    input.recordedDurationHint,
    input.mediaDuration,
    input.timelineTick,
    input.zoomLevel,
  ])

  useEffect(() => () => {
    workerRef.current?.terminate()
    workerRef.current = null
  }, [])

  // sync 路径：纯计算放 useMemo（不写 ref），result 提交后由 effect 写 ref，
  // 避免渲染期副作用（StrictMode 双渲染/并发渲染下 ref 与提交值不一致）。
  const syncResult = useMemo(() => {
    if (useWorker) return null
    return computeSync()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    useWorker,
    clipBlocks,
    input.commonMode,
    input.timelineContext,
    input.referenceRoomId,
    input.rooms,
    input.previewPositions,
    input.commonMarkIn,
    input.commonMarkOut,
    input.clips,
    input.timelineHighlights,
    input.refiningClipId,
    input.waveformPeaks,
    input.timelineFollowLive,
    input.timelineScrubbing,
    input.frozenWindowStart,
    input.recordedDurationHint,
    input.mediaDuration,
    input.timelineTick,
    input.zoomLevel,
  ])

  useEffect(() => {
    if (!syncResult) return
    lastContentEndRef.current = syncResult.contentEnd
    lastWindowStartRef.current = syncResult.windowStart
    contentEdgeRoomRef.current = syncResult.contentEdgeRoomId
  }, [syncResult])

  return useWorker ? asyncView : (syncResult?.view ?? null)
}
