import { useApp } from '../context/AppContext'
import ProductCard from './ProductCard'

const FILTERS = ['all', 'home', 'kitchen', 'apparel', 'personal_care', 'stationery']

export default function MainContent() {
  const { state, dispatch } = useApp()

  const filteredProducts = state.activeFilter === 'all'
    ? state.products
    : state.products.filter(p => p.category === state.activeFilter)

  if (state.products.length === 0) {
    return (
      <main className="main">
        <div className="empty-state">
          <div className="icon">🤖</div>
          <div className="title">Ready to shop</div>
          <div className="desc">
            Configure your buyer in the sidebar and click Start.<br />
            The buyer shops, the merchant negotiates, and Razorpay settles.
          </div>
        </div>
      </main>
    )
  }

  return (
    <main className="main">
      <div className="filters">
        {FILTERS.map(f => (
          <button
            key={f}
            className={`filter-btn ${state.activeFilter === f ? 'active' : ''}`}
            onClick={() => dispatch({ type: 'SET_FILTER', filter: f })}
          >
            {f === 'all' ? 'All' : f.replace('_', ' ')}
          </button>
        ))}
      </div>
      <div className="products">
        {filteredProducts.map(p => (
          <ProductCard key={p.id} product={p} />
        ))}
      </div>
    </main>
  )
}
