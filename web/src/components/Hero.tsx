interface Props {
  totalDonated: number
}

export default function Hero({ totalDonated }: Props) {
  return (
    <section className="hero">
      <div className="hero-inner">
        <h1>Everyday essentials, elevated.</h1>
        <p>
          Discover curated goods with our AI buyer. Watch it browse, reason, and find the perfect items — all in real time.
        </p>
        <div className="hero-stats">
          <div className="hero-stat">
            <div className="hero-stat-value">{15}</div>
            <div className="hero-stat-label">Products</div>
          </div>
          <div className="hero-stat">
            <div className="hero-stat-value">{2}</div>
            <div className="hero-stat-label">AI Agents</div>
          </div>
          <div className="hero-stat">
            <div className="hero-stat-value">{totalDonated.toLocaleString('en-US')}</div>
            <div className="hero-stat-label">Orders</div>
          </div>
        </div>
      </div>
    </section>
  )
}
