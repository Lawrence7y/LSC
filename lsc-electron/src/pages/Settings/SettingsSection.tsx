import type { CSSProperties, ReactNode } from 'react'

export function SettingsSection({
  id,
  title,
  extra,
  children,
  bodyStyle,
}: {
  id: string
  title: string
  extra?: ReactNode
  children: ReactNode
  /** 覆盖默认卡片 body（如 Cookie / 日志需要内边距） */
  bodyStyle?: CSSProperties
}) {
  return (
    <div id={id} className="settings-section">
      <div className="settings-section__title">
        <span>{title}</span>
        {extra}
      </div>
      <div className="settings-section__body" style={bodyStyle}>
        {children}
      </div>
    </div>
  )
}
