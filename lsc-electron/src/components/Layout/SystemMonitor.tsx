import { Popover } from 'antd'
import { useAppStore } from '@/store/appStore'
import { useI18n } from '@/i18n'

function ResourceBar({ label, percent, color }: { label: string; percent: number; color: string }) {
  const isOverload = percent > 85
  const barColor = isOverload ? 'var(--state-error-dark)' : color
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, width: 160, fontSize: 11 }}>
      <span style={{ width: 28, color: 'var(--text-tertiary)', flexShrink: 0 }}>{label}</span>
      <div style={{
        flex: 1,
        height: 4,
        borderRadius: 2,
        background: 'var(--bg-tertiary)',
        overflow: 'hidden',
      }}>
        <div style={{
          width: `${Math.min(100, Math.max(0, percent))}%`,
          height: '100%',
          borderRadius: 2,
          background: barColor,
          transition: 'width 0.5s ease, background 0.3s ease',
        }} />
      </div>
      <span style={{
        width: 32,
        textAlign: 'right',
        color: isOverload ? 'var(--state-error-dark)' : 'var(--text-secondary)',
        flexShrink: 0,
        fontVariantNumeric: 'tabular-nums',
      }}>
        {percent >= 0 ? `${Math.round(percent)}%` : '--'}
      </span>
    </div>
  )
}

export default function SystemMonitor() {
  const systemStats = useAppStore((state) => state.systemStats)
  const { t } = useI18n()

  if (!systemStats) return null

  const popoverContent = (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6, padding: '4px 2px' }}>
      <ResourceBar label="CPU" percent={systemStats.cpu_percent} color="var(--brand-500)" />
      <ResourceBar label={t('内存')} percent={systemStats.memory_percent} color="var(--state-warning-dark)" />
      <ResourceBar label={t('磁盘')} percent={systemStats.disk_percent} color="var(--state-success-dark)" />
    </div>
  )

  return (
    <Popover content={popoverContent} title={null} placement="bottom" trigger="hover">
      <div style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 8,
        fontSize: 11,
        fontFamily: 'var(--font-mono)',
        whiteSpace: 'nowrap',
        cursor: 'pointer',
        userSelect: 'none',
      }}>
        <span style={{ color: 'var(--text-tertiary)' }}>
          CPU <strong style={{ color: systemStats.cpu_percent > 85 ? 'var(--state-error)' : 'var(--text-secondary)' }}>{Math.round(systemStats.cpu_percent)}%</strong>
        </span>
        <span style={{ color: 'var(--text-tertiary)' }}>
          {t('内存')} <strong style={{ color: systemStats.memory_percent > 85 ? 'var(--state-error)' : 'var(--text-secondary)' }}>{Math.round(systemStats.memory_percent)}%</strong>
        </span>
        <span style={{ color: 'var(--text-tertiary)' }}>
          {t('磁盘')} <strong style={{ color: systemStats.disk_percent > 85 ? 'var(--state-error)' : 'var(--text-secondary)' }}>{Math.round(systemStats.disk_percent)}%</strong>
        </span>
      </div>
    </Popover>
  )
}
