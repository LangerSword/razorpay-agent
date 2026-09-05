export default function Header() {
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
        <span className="ts">{now}</span>
      </div>
    </header>
  )
}
