import { useApp } from '../context/AppContext'

export default function Header() {
  const { dispatch } = useApp()
  const now = new Date().toLocaleTimeString('en-US', { hour12: false })

  return (
    <header className="header">
      <div className="brand">
        <div className="brand-logo">C</div>
        <div>
          <div className="brand-name">Common</div>
          <div className="brand-tag">Everyday essentials, elevated.</div>
        </div>
      </div>
      <div className="header-meta">
        <button
          onClick={() => dispatch({ type: 'SET_SETTINGS_OPEN', open: true })}
          style={{
            background: 'none',
            border: '1px solid var(--border)',
            borderRadius: 'var(--radius-sm)',
            padding: '6px 10px',
            fontSize: '11px',
            fontWeight: 500,
            cursor: 'pointer',
            color: 'var(--ink-secondary)',
            transition: 'all 150ms ease',
          }}
        >
          ⚙ Settings
        </button>
        <span className="ts">{now}</span>
      </div>
    </header>
  )
}
