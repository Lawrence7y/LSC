import { Tooltip } from 'antd'
import { CheckCircleFilled, CloseCircleFilled } from '@ant-design/icons'
import { useI18n } from '@/i18n'

export function DepStatus({
  ok,
  version,
  path: depPath,
}: {
  ok: boolean | undefined
  version?: string
  path?: string
}) {
  const { t } = useI18n()
  if (ok === undefined) {
    return <span style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>{t('检测中...')}</span>
  }
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0, maxWidth: 'min(320px, 100%)' }}>
      {ok ? (
        <CheckCircleFilled style={{ color: 'var(--state-success)', fontSize: 14, flexShrink: 0 }} />
      ) : (
        <CloseCircleFilled style={{ color: 'var(--state-error)', fontSize: 14, flexShrink: 0 }} />
      )}
      <div style={{ display: 'flex', flexDirection: 'column', minWidth: 0, flex: 1 }}>
        {version && (
          <Tooltip title={version}>
            <span
              style={{
                fontSize: 12,
                color: ok ? 'var(--text-secondary)' : 'var(--state-error)',
                lineHeight: 1.4,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
            >
              {version.split('\n')[0]}
            </span>
          </Tooltip>
        )}
        {depPath && (
          <Tooltip title={depPath}>
            <span
              style={{
                fontSize: 11,
                color: 'var(--text-tertiary)',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
                fontFamily: 'var(--font-mono)',
              }}
            >
              {depPath}
            </span>
          </Tooltip>
        )}
      </div>
    </div>
  )
}
