import { useApp } from '../context/AppContext'

interface Props {
  interests: string
  budget: number
  patience: number
  style: string
  demoAutoPay: boolean
  running: boolean
  cartCount: number
  onInterestsChange: (v: string) => void
  onBudgetChange: (v: number) => void
  onPatienceChange: (v: number) => void
  onStyleChange: (v: string) => void
  onDemoAutoPayChange: (v: boolean) => void
  onStart: () => void
  onReset: () => void
  onOpenCart: () => void
}

export default function Sidebar({
  interests, budget, patience, style, demoAutoPay, running, cartCount,
  onInterestsChange, onBudgetChange, onPatienceChange, onStyleChange,
  onDemoAutoPayChange, onStart, onReset, onOpenCart
}: Props) {
  const { state } = useApp()

  return (
    <aside className="sidebar">
      <div className="sidebar-header">
        <div className="sidebar-title">Agent Panel</div>
        <div className="sidebar-subtitle">Configure & control</div>
      </div>
      <div className="sidebar-body">
        <div className="controls">
          <div className="control-row">
            <label htmlFor="interests">Interests</label>
            <input id="interests" type="text" value={interests} onChange={e => onInterestsChange(e.target.value)} placeholder="apparel, kitchen, home" />
          </div>
          <div className="control-row">
            <label htmlFor="budget">Budget (₹)</label>
            <input id="budget" type="number" value={budget} min={1000} step={500} onChange={e => onBudgetChange(Number(e.target.value))} />
          </div>
          <div className="control-row">
            <label htmlFor="patience">Patience</label>
            <input id="patience" type="number" value={patience} min={3} max={15} step={1} onChange={e => onPatienceChange(Number(e.target.value))} />
          </div>
          <div className="control-row">
            <label htmlFor="style">Style</label>
            <select id="style" value={style} onChange={e => onStyleChange(e.target.value)}>
              <option value="analytical">Analytical — careful</option>
              <option value="casual">Casual — relaxed</option>
              <option value="aggressive">Aggressive — deal hunter</option>
              <option value="passive">Passive — quick</option>
            </select>
          </div>
          <div className="control-check">
            <input type="checkbox" id="demoAutoPay" checked={demoAutoPay} onChange={e => onDemoAutoPayChange(e.target.checked)} />
            <label htmlFor="demoAutoPay">Auto-pay (demo)</label>
          </div>
        </div>

        <button className="btn btn-primary" onClick={onStart} disabled={running}>
          ▶ Start Shopping
        </button>
        <div style={{ height: 8 }} />
        <button className="btn btn-secondary" onClick={onOpenCart}>
          🛒 Cart (<span>{cartCount}</span>)
        </button>
        <div style={{ height: 8 }} />
        <button className="btn btn-danger" onClick={onReset}>↺ Reset</button>

        {state.paymentLink && (
          <div style={{ marginTop: 16, padding: 12, background: 'rgba(45,90,61,0.08)', borderRadius: 8 }}>
            <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--accent)', marginBottom: 6 }}>PAYMENT READY</div>
            <a href={state.paymentLink.url} target="_blank" rel="noopener noreferrer" className="btn btn-primary" style={{ fontSize: 12, padding: '8px 12px' }}>
              💳 Pay ₹{(state.paymentLink.amount_paise / 100).toFixed(0)}
            </a>
          </div>
        )}

        <div style={{ marginTop: 24 }}>
          <div style={{ fontSize: 10, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 0.8, color: 'var(--ink-subtle)', marginBottom: 8 }}>
            Live Reasoning
          </div>
          <div className="log">
            {state.log.length === 0 ? (
              <div className="log-entry system">
                <span className="time">--:--:--</span>
                Ready
              </div>
            ) : (
              state.log.map((entry, i) => (
                <div key={i} className={`log-entry ${entry.type}`}>
                  <span className="time">{entry.time}</span>
                  {entry.message}
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </aside>
  )
}
