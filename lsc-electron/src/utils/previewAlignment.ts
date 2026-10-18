/**
 * 一键对齐结果落到预览播放头的唯一入口。
 *
 * 为什么单独成模块：这段逻辑原先内联在 `Workbench/index.tsx` 的
 * `align_preview_audio_response` 回调里，全仓 0 条可执行用例能跑到它
 * （`tests/test_frontend_stability_guards.py` 只能对它做文本匹配）。
 */

/** 小于此值的差不用动播放头：seek 本身的落地精度就在几十毫秒量级。 */
export const NEGLIGIBLE_ALIGN_OFFSET_SEC = 0.05

export type AlignmentSeekOutcome = 'seeked' | 'negligible' | 'no-buffer' | 'seek-failed'

/**
 * 把对齐偏移应用到预览：`target = currentTime - offset`，夹在已缓冲区间内。
 *
 * 后端 `align_audio_map` 把"最慢房间"归零后返回，故 `offset` 恒 ≥ 0；
 * 这里仍按符号求差而不是取绝对值，是为了让"只往后调"这一隐含约定
 * 变成代码里看得见的算式 —— 将来若放开负偏移，前向修正不会被当成 0 丢掉。
 */
export function applyAlignmentSeek(
  video: HTMLVideoElement | null | undefined,
  offset: number,
): AlignmentSeekOutcome {
  // 先判"要不要动"再判"有没有数据"：偏移本身可忽略时，房间就是对齐的，
  // 不该因为播放器还没挂上/缓冲为空就被报成"未对齐"。
  if (Math.abs(offset) < NEGLIGIBLE_ALIGN_OFFSET_SEC) return 'negligible'
  if (!video || video.buffered.length === 0) return 'no-buffer'
  const bufStart = video.buffered.start(0)
  const bufEnd = video.buffered.end(video.buffered.length - 1)
  const current = video.currentTime
  const floor = Math.min(bufStart, bufEnd)
  const target = Math.min(Math.max(current - offset, floor), Math.max(bufEnd, floor))
  if (Math.abs(target - current) < NEGLIGIBLE_ALIGN_OFFSET_SEC) return 'negligible'
  let seeked = true
  try {
    video.currentTime = target
  } catch {
    seeked = false
  }
  // seek 失败也要重新触发播放：这一步与原先内联实现同口径，不改预览自愈语义。
  video.play().catch(() => {})
  return seeked ? 'seeked' : 'seek-failed'
}
