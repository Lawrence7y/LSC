import { Tooltip } from 'antd'
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
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0, width: '100%', maxWidth: 400 }}>
      <span
        style={{
          display: 'inline-block',
          width: 7,
          height: 7,
          borderRadius: '50%',
          backgroundColor: ok ? '#45ab6c' : '#c96868',
          boxShadow: ok ? '0 0 6px rgba(69, 171, 108, 0.4)' : '0 0 6px rgba(201, 104, 104, 0.3)',
          flexShrink: 0,
        }}
      />
      <div style={{ display: 'flex', flexDirection: 'column', minWidth: 0, flex: 1, overflow: 'hidden' }}>
        {version && (
          <Tooltip title={version}>
            <span
              style={{
                fontSize: 12,
                color: ok ? 'var(--text-primary)' : 'var(--state-error)',
                fontFamily: 'var(--font-mono)',
                fontWeight: 500,
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
