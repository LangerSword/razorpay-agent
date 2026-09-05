import { useApp } from '../context/AppContext'

export default function ProductModal() {
  const { state, dispatch } = useApp()
  const product = state.modalProduct

  if (!product) return null

  return (
    <div
      className="modal-overlay active"
      onClick={() => dispatch({ type: 'SET_MODAL_PRODUCT', product: null })}
    >
      <div className="modal" onClick={e => e.stopPropagation()}>
        <button className="modal-close" onClick={() => dispatch({ type: 'SET_MODAL_PRODUCT', product: null })}>✕</button>
        <div className="modal-grid">
          <div className="modal-image">
            {product.image_url ? (
              <img src={product.image_url} alt={product.title} />
            ) : (
              <div style={{ width: '100%', height: '100%', background: 'var(--bg-elevated)' }} />
            )}
          </div>
          <div className="modal-details">
            <div className="modal-category">{product.category.replace('_', ' ')}</div>
            <h2 className="modal-title">{product.title}</h2>
            <div className="modal-rating">
              <span className="modal-rating-stars">{'★'.repeat(Math.round(product.rating))}{'☆'.repeat(5 - Math.round(product.rating))}</span>
              <span>{product.rating} ({product.reviews} reviews)</span>
            </div>
            <p className="modal-desc">{product.description}</p>
            <div className="modal-tags">
              {product.tags.map(t => (
                <span key={t} className="modal-tag">{t}</span>
              ))}
            </div>
            <div className="modal-price-row">
              <div>
                <div className="modal-price">₹{product.price_inr.toFixed(0)}</div>
                <div className={`modal-stock ${product.stock < 10 ? 'low' : 'in-stock'}`}>
                  {product.stock < 10 ? `Only ${product.stock} left` : 'In stock'}
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
