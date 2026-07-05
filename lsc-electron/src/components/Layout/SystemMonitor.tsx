import { useAppStore } from '@/store/appStore'

function ResourceBar({ label, percent, color }: { label: string; percent: number; color: string }) {
  const isOverload = percent > 85
  const barColor = isOverload ? 'var(--state-error-dark)' : color
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6, width: '100%', fontSize: 11 }}>
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

  if (!systemStats) return null

  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      gap: 4,
      padding: '6px 8px',
      borderRadius: 6,
      background: 'var(--bg-tertiary)',
      border: '1px solid var(--border-default)',
      width: '100%',
    }}>
      <ResourceBar label="CPU" percent={systemStats.cpu_percent} color="var(--brand-500)" />
      <ResourceBar label="内存" percent={systemStats.memory_percent} color="var(--state-warning-dark)" />
      <ResourceBar label="磁盘" percent={systemStats.disk_percent} color="var(--state-success-dark)" />
    </div>
  )
}
