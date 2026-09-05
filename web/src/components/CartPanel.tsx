import { useApp } from '../context/AppContext'

export default function CartPanel() {
  const { state, dispatch } = useApp()
  const { cart, cartOpen } = state

  const total = cart.reduce((s, i) => s + i.price, 0)

  return (
    <>
      <div
        className={`cart-overlay ${cartOpen ? 'active' : ''}`}
        onClick={() => dispatch({ type: 'SET_CART_OPEN', open: false })}
      />
      <div className={`cart-panel ${cartOpen ? 'active' : ''}`}>
        <div className="cart-header">
          <div className="cart-title">Your Cart</div>
          <button className="cart-close" onClick={() => dispatch({ type: 'SET_CART_OPEN', open: false })}>✕</button>
        </div>
        <div className="cart-items">
          {cart.length === 0 ? (
            <div className="cart-empty">Your cart is empty</div>
          ) : (
            cart.map((item, i) => (
              <div key={i} className="cart-item">
                <div className="cart-item-image">
                  <div style={{ width: '100%', height: '100%', background: 'var(--bg-elevated)' }} />
                </div>
                <div className="cart-item-info">
                  <div className="cart-item-name">{item.name}</div>
                  <div className="cart-item-price">₹{item.price.toFixed(0)}</div>
                </div>
              </div>
            ))
          )}
        </div>
        <div className="cart-footer">
          <div className="cart-total-row">
            <span className="cart-total-label">Total</span>
            <span className="cart-total-value">₹{total.toLocaleString('en-IN')}</span>
          </div>
          <button
            className="btn btn-primary"
            onClick={() => {
              dispatch({ type: 'SET_CART_OPEN', open: false })
              alert('Demo only — agent handles checkout')
            }}
          >
            Checkout
          </button>
        </div>
      </div>
    </>
  )
}
