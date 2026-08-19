import { useCallback } from 'react'
import { message } from 'antd'
import { useAppStore } from '@/store/appStore'
import { getClipStableId } from '@/pages/Workbench/components/ClipList'
import { useUndoStack } from '@/hooks/useUndoStack'
import { t } from '@/i18n'

type SendFn = (type: string, data?: any) => boolean

/**
 * 切片删除流程（从 Workbench 拆出）。
 *
 * 职责：删除前取消相关导出/精修任务、从列表与选中集移除、并提供 5s 撤销兜底。
 * 撤销栈（{@link useUndoStack}）由本 hook 持有，删除即压入可恢复命令，toast 上「撤销」触发回滚。
 *
 * 读取 clips / setClips 走 useAppStore.getState()（调用时取最新值），避免闭包过期，
 * 同时减少需要传入的依赖数量。
 */
export function useClipDelete(opts: {
  send: SendFn
  refiningClipId: string | null
  setRefiningClipId: (id: string | null) => void
  setClipSelectedIds: (updater: (prev: Set<string>) => Set<string>) => void
}) {
  const { send, refiningClipId, setRefiningClipId, setClipSelectedIds } = opts
  const clipUndo = useUndoStack(20)

  const handleDeleteClip = useCallback((clipId: string) => {
    const { clips, setClips } = useAppStore.getState()
    const idx = clips.findIndex(c => getClipStableId(c) === clipId)
    if (idx === -1) return
    const clip = clips[idx]
    if (clip.export_status === 'queued' || clip.export_status === 'exporting') {
      if (clip.job_id) {
        // 取消导出任务
        send('cancel_export', { job_id: clip.job_id })
      }
    }
    if (clip.confirm_status === 'refining' && refiningClipId != null) {
      const clipKey = clip.round_key || clip.clip_id || ''
      if (clipKey === refiningClipId) {
        // 取消精修
        send('cancel_refine_clip', {
          room_id: clip.room_id,
          round_key: clipKey,
          start: clip.start,
          end: clip.end,
        })
        setRefiningClipId(null)
      }
    }
    const roundKey = clip.round_key || clip.clip_id || ''
    if (clip.is_ai_highlight && roundKey) {
      // 通知后端记 tombstone，防止 OCR upsert 把已删切片重新广播复活
      send('delete_clip', { room_id: clip.room_id, round_key: roundKey })
    }
    setClips(clips.filter(c => getClipStableId(c) !== clipId))
    setClipSelectedIds(prev => {
      if (!prev.has(clipId)) return prev
      const next = new Set(prev)
      next.delete(clipId)
      return next
    })

    // 撤销兜底：捕获被删切片与原位置，5s 内可恢复
    const toastKey = `clip-delete-${clipId}-${Date.now()}`
    const undoId = clipUndo.push(t('删除切片「{label}」', { label: clip.label }), () => {
      const current = useAppStore.getState().clips
      const insertAt = Math.min(idx, current.length)
      const restored = [...current.slice(0, insertAt), clip, ...current.slice(insertAt)]
      useAppStore.getState().setClips(restored)
    })
    message.open({
      key: toastKey,
      type: 'success',
      duration: 5,
      content: (
        <span>
          {t('已删除切片「{label}」', { label: clip.label })}
          <a
            style={{ marginLeft: 8 }}
            onClick={() => {
              if (clipUndo.undo(undoId)) message.success(t('已恢复切片'))
              message.destroy(toastKey)
            }}
          >
            {t('撤销')}
          </a>
        </span>
      ),
    })
  }, [send, refiningClipId, setRefiningClipId, setClipSelectedIds, clipUndo])

  return { handleDeleteClip }
}
