import { Tooltip } from 'antd'
import { useI18n } from '@/i18n'

/**
 * 平台 Cookie 状态。
 *
 * `configured/count/keys` 回答的是"用户贴过没有"（后端只读文件）；
 * `credential_*` 三件套回答的是"这份 Cookie 在平台上还好不好"——它来自运行时
 * 实测：解析/拉流被判 AUTH_REQUIRED / AUTH_EXPIRED 时由后端凭据提供方打标记。
 * 两者刻意分开：贴过 ≠ 有效，此前 UI 只显示前者，用户贴完过期 Cookie 只能看到
 * "已配置"加一个说不清的黑屏。
 */
export type CookieStatus = {
  configured?: boolean
  count?: number
  keys?: string[]
  credential_status?: string
  credential_invalidated_at?: number
  credential_invalidated_reason?: string
}

/** 后端响应 → 状态的唯一收敛点（6 个监听器共用，避免字段逐处手抄）。 */
export function cookieStatusFrom(data: CookieStatus | undefined | null): CookieStatus {
  return {
    configured: !!data?.configured,
    count: data?.count || 0,
    keys: data?.keys || [],
    credential_status: data?.credential_status,
    credential_invalidated_at: data?.credential_invalidated_at,
    credential_invalidated_reason: data?.credential_invalidated_reason,
  }
}

export function CookieStatusLine({ status }: { status: CookieStatus | null }) {
  const { t } = useI18n()
  const configured = !!status?.configured
  const invalidated = status?.credential_status === 'INVALID'
  const checkedAt = status?.credential_invalidated_at
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, marginBottom: 10 }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          color: configured ? 'var(--state-success)' : 'var(--state-warning)',
        }}
      >
        {configured
          ? <>
              <span>{t('已配置 {count} 项', { count: status?.count || 0 })}</span>
              {(status?.keys || []).length > 0 && (
                <Tooltip title={(status?.keys || []).join(', ')}>
                  <span style={{
                    fontSize: 11,
                    color: 'var(--text-tertiary)',
                    textDecoration: 'underline dotted',
                    cursor: 'help',
                  }}>
                    {t('查看键名')}
                  </span>
                </Tooltip>
              )}
            </>
          : t('尚未配置有效 Cookie')}
      </div>
      {configured && invalidated && (
        <div style={{ color: 'var(--state-error)', display: 'flex', alignItems: 'center', gap: 6 }}>
          <span>
            {t('实测已失效：{reason}', {
              reason: status?.credential_invalidated_reason || t('登录态被平台拒绝'),
            })}
          </span>
          {typeof checkedAt === 'number' && checkedAt > 0 && (
            <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>
              {new Date(checkedAt * 1000).toLocaleString()}
            </span>
          )}
          <span>{t('，请重新粘贴有效 Cookie 后重新连接直播间')}</span>
        </div>
      )}
    </div>
  )
}
