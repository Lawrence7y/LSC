import { useEffect, useRef, type Dispatch, type MutableRefObject, type SetStateAction } from 'react'
import { message } from 'antd'
import { useAppStore } from '@/store/appStore'
import type { ExportProgressInfo } from '@/pages/Workbench/components/ClipList'

type OnFn = (type: string, handler: (data: any) => void) => () => void

/** 进度条目超时：超过该时长无任何 export_progress 更新，判定终态事件丢失并清扫 */
const EXPORT_PROGRESS_STALE_MS = 120_000
/** 超时清扫巡检间隔 */
const EXPORT_PROGRESS_SWEEP_INTERVAL_MS = 30_000

/**
 * 导出进度 / 完成 / 失败 WebSocket 监听（从 Workbench 拆出，降低巨型组件体积）。
 */
export function useExportProgressListeners(opts: {
  on: OnFn
  setExportProgressMap: Dispatch<SetStateAction<Record<string, ExportProgressInfo>>>
  exportProgressPendingRef: MutableRefObject<Record<string, ExportProgressInfo>>
  exportProgressFlushTimerRef: MutableRefObject<ReturnType<typeof setTimeout> | null>
  exportProgressStatusPendingRef: MutableRefObject<Set<string>>
  pendingExportJobIdsRef: MutableRefObject<Set<string>>
}): void {
  const {
    on,
    setExportProgressMap,
    exportProgressPendingRef,
    exportProgressFlushTimerRef,
    exportProgressStatusPendingRef,
    pendingExportJobIdsRef,
  } = opts
  // 各 job 最近一次进度时间戳：用于终态事件（clip_completed/clip_failed）丢失时的兜底清扫
  const lastProgressAtRef = useRef<Record<string, number>>({})

  useEffect(() => {
    const unsubs: (() => void)[] = []
    const flushExportProgress = () => {
      exportProgressFlushTimerRef.current = null
      const pending = exportProgressPendingRef.current
      const statusJobs = exportProgressStatusPendingRef.current
      if (Object.keys(pending).length === 0 && statusJobs.size === 0) return
      exportProgressPendingRef.current = {}
      if (statusJobs.size > 0) {
        const jobIds = new Set(statusJobs)
        statusJobs.clear()
        const progressStore = useAppStore.getState()
        let statusChanged = false
        const updatedClips = progressStore.clips.map(c => {
          if (c.job_id && jobIds.has(c.job_id) && c.export_status !== 'exporting') {
            statusChanged = true
            return { ...c, export_status: 'exporting' as const }
          }
          return c
        })
        if (statusChanged) progressStore.setClips(updatedClips)
      }
      setExportProgressMap(prev => ({ ...prev, ...pending }))
    }
    const scheduleExportProgressFlush = () => {
      if (exportProgressFlushTimerRef.current != null) return
      exportProgressFlushTimerRef.current = setTimeout(flushExportProgress, 500)
    }
    unsubs.push(on('export_progress', (data: any) => {
      if (data?.job_id && typeof data.percent === 'number') {
        exportProgressPendingRef.current[data.job_id] = {
          percent: data.percent,
          elapsed: data.elapsed ?? 0,
          total: data.total ?? 0,
        }
        lastProgressAtRef.current[data.job_id] = Date.now()
        exportProgressStatusPendingRef.current.add(data.job_id)
        scheduleExportProgressFlush()
      }
    }))
    unsubs.push(on('clip_completed', (data: any) => {
      if (data?.job_id) {
        const store = useAppStore.getState()
        const updatedClips = store.clips.map(c =>
          c.job_id === data.job_id
            ? { ...c, exported: true, outputPath: data.output_path, export_status: 'completed' as const, export_error: undefined }
            : c
        )
        store.setClips(updatedClips)
        delete exportProgressPendingRef.current[data.job_id]
        delete lastProgressAtRef.current[data.job_id]
        exportProgressStatusPendingRef.current.delete(data.job_id)
        setExportProgressMap(prev => {
          if (!prev[data.job_id]) return prev
          const next = { ...prev }
          delete next[data.job_id]
          return next
        })
        // 聚焦时弹应用内 toast；失焦时由 useNotifications 的 OS 通知覆盖
        if (document.hasFocus()) {
          message.success('切片导出完成')
        }
      }
    }))
    unsubs.push(on('clip_failed', (data: { room_id?: string; job_id?: string; error?: string }) => {
      const isCancelled = data.error === '导出已取消'
      if (!isCancelled && data.error) {
        message.error({ content: `导出失败：${data.error}`, duration: 5 })
      } else if (!isCancelled && !data.error) {
        message.error({ content: '导出失败：未知错误。请点击切片列表中的「打开输出文件夹」排查或重试。', duration: 5 })
      }
      if (data?.job_id) {
        const jid = data.job_id
        delete exportProgressPendingRef.current[jid]
        delete lastProgressAtRef.current[jid]
        exportProgressStatusPendingRef.current.delete(jid)
        setExportProgressMap(prev => {
          if (!prev[jid]) return prev
          const next = { ...prev }
          delete next[jid]
          return next
        })
        const store = useAppStore.getState()
        const updatedClips = store.clips.map(c =>
          c.job_id === jid
            ? { ...c, export_status: 'failed' as const, export_error: data.error || '导出失败' }
            : c
        )
        store.setClips(updatedClips)
      }
    }))
    const handleExportSubmitResponse = (data: { success?: boolean; error?: string; job_id?: string }) => {
      const failed = data?.success === false || (Boolean(data?.error) && data?.success !== true)
      if (!failed) {
        if (data?.job_id) pendingExportJobIdsRef.current.delete(data.job_id)
        return
      }
      message.error(`导出失败：${data.error || '未知错误'}`)
      const rollbackIds = new Set<string>()
      if (data?.job_id) {
        rollbackIds.add(data.job_id)
        pendingExportJobIdsRef.current.delete(data.job_id)
      } else {
        for (const id of pendingExportJobIdsRef.current) rollbackIds.add(id)
        pendingExportJobIdsRef.current.clear()
      }
      if (rollbackIds.size > 0) {
        setExportProgressMap(prev => {
          let next = prev
          for (const id of rollbackIds) {
            if (next[id]) {
              if (next === prev) next = { ...prev }
              delete next[id]
            }
            delete exportProgressPendingRef.current[id]
            exportProgressStatusPendingRef.current.delete(id)
          }
          return next
        })
        const store = useAppStore.getState()
        store.setClips(store.clips.map(c =>
          c.job_id && rollbackIds.has(c.job_id)
            ? { ...c, export_status: 'failed' as const, export_error: data.error || '导出失败' }
            : c
        ))
      }
    }
    unsubs.push(on('export_clip_response', handleExportSubmitResponse))
    unsubs.push(on('export_clip_by_id_response', handleExportSubmitResponse))
    unsubs.push(on('cancel_export_response', (data: { success?: boolean; error?: string; job_id?: string }) => {
      if (data?.success === false) {
        message.warning(`取消导出失败：${data.error || '任务可能已结束'}`)
      }
    }))
    // 超时清扫：后端若漏发终态事件（进程崩溃/消息丢失），进度条目不会永久残留
    const sweepTimer = setInterval(() => {
      const now = Date.now()
      const staleJobIds = Object.keys(lastProgressAtRef.current).filter(
        (jid) => now - lastProgressAtRef.current[jid] > EXPORT_PROGRESS_STALE_MS,
      )
      if (staleJobIds.length === 0) return
      const staleSet = new Set(staleJobIds)
      for (const jid of staleJobIds) {
        delete lastProgressAtRef.current[jid]
        delete exportProgressPendingRef.current[jid]
        exportProgressStatusPendingRef.current.delete(jid)
      }
      setExportProgressMap(prev => {
        let next = prev
        for (const jid of staleJobIds) {
          if (next[jid]) {
            if (next === prev) next = { ...prev }
            delete next[jid]
          }
        }
        return next
      })
      const store = useAppStore.getState()
      store.setClips(store.clips.map(c =>
        c.job_id && staleSet.has(c.job_id) && c.export_status === 'exporting'
          ? { ...c, export_status: 'failed' as const, export_error: '导出进度超时，请检查产物或重试' }
          : c
      ))
    }, EXPORT_PROGRESS_SWEEP_INTERVAL_MS)
    return () => {
      clearInterval(sweepTimer)
      if (exportProgressFlushTimerRef.current != null) {
        clearTimeout(exportProgressFlushTimerRef.current)
        exportProgressFlushTimerRef.current = null
      }
      unsubs.forEach(u => u())
    }
  }, [
    on,
    setExportProgressMap,
    exportProgressPendingRef,
    exportProgressFlushTimerRef,
    exportProgressStatusPendingRef,
    pendingExportJobIdsRef,
  ])
}
