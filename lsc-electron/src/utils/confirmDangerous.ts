import type { ModalFuncProps } from 'antd'
import { t } from '@/i18n'

/**
 * antd v5 的静态 `Modal.confirm` 不消费 ConfigProvider（主题 / locale），
 * 暗色界面下会弹出浅色对话框。全项目统一走 `App.useApp()` 拿到的 context modal，
 * 本模块只负责把「不可逆动作」的确认框样式收敛成一份，避免同一动作在不同入口
 * （卡片按钮 / 快捷键 / 批量操作）弹出不同文案。
 */
export type ConfirmModalApi = {
  confirm: (config: ModalFuncProps) => unknown
}

/**
 * 危险动作统一二次确认。
 *
 * @param modal  `App.useApp()` 返回的 modal 实例
 * @param okText 确认按钮文案，默认「确认」
 */
export function confirmDangerous(
  modal: ConfirmModalApi,
  title: string,
  content: string,
  onOk: () => void,
  okText?: string,
): void {
  modal.confirm({
    title,
    content,
    okText: okText ?? t('确认'),
    okButtonProps: { danger: true },
    cancelText: t('取消'),
    onOk,
  })
}

/**
 * 「停止录制」的确认文案：持续分析正在跟这个房间时，必须提示收尾流程，
 * 否则用户停录后会以为回合丢了（实际是还在补扫尾部）。
 *
 * @param analyzingThisRoom 该房间是否为当前持续分析的目标房（按房判定，
 *                          不要用全局 running，否则无关房间也会被吓一下）
 */
export function stopRecordConfirmContent(
  roomNames: string[],
  analyzingThisRoom: boolean,
): string {
  const named = roomNames.length === 1
    ? t('将停止录制「{name}」', { name: roomNames[0] })
    : t('将停止 {count} 个房间的录制', { count: roomNames.length })
  if (!analyzingThisRoom) return named
  return roomNames.length === 1
    ? t('将停止录制「{name}」。请先结束录制，再等待持续分析收尾并将回合入列待确认，请勿立刻停止分析。', { name: roomNames[0] })
    : t('将停止 {count} 个房间的录制。持续分析将收尾并将回合入列待确认，请勿立刻停止分析。', { count: roomNames.length })
}
