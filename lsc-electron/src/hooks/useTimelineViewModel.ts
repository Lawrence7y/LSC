import { useEffect, useMemo, useRef, useState } from 'react'
import type { TimelineViewModel } from '@/pages/Workbench/components/ControlBar'
import {
  buildClipBlocks,
  computeTimelineViewModel,
  type TimelineViewInput,
  type TimelineViewResult,
} from '@/utils/timelineViewModel'

const WORKER_CLIP_THRESHOLD = 20

/**
 * 切片较多时把时间线视图计算丢到 Worker；否则同步计算（避免小数据 Worker 开销）。
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
  const useWorker = input.clips.length >= WORKER_CLIP_THRESHOLD

  const clipBlocks = useMemo(() => {
    if (!input.commonMode || !input.timelineContext) return []
    return buildClipBlocks(input.clips, input.timelineContext)
  }, [input.commonMode, input.timelineContext, input.clips])

  useEffect(() => {
    if (!useWorker) {
      workerRef.current?.terminate()
      workerRef.current = null
      return
    }
    if (!workerRef.current) {
      workerRef.current = new Worker(
        new URL('../workers/timelineView.worker.ts', import.meta.url),
        { type: 'module' },
      )
      workerRef.current.onmessage = (event: MessageEvent<{ ok: boolean; result?: TimelineViewResult; error?: string }>) => {
        if (!event.data?.ok || !event.data.result) return
        const { view, contentEnd, windowStart, contentEdgeRoomId } = event.data.result
        lastContentEndRef.current = contentEnd
        lastWindowStartRef.current = windowStart
        contentEdgeRoomRef.current = contentEdgeRoomId
        setAsyncView(view)
      }
    }
    const payload: TimelineViewInput = {
      ...input,
      clipBlocks,
      prevContentEnd: lastContentEndRef.current,
      prevWindowStart: lastWindowStartRef.current,
      contentEdgeRoomId: contentEdgeRoomRef.current,
    }
    workerRef.current.postMessage(payload)
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
  ])

  useEffect(() => () => {
    workerRef.current?.terminate()
    workerRef.current = null
  }, [])

  const syncView = useMemo(() => {
    if (useWorker) return null
    const result = computeTimelineViewModel({
      ...input,
      clipBlocks,
      prevContentEnd: lastContentEndRef.current,
      prevWindowStart: lastWindowStartRef.current,
      contentEdgeRoomId: contentEdgeRoomRef.current,
    })
    lastContentEndRef.current = result.contentEnd
    lastWindowStartRef.current = result.windowStart
    contentEdgeRoomRef.current = result.contentEdgeRoomId
    return result.view
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
  ])

  return useWorker ? asyncView : syncView
}
