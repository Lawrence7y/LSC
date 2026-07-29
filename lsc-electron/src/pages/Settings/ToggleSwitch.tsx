export function ToggleSwitch({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="settings-toggle">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="settings-toggle__input"
      />
      <span className={`settings-toggle__track${checked ? ' is-on' : ''}`}>
        <span className="settings-toggle__thumb" />
      </span>
    </label>
  )
}
