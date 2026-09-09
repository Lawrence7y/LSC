import type { CSSProperties, ReactNode } from 'react'

export function SettingsSection({
  id,
  title,
  description,
  extra,
  children,
  bodyStyle,
}: {
  id: string
  title: string
  description?: string
  extra?: ReactNode
  children: ReactNode
  /** 覆盖默认卡片 body（如 Cookie / 日志需要内边距） */
  bodyStyle?: CSSProperties
}) {
  return (
    <div id={id} className="settings-section">
      <div className="settings-section__header">
        <div className="settings-section__title-wrap">
          <span className="settings-section__title">{title}</span>
          {description && <span className="settings-section__desc">{description}</span>}
        </div>
        {extra && <div className="settings-section__extra">{extra}</div>}
      </div>
      <div className="settings-section__body" style={bodyStyle}>
        {children}
      </div>
    </div>
  )
}
