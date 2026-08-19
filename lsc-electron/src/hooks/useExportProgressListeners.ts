import { useEffect, type Dispatch, type MutableRefObject, type SetStateAction } from 'react'
import { message } from 'antd'
import { useAppStore } from '@/store/appStore'
import type { ExportProgressInfo } from '@/pages/Workbench/components/ClipList'
import { t } from '@/i18n'

type OnFn = (type: string, handler: (data: any) => void) => () => void
type SendFn = (type: string, data?: any) => boolean

/**
 * 导出进度 / 完成 / 失败 WebSocket 监听（从 Workbench 拆出，降低巨型组件体积）。
 */
export function useExportProgressListeners(opts: {
  on: OnFn
  send: SendFn
  setExportProgressMap: Dispatch<SetStateAction<Record<string, ExportProgressInfo>>>
  exportProgressPendingRef: MutableRefObject<Record<string, ExportProgressInfo>>
  exportProgressFlushTimerRef: MutableRefObject<ReturnType<typeof setTimeout> | null>
  exportProgressStatusPendingRef: MutableRefObject<Set<string>>
  pendingExportJobIdsRef: MutableRefObject<Set<string>>
}): void {
  const {
    on,
    send,
    setExportProgressMap,
    exportProgressPendingRef,
    exportProgressFlushTimerRef,
    exportProgressStatusPendingRef,
    pendingExportJobIdsRef,
  } = opts
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
    unsubs.push(on('clip_export_started', (data: any) => {
      if (!data?.job_id) return
      exportProgressPendingRef.current[data.job_id] = {
        percent: 0,
        elapsed: 0,
        total: 0,
      }
      exportProgressStatusPendingRef.current.add(data.job_id)
      scheduleExportProgressFlush()
    }))
    unsubs.push(on('export_progress', (data: any) => {
      if (data?.job_id && typeof data.percent === 'number') {
        exportProgressPendingRef.current[data.job_id] = {
          percent: data.percent,
          elapsed: data.elapsed ?? 0,
          total: data.total ?? 0,
        }
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
        exportProgressStatusPendingRef.current.delete(data.job_id)
        setExportProgressMap(prev => {
          if (!prev[data.job_id]) return prev
          const next = { ...prev }
          delete next[data.job_id]
          return next
        })
        // 聚焦时弹应用内 toast；失焦时由 useNotifications 的 OS 通知覆盖
        if (document.hasFocus()) {
          message.success(t('切片导出完成'))
        }
      }
    }))
    unsubs.push(on('clip_failed', (data: { room_id?: string; job_id?: string; error?: string }) => {
      const isCancelled = data.error === '导出已取消'
      if (!isCancelled && data.error) {
        message.error({ content: t('导出失败：{err}', { err: data.error }), duration: 5 })
      } else if (!isCancelled && !data.error) {
        message.error({ content: t('导出失败：未知错误。请点击切片列表中的「打开输出文件夹」排查或重试。'), duration: 5 })
      }
      if (data?.job_id) {
        const jid = data.job_id
        delete exportProgressPendingRef.current[jid]
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
            ? { ...c, export_status: 'failed' as const, export_error: data.error || t('导出失败') }
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
      message.error(t('导出失败：{err}', { err: data.error || t('未知错误') }))
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
            ? { ...c, export_status: 'failed' as const, export_error: data.error || t('导出失败') }
            : c
        ))
      }
    }
    unsubs.push(on('export_clip_response', handleExportSubmitResponse))
    unsubs.push(on('export_clip_by_id_response', handleExportSubmitResponse))
    unsubs.push(on('cancel_export_response', (data: { success?: boolean; error?: string; job_id?: string }) => {
      if (data?.success === false) {
        message.warning(t('取消导出失败：{err}', { err: data.error || t('任务可能已结束') }))
      }
    }))
    unsubs.push(on('get_export_job_status_response', (data: {
      success?: boolean
      jobs?: Array<{
        job_id: string
        status: 'queued' | 'exporting' | 'completed' | 'failed' | 'cancelled'
        percent?: number
        elapsed?: number
        total?: number
        output_path?: string
        error?: string
      }>
    }) => {
      if (data?.success === false || !Array.isArray(data?.jobs)) return
      const store = useAppStore.getState()
      let clipsChanged = false
      let nextClips = store.clips
      for (const job of data.jobs) {
        if (!job?.job_id) continue
        if (job.status === 'queued' || job.status === 'exporting') {
          exportProgressPendingRef.current[job.job_id] = {
            percent: Number(job.percent || 0),
            elapsed: Number(job.elapsed || 0),
            total: Number(job.total || 0),
          }
          exportProgressStatusPendingRef.current.add(job.job_id)
          scheduleExportProgressFlush()
          continue
        }
        delete exportProgressPendingRef.current[job.job_id]
        exportProgressStatusPendingRef.current.delete(job.job_id)
        setExportProgressMap(prev => {
          if (!prev[job.job_id]) return prev
          const next = { ...prev }
          delete next[job.job_id]
          return next
        })
        nextClips = nextClips.map(clip => {
          if (clip.job_id !== job.job_id) return clip
          clipsChanged = true
          if (job.status === 'completed') {
            return {
              ...clip,
              exported: true,
              outputPath: job.output_path,
              export_status: 'completed' as const,
              export_error: undefined,
            }
          }
          return {
            ...clip,
            export_status: job.status === 'cancelled' ? 'pending' as const : 'failed' as const,
            export_error: job.status === 'cancelled' ? undefined : (job.error || t('导出失败')),
          }
        })
      }
      if (clipsChanged) store.setClips(nextClips)
    }))

    // 终态补偿：广播可能因重连或线程切换丢失，主动查询仍在队列/导出中的任务。
    // 从 1500ms 提升到 5000ms：主要进度由 export_progress 事件实时推送，
    // 此定时器仅为安全网，无需高频轮询。
    const syncExportJobStatus = () => {
      const jobIds = useAppStore.getState().clips
        .filter(clip => (
          (clip.export_status === 'queued' || clip.export_status === 'exporting')
          && Boolean(clip.job_id)
        ))
        .map(clip => clip.job_id!)
      if (jobIds.length > 0) {
        send('get_export_job_status', { job_ids: [...new Set(jobIds)] })
      }
    }
    syncExportJobStatus()
    const statusSyncTimer = setInterval(syncExportJobStatus, 5000)
    return () => {
      clearInterval(statusSyncTimer)
      if (exportProgressFlushTimerRef.current != null) {
        clearTimeout(exportProgressFlushTimerRef.current)
        exportProgressFlushTimerRef.current = null
      }
      unsubs.forEach(u => u())
    }
  }, [
    on,
    send,
    setExportProgressMap,
    exportProgressPendingRef,
    exportProgressFlushTimerRef,
    exportProgressStatusPendingRef,
    pendingExportJobIdsRef,
  ])
}
