// lsc-electron/src/utils/clipNaming.ts
// 切片命名：短 label 用于列表/文件；悬停用全名

const INVALID_CHARS = /[/\\:*?"<>|]/g

/** 清理主播名；默认截到 6 字，避免列表挤爆 */
export function sanitizeStreamerName(name: string, maxLen = 6): string {
  const cleaned = (name || '未知').replace(INVALID_CHARS, '_').trim() || '未知'
  return cleaned.slice(0, maxLen)
}

/** 手动切片短名：主播_M01 */
export function formatManualClipLabel(streamer: string, index: number): string {
  return `${sanitizeStreamerName(streamer)}_M${String(index).padStart(2, '0')}`
}

/** AI 回合短名：主播_R03 */
export function formatAiRoundClipLabel(streamer: string, roundIdx: number, _index?: number): string {
  return `${sanitizeStreamerName(streamer)}_R${String(roundIdx).padStart(2, '0')}`
}

/** 悬停全名：房间 · 短名 · 入出点 */
export function formatClipHoverTitle(
  label: string,
  opts?: { roomName?: string; start?: number; end?: number; formatTime?: (s: number) => string },
): string {
  const parts: string[] = []
  if (opts?.roomName) parts.push(opts.roomName)
  parts.push(label || '切片')
  if (opts?.formatTime && opts.start != null && opts.end != null) {
    parts.push(`${opts.formatTime(opts.start)} – ${opts.formatTime(opts.end)}`)
  }
  return parts.join(' · ')
}
