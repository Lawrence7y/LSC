import { useEffect, useState } from 'react'
import { App, Button, Input, Tooltip } from 'antd'
import { useWebSocket } from '@/hooks/useWebSocket'
import { useI18n } from '@/i18n'

/** 后端 `run_platform_self_check` 的报告（`AcceptanceReport.to_dict()` 的子集）。 */
type SelfCheckReport = {
  passed?: boolean
  platform?: string
  support_level?: string
  credential_status?: string
  candidate_count?: number
  probe_count?: number
  selected_lease?: Record<string, unknown>
  timings_ms?: Record<string, number>
  failures?: { code: string; detail: string }[]
  lifecycle?: Record<string, unknown>
}

const ROW: React.CSSProperties = {
  display: 'flex',
  alignItems: 'baseline',
  gap: 8,
  fontSize: 12,
  lineHeight: 1.7,
}
const LABEL: React.CSSProperties = {
  color: 'var(--text-tertiary)',
  minWidth: 92,
  flexShrink: 0,
}

/**
 * 平台自检（P2-2c）。
 *
 * `lsc/platforms/acceptance.py` 那套控制面验收此前只有开发者跑
 * `scripts/platform_acceptance.py` 才碰得到，于是"房间打不开"到底该怪平台改版、
 * 自己的网络，还是 Cookie 过期，用户完全无从判断。这里把它接进设置页，
 * 后端侧固定 `recording=False / preview=False / record_dir=""`——不启 FFmpeg、不写盘。
 */
export function PlatformSelfCheck() {
  const { t } = useI18n()
  const { message } = App.useApp()
  const { send, on, isConnected } = useWebSocket()
  const [url, setUrl] = useState('')
  const [running, setRunning] = useState(false)
  const [report, setReport] = useState<SelfCheckReport | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    const unsub = on('run_platform_self_check_response', (data: {
      success?: boolean
      error?: string
      report?: SelfCheckReport
    }) => {
      setRunning(false)
      if (data?.success === false) {
        setError(data?.error || t('自检失败'))
        setReport(null)
        return
      }
      setError('')
      setReport(data?.report || null)
    })
    return () => {
      unsub?.()
    }
  }, [on, t])

  const handleRun = () => {
    if (!url.trim()) {
      message.warning(t('请先粘贴一个直播间链接'))
      return
    }
    setError('')
    setReport(null)
    setRunning(true)
    send('run_platform_self_check', { source_url: url.trim() })
  }

  const failures = report?.failures || []
  const timings = report?.timings_ms || {}

  return (
    <div style={{
      background: 'var(--bg-secondary)',
      border: '1px solid var(--border-default)',
      borderRadius: 'var(--radius-md, 8px)',
      overflow: 'hidden',
      padding: 16,
      marginBottom: 16,
    }}>
      <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)', marginBottom: 8 }}>
        {t('平台自检')}
      </div>
      <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginBottom: 10, lineHeight: 1.6 }}>
        {t('粘贴一个直播间链接，检查解析、线路探测与凭据是否都正常。不会启动录制，也不会写任何文件。')}
      </div>
      <Input
        value={url}
        onChange={(e) => setUrl(e.target.value)}
        onPressEnter={handleRun}
        placeholder={t('https://... 直播间链接')}
        disabled={running}
        style={{ marginBottom: 10 }}
      />
      <Button type="primary" size="small" loading={running} onClick={handleRun} disabled={!isConnected}>
        {running ? t('自检中...') : t('开始自检')}
      </Button>

      {error && (
        <div style={{ ...ROW, color: 'var(--state-error)', marginTop: 10 }}>{error}</div>
      )}

      {report && (
        <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 2 }}>
          <div style={{ ...ROW, color: report.passed ? 'var(--state-success)' : 'var(--state-error)', fontWeight: 600 }}>
            {report.passed ? t('✓ 控制面自检通过') : t('✗ 自检未通过，见下方失败项')}
          </div>
          <div style={ROW}>
            <span style={LABEL}>{t('识别平台')}</span>
            <span style={{ fontFamily: 'var(--font-mono)' }}>{report.platform || 'unknown'}</span>
            <Tooltip title={t('该平台在本产品中的支持等级')}>
              <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>{report.support_level || ''}</span>
            </Tooltip>
          </div>
          <div style={ROW}>
            <span style={LABEL}>{t('凭据状态')}</span>
            <span style={{ fontFamily: 'var(--font-mono)', color: report.credential_status === 'INVALID' ? 'var(--state-error)' : 'var(--text-secondary)' }}>
              {report.credential_status || 'NOT_CONFIGURED'}
            </span>
          </div>
          <div style={ROW}>
            <span style={LABEL}>{t('候选/探测')}</span>
            <span style={{ fontFamily: 'var(--font-mono)' }}>
              {`${report.candidate_count ?? 0} / ${report.probe_count ?? 0}`}
            </span>
          </div>
          {Object.keys(timings).length > 0 && (
            <div style={ROW}>
              <span style={LABEL}>{t('耗时(ms)')}</span>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11 }}>
                {Object.entries(timings).map(([k, v]) => `${k}=${v}`).join('  ')}
              </span>
            </div>
          )}
          {failures.length > 0 && (
            <div style={{ marginTop: 6, display: 'flex', flexDirection: 'column', gap: 4 }}>
              {failures.map((item) => (
                <div key={`${item.code}:${item.detail}`} style={{ ...ROW, alignItems: 'flex-start' }}>
                  <span style={{ ...LABEL, color: 'var(--state-error)' }}>{item.code}</span>
                  <span style={{ color: 'var(--text-secondary)', wordBreak: 'break-word' }}>{item.detail}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
