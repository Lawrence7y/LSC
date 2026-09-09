import type { ClipSegment } from '@/types'

/**
 * 切片可导出性的唯一判定入口（列表页按钮、批量导出、Ctrl+E 快捷键共用）。
 *
 * 历史：Workbench 快捷键路径曾内联一份判定并以 `!== 'refining'` 收尾，
 * 使前三个确认分支恒短路 —— audio_pending 切片可走 Ctrl+E 绕过导出门禁，
 * 而守卫测试只查文本被顺带绕过。现在禁止任何调用方内联 confirm_status 判定。
 */
export function canExportClip(clip: ClipSegment): boolean {
  if (clip.export_status === 'queued' || clip.export_status === 'exporting') return false
  // 切片页取消待确认状态：已确认、OCR/视觉确认及已识别 pending 切片均可直接导出
  return !clip.confirm_status ||
    clip.confirm_status === 'user_confirmed' ||
    clip.confirm_status === 'ocr_confirmed' ||
    clip.confirm_status === 'vision_confirmed' ||
    clip.confirm_status === 'pending'
}

/** 批量或单条可导出判定：与单条同源（保留历史调用点名） */
export function canExportOrConfirmExport(clip: ClipSegment, _hasConfirmAndExport?: boolean): boolean {
  return canExportClip(clip)
}
