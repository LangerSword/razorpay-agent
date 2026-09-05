import { useState } from 'react'
import { useApp } from '../context/AppContext'
import type { Product } from '../types'

export default function ProductCard({ product }: { product: Product }) {
  const { dispatch } = useApp()
  const [imgError, setImgError] = useState(false)
  const lowStock = product.stock < 10

  return (
    <div
      className="product"
      onClick={() => dispatch({ type: 'SET_MODAL_PRODUCT', product })}
    >
      <div className="product-image">
        {product.image_url && !imgError ? (
          <img
            src={product.image_url}
            alt={product.title}
            loading="lazy"
            onError={() => setImgError(true)}
          />
        ) : (
          <div style={{ width: '100%', height: '100%', background: 'var(--bg-elevated)' }} />
        )}
      </div>
      {lowStock && <span className="product-badge low-stock">Only {product.stock} left</span>}
      {product.stagnant && <span className="product-badge clearance">Clearance</span>}
      <div className="product-info">
        <div className="product-category">{product.category.replace('_', ' ')}</div>
        <div className="product-name">{product.title}</div>
        <div className="product-desc">{product.description}</div>
        <div className="product-meta">
          <div className="product-rating">
            <span className="product-rating-star">★</span> {product.rating}
          </div>
          <div className="product-reviews">({product.reviews})</div>
        </div>
        <div className="product-tags">
          {product.tags.map(t => (
            <span key={t} className="product-tag">{t}</span>
          ))}
        </div>
        <div className="product-footer">
          <div className="product-price">₹{product.price_inr.toFixed(0)}</div>
          <div className={`product-stock ${lowStock ? 'low' : ''}`}>
            {lowStock ? 'Low stock' : 'In stock'}
          </div>
        </div>
      </div>
    </div>
  )
}
