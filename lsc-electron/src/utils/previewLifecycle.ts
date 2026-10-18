import type { I18nT } from '@/i18n'

/**
 * 预览播放器"该不该重建 / 该说什么话"的纯判据
 * （2026-09-19 从 `components/VideoPreview.tsx` 搬出，债务报告 §11.28）。
 *
 * 搬的理由：这两条都是"做错就表现为预览黑屏或假文案"的判据，而它们此前的守卫形状是
 * **否定式文本断言**（例如断言某段代码里不出现 `bilibili`、不出现 `platform`），
 * 既证明不了分支正确，也会在无关改名时假红。
 */

export interface PreviewSource {
  /** `live_mse` / `recording_review`；review 由独立的 ReviewPlayer 承载。 */
  mode: string
  /** 后端预览 sink 的代际 id；空串表示尚未拿到（首帧前）。 */
  epoch: string
}

/**
 * 只有**直播 epoch 真正变化**才重建 LivePlayer。
 *
 * 两条必须同时成立：
 * - `epoch` 非空：还没拿到 epoch 时不能重建，否则每次 store 抖动都拆一次播放器；
 * - 与上一帧不同：同 epoch 的重复广播（心跳/重排）不得清缓存重来。
 * 而 `mode` 在 live ↔ recording_review 之间切换**不**触发重建：回看是另一个播放器，
 * 销毁 LivePlayer 会让"回到直播"变成一次重新拉流。
 */
export function shouldRebuildLivePlayer(
  prev: PreviewSource | null,
  next: PreviewSource,
): boolean {
  if (prev === null) return false
  return next.epoch !== '' && prev.epoch !== next.epoch
}

/**
 * 阶段文案。默认档保持历史口径"正在拉流/转码…"；
 * `refreshing_url`（刷新流地址）额外给一条"首次可能需要 10–30 秒"的提示。
 * 文案不得按平台特化 —— 预览链路对所有平台同构，写死某个平台名会误导用户。
 */
export function previewPhaseCopy(
  phase: string | undefined,
  t: I18nT,
): { text: string; hint: string | null } {
  if (phase === 'refreshing_url') {
    return { text: t('正在刷新流地址…'), hint: t('首次刷新可能需要 10–30 秒') }
  }
  if (phase === 'probing') {
    return { text: t('正在探测/转码…'), hint: null }
  }
  return { text: t('正在拉流/转码…'), hint: null }
}
