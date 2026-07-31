import { useEffect, type Dispatch, type MutableRefObject, type SetStateAction } from 'react'
import { useAppStore } from '@/store/appStore'
import { getAlignStatus, pickReferenceRoomId, previewToCommon } from '@/utils/timelineCoords'
import { writeDisplayPlayhead, writePlayhead } from '@/utils/playheadStore'

// 轴换算降级告警节流（200ms 采样循环内，避免刷屏）
let _lastAxisFallbackWarnAt = 0

/**
 * 预览播放头采样：100ms 读 MSE currentTime → playheadStore 直写；
 * setPreviewPositions 降频 500ms，确保 DVR 边界保持亚秒级更新，同时避免
 * 多路预览时 React 树以 4Hz 重渲染。
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
    let rafId: number
    let lastTick = 0
    let lastSetState = 0
    const SAMPLE_INTERVAL_MS = 100  // 采样间隔
    const SETSTATE_INTERVAL_MS = 500  // setState 间隔

    const tick = (now: number) => {
      rafId = requestAnimationFrame(tick)

      // 节流：按 SAMPLE_INTERVAL_MS 采样
      if (now - lastTick < SAMPLE_INTERVAL_MS) return
      lastTick = now

      // scrub 中跳过：光标走 Timeline 本地 dragTime，避免父级轮询重渲染抢帧
      if (timelineScrubbingRef.current) return
      const registry = window.__msePlayers
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
      // 逻辑通道：500ms setState；视觉播放头仍由上面的 rAF 通道保持流畅。
      if (changed && now - lastSetState >= SETSTATE_INTERVAL_MS) {
        lastSetState = now
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
          } catch (err) {
            // preview→common 轴换算失败（对齐快照瞬时不可用）：降级为 preview 轴，
            // 两轴数值含义不同会导致播放头瞬时跳变，节流记录日志便于排查
            if (now - _lastAxisFallbackWarnAt > 5000) {
              _lastAxisFallbackWarnAt = now
              console.warn('[usePlayheadSampling] previewToCommon failed, fallback to preview axis:', err)
            }
            writeDisplayPlayhead(t)
          }
        } else {
          writeDisplayPlayhead(t)
        }
      }
    }

    rafId = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(rafId)
  }, [
    setPreviewPositions,
    lastPreviewPositionsRef,
    scrubOverrideRef,
    timelineScrubbingRef,
    selectedRoomIdsRef,
    lastPositionsSetStateAtRef,
  ])
}
