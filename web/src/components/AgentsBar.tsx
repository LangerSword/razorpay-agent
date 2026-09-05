import type { AgentStatus } from '../types'

interface Props {
  buyerStatus: AgentStatus
  buyerText: string
  merchantStatus: AgentStatus
  merchantText: string
}

export default function AgentsBar({ buyerStatus, buyerText, merchantStatus, merchantText }: Props) {
  return (
    <div className="agents-bar">
      <div className={`agent-pill buyer ${buyerStatus === 'active' ? 'thinking' : ''}`}>
        <span className="agent-pill-icon">🛒</span>
        <div className="agent-pill-text">
          <div className="agent-pill-name">AutoBuyer</div>
          <div className="agent-pill-status">{buyerText}</div>
        </div>
        <span className={`agent-pill-badge ${buyerStatus}`}>
          {buyerStatus.toUpperCase()}
        </span>
      </div>
      <div className={`agent-pill merchant ${merchantStatus === 'active' ? 'thinking' : ''}`}>
        <span className="agent-pill-icon">🏪</span>
        <div className="agent-pill-text">
          <div className="agent-pill-name">MerchantAgent</div>
          <div className="agent-pill-status">{merchantText}</div>
        </div>
        <span className={`agent-pill-badge ${merchantStatus}`}>
          {merchantStatus.toUpperCase()}
        </span>
      </div>
    </div>
  )
}
