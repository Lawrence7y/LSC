import { useEffect, type Dispatch, type MutableRefObject, type SetStateAction } from 'react'
import { useAppStore } from '@/store/appStore'
import { getAlignStatus, pickReferenceRoomId, previewToCommon } from '@/utils/timelineCoords'
import { writeDisplayPlayhead, writePlayhead } from '@/utils/playheadStore'

/**
 * 预览播放头采样：200ms 读 MSE currentTime → playheadStore 直写；
 * setPreviewPositions 降频 500ms，仅驱动 contentEnd 等低频逻辑。
 */
export function usePlayheadSampling(opts: {
  setPreviewPositions: Dispatch<SetStateAction<Record<string, number>>>
  lastPreviewPositionsRef: MutableRefObject<Record<string, number>>
  scrubOverrideRef: MutableRefObject<Record<string, number>>
  timelineScrubbingRef: MutableRefObject<boolean>
  selectedRoomIdsRef: MutableRefObject<Set<string>>
  lastPositionsSetStateAtRef: MutableRefObject<number>
}): void {
  const {
    setPreviewPositions,
    lastPreviewPositionsRef,
    scrubOverrideRef,
    timelineScrubbingRef,
    selectedRoomIdsRef,
    lastPositionsSetStateAtRef,
  } = opts

  useEffect(() => {
    const id = setInterval(() => {
      // scrub 中跳过：光标走 Timeline 本地 dragTime，避免父级轮询重渲染抢帧
      if (timelineScrubbingRef.current) return
      const registry = (window as any).__msePlayers
      if (!registry) return
      const next: Record<string, number> = { ...lastPreviewPositionsRef.current }
      let changed = false
      for (const rid of Object.keys(registry)) {
        const entry = registry[rid]
        const t = entry?.player?.videoElement?.currentTime
        if (typeof t !== 'number' || t < 0) continue
        const scrub = scrubOverrideRef.current[rid]
        if (scrub != null) {
          if (Math.abs(t - scrub) < 0.35) {
            delete scrubOverrideRef.current[rid]
            next[rid] = t
            changed = true
            writePlayhead(rid, t)
          } else {
            if (next[rid] !== scrub) {
              next[rid] = scrub
              changed = true
            }
            writePlayhead(rid, scrub)
          }
          continue
        }
        // 高频视觉通道：播放头经 playheadStore rAF 直写 DOM（60fps），不进 React state
        writePlayhead(rid, t)
        const prev = lastPreviewPositionsRef.current[rid]
        if (prev === undefined || Math.abs(t - prev) > 0.01) {
          next[rid] = t
          changed = true
        }
      }
      // 低频逻辑通道：500ms 才 setState，仅驱动 contentEnd/窗口公式，降低全页重渲染
      const now = Date.now()
      if (changed && now - lastPositionsSetStateAtRef.current >= 500) {
        lastPositionsSetStateAtRef.current = now
        lastPreviewPositionsRef.current = next
        setPreviewPositions(next)
      } else if (changed) {
        lastPreviewPositionsRef.current = next
      }

      // 显示轴绝对播放头（与 timelineView.currentTime 同轴）→ Timeline rAF 直写
      const store = useAppStore.getState()
      const ctx = store.timelineContext
      const status = getAlignStatus(ctx, store.timelineInvalidated)
      const refId =
        pickReferenceRoomId(ctx, selectedRoomIdsRef.current, null)
        || Object.keys(registry).find((id) => next[id] != null || lastPreviewPositionsRef.current[id] != null)
        || null
      if (refId) {
        const t = next[refId] ?? lastPreviewPositionsRef.current[refId] ?? 0
        if (status === 'ready' && ctx?.room_snapshots[refId]) {
          try {
            writeDisplayPlayhead(previewToCommon(ctx, refId, t))
          } catch {
            writeDisplayPlayhead(t)
          }
        } else {
          writeDisplayPlayhead(t)
        }
      }
    }, 200)  // 采样 200ms 供播放头直写；setState 已降频 500ms（见上）
    return () => clearInterval(id)
  }, [
    setPreviewPositions,
    lastPreviewPositionsRef,
    scrubOverrideRef,
    timelineScrubbingRef,
    selectedRoomIdsRef,
    lastPositionsSetStateAtRef,
  ])
}
