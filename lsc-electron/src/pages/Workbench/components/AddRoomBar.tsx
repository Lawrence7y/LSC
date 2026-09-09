import { useMemo } from 'react'
import { Button, Input, Tooltip } from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import { useI18n } from '@/i18n'
import type { RoomUrlValidationState } from '@/hooks/useAddRoom'

/** 单次可添加的链接上限，与 useAddRoom 的校验保持一致 */
const MAX_ROOM_URLS = 12

interface AddRoomBarProps {
  url: string
  onUrlChange: (value: string) => void
  /** 重新编辑时清除上一次校验结果提示 */
  onClearValidation: () => void
  onSubmit: () => void
  loading: boolean
  validation: RoomUrlValidationState
}

/**
 * 添加直播间输入条。
 *
 * 两个历史缺陷在此修掉：
 * 1. 原先用单行 `Input`，而解析逻辑按换行切分多链接 —— 单行输入框永远拿不到
 *    换行，Onboarding 承诺的「一次最多添加 12 路」实际不可达。改成 TextArea。
 * 2. 原先挂在右侧切片面板内，而该面板可折叠到 width:0 —— 收起切片后无法添加房间。
 *    现在移到房间网格上方，与它操作的对象同区。
 */
export function AddRoomBar({ url, onUrlChange, onClearValidation, onSubmit, loading, validation }: AddRoomBarProps) {
  const { t } = useI18n()

  const pendingCount = useMemo(
    () => url.split(/\r?\n/).map(s => s.trim()).filter(Boolean).length,
    [url],
  )
  const overLimit = pendingCount > MAX_ROOM_URLS

  return (
    <div className="add-room-bar">
      <div className="add-room-bar__field">
        <Input.TextArea
          value={url}
          autoSize={{ minRows: 1, maxRows: 6 }}
          placeholder={t('粘贴直播间链接，一行一个（最多 {count} 路）', { count: MAX_ROOM_URLS })}
          disabled={loading}
          onChange={e => {
            onUrlChange(e.target.value)
            // 重新编辑即清掉上一次的结果提示，避免旧文案挂在下面误导
            if (validation.status !== 'idle') onClearValidation()
          }}
          onKeyDown={e => {
            // Enter 提交、Shift+Enter 换行：既保留单链接习惯，也让多行粘贴可用
            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault()
              if (!loading && url.trim()) onSubmit()
            }
          }}
          className="add-room-bar__input"
        />
        <Tooltip
          title={
            overLimit
              ? t('一次最多添加 {count} 个直播间', { count: MAX_ROOM_URLS })
              : pendingCount > 1
                ? t('将添加 {count} 个直播间', { count: pendingCount })
                : t('添加直播间')
          }
        >
          <Button
            type="primary"
            icon={<PlusOutlined />}
            loading={loading}
            disabled={!url.trim() || overLimit}
            onClick={onSubmit}
            className="add-room-bar__submit"
          >
            {pendingCount > 1 ? t('添加 {count} 路', { count: pendingCount }) : t('添加')}
          </Button>
        </Tooltip>
      </div>
      {validation.status !== 'idle' && (
        <div
          className={`add-room-bar__msg add-room-bar__msg--${
            validation.status === 'checking' ? 'pending' : validation.status === 'success' ? 'ok' : 'err'
          }`}
          role="status"
        >
          {validation.message}
        </div>
      )}
    </div>
  )
}
