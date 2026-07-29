import type { ReactNode } from 'react'

export function SettingsRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="settings-row">
      <span className="settings-row__label">{label}</span>
      <div className="settings-row__control">{children}</div>
    </div>
  )
}
