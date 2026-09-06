import { createContext, useContext, useReducer, useCallback, useRef, ReactNode } from 'react'
import type { AppState, Action, Product, CartItem, Settings } from '../types'

const defaultSettings: Settings = {
  provider: 'openai',
  apiKey: '',
  baseUrl: 'https://api.openai.com/v1',
  model: 'gpt-4o-mini',
}

const initialState: AppState = {
  products: [],
  cart: [],
  running: false,
  curatedIds: [],
  totalDonated: 12341,
  buyerStatus: 'idle',
  buyerText: 'Idle',
  merchantStatus: 'idle',
  merchantText: 'Idle',
  log: [],
  toast: null,
  cartOpen: false,
  modalProduct: null,
  activeFilter: 'all',
  settings: defaultSettings,
  settingsOpen: false,
  paymentLink: null,
}

function reducer(state: AppState, action: Action): AppState {
  switch (action.type) {
    case 'SET_PRODUCTS':
      return { ...state, products: action.products, curatedIds: action.curatedIds }
    case 'ADD_TO_CART':
      return { ...state, cart: [...state.cart, action.item] }
    case 'CLEAR_CART':
      return { ...state, cart: [] }
    case 'SET_RUNNING':
      return { ...state, running: action.running }
    case 'SET_BUYER_STATUS':
      return { ...state, buyerStatus: action.status, buyerText: action.text }
    case 'SET_MERCHANT_STATUS':
      return { ...state, merchantStatus: action.status, merchantText: action.text }
    case 'ADD_LOG':
      return { ...state, log: [...state.log, action.entry] }
    case 'INCREMENT_DONATED':
      return { ...state, totalDonated: state.totalDonated + 1 }
    case 'SET_TOAST':
      return { ...state, toast: action.toast }
    case 'SET_CART_OPEN':
      return { ...state, cartOpen: action.open }
    case 'SET_MODAL_PRODUCT':
      return { ...state, modalProduct: action.product }
    case 'SET_FILTER':
      return { ...state, activeFilter: action.filter }
    case 'SET_SETTINGS':
      return { ...state, settings: action.settings }
    case 'SET_SETTINGS_OPEN':
      return { ...state, settingsOpen: action.open }
    case 'SET_PAYMENT_LINK':
      return { ...state, paymentLink: action.paymentLink }
    case 'RESET':
      return { ...initialState }
    default:
      return state
  }
}

interface AppContextValue {
  state: AppState
  dispatch: React.Dispatch<Action>
  startDemo: (params: {
    interests: string
    budget: number
    patience: number
    demoAutoPay: boolean
    style: string
  }) => Promise<void>
}

const AppContext = createContext<AppContextValue | null>(null)

export function AppProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(reducer, initialState)
  const toastTimeout = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)

  const showToast = useCallback((msg: string) => {
    dispatch({ type: 'SET_TOAST', toast: msg })
    if (toastTimeout.current) clearTimeout(toastTimeout.current)
    toastTimeout.current = setTimeout(() => {
      dispatch({ type: 'SET_TOAST', toast: null })
    }, 2400)
  }, [])

  const startDemo = useCallback(async (params: {
    interests: string
    budget: number
    patience: number
    demoAutoPay: boolean
    style: string
  }) => {
    dispatch({ type: 'SET_RUNNING', running: true })
    dispatch({ type: 'SET_PAYMENT_LINK', paymentLink: null })

    try {
      // Step 1: Shop greeting (merchant agent curating)
      dispatch({ type: 'SET_MERCHANT_STATUS', status: 'active', text: 'Curating products...' })
      
      const greetRes = await fetch('/api/shop/greet', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          interests: params.interests.split(',').map(s => s.trim()),
          budget: params.budget,
          style: params.style,
        }),
      })
      if (!greetRes.ok) throw new Error('Greeting failed')
      const greetData = await greetRes.json()
      const products: Product[] = greetData.recommendations || []
      dispatch({ type: 'SET_PRODUCTS', products, curatedIds: products.map(p => p.id) })

      const now = new Date().toLocaleTimeString('en-US', { hour12: false })
      dispatch({ type: 'ADD_LOG', entry: { time: now, message: `Shop assistant: ${greetData.greeting}`, type: 'merchant' } })
      
      // Small delay to show merchant thinking
      await new Promise(r => setTimeout(r, 1200))
      dispatch({ type: 'SET_MERCHANT_STATUS', status: 'waiting', text: 'Waiting for buyer' })

      // Step 2: Start buyer agent
      const startRes = await fetch('/api/autonomous/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          interests: params.interests,
          budget: params.budget,
          style: params.style,
          demo_auto_pay: params.demoAutoPay,
          curated_ids: products.map(p => p.id).join(','),
        }),
      })
      if (!startRes.ok) throw new Error('Failed to start')

      let lastCount = 0
      let done = false
      const start = Date.now()

      while (!done && Date.now() - start < 180000) {
        try {
          const res = await fetch('/api/buyer-messages?limit=50')
          if (!res.ok) { await new Promise(r => setTimeout(r, 800)); continue }
          const msgs = await res.json()
          const messages = msgs.messages || []
          for (let i = lastCount; i < messages.length; i++) {
            processMessage(messages[i].message, products, dispatch, showToast)
          }
          lastCount = messages.length
          for (const m of messages) {
            if (m.message.includes('🏁 Complete') || m.message.includes('🏁 No items') || m.message.includes('Payment not received')) done = true
          }
          if (done) break
        } catch {
          // poll retry
        }
        await new Promise(r => setTimeout(r, 1500))
      }
    } catch (err) {
      const now = new Date().toLocaleTimeString('en-US', { hour12: false })
      dispatch({ type: 'ADD_LOG', entry: { time: now, message: `Error: ${(err as Error).message}`, type: 'system' } })
    }
    dispatch({ type: 'SET_RUNNING', running: false })
  }, [showToast])

  return (
    <AppContext.Provider value={{ state, dispatch, startDemo }}>
      {children}
    </AppContext.Provider>
  )
}

function processMessage(
  msg: string,
  products: Product[],
  dispatch: React.Dispatch<Action>,
  showToast: (msg: string) => void
): void {
  const now = new Date().toLocaleTimeString('en-US', { hour12: false })

  if (msg.includes('🔧')) {
    const match = msg.match(/🔧\s*(\w+)\(([^)]+)\)/)
    if (match) dispatch({ type: 'ADD_LOG', entry: { time: now, message: `${match[1]}(${match[2].substring(0, 30)}…)`, type: 'tool' } })
    return
  }

  const evalMatch = msg.match(/💭\s*(.+)/)
  if (evalMatch?.[1] && evalMatch[1].length > 5) {
    dispatch({ type: 'SET_BUYER_STATUS', status: 'active', text: evalMatch[1].substring(0, 40) })
    dispatch({ type: 'ADD_LOG', entry: { time: now, message: `💭 ${evalMatch[1].substring(0, 80)}`, type: 'buyer' } })
    return
  }

  if (msg.includes('🛍️ Added')) {
    const name = msg.split('Added ')[1]?.split(' to cart')[0]
    const p = products.find(prod => prod.title === name)
    if (name) {
      const item: CartItem = { name, price: p?.price_inr ?? 0, image: p?.image_url ?? '', category: p?.category ?? 'home' }
      dispatch({ type: 'ADD_TO_CART', item })
      dispatch({ type: 'INCREMENT_DONATED' })
      showToast(`✓ ${name} added to cart`)
      dispatch({ type: 'ADD_LOG', entry: { time: now, message: `Added: ${name}`, type: 'buyer' } })
    }
    return
  }

  if (msg.includes('❌ Skipping')) {
    const name = msg.split('Skipping ')[1]
    if (name) dispatch({ type: 'ADD_LOG', entry: { time: now, message: `Skipped: ${name}`, type: 'buyer' } })
    return
  }

  if (msg.includes('Checking out')) {
    dispatch({ type: 'SET_MERCHANT_STATUS', status: 'active', text: 'Processing order…' })
    return
  }

  if (msg.includes('Session created')) {
    dispatch({ type: 'SET_MERCHANT_STATUS', status: 'waiting', text: 'Waiting for buyer' })
    return
  }

  if (msg.includes('🤖 Auto-paying')) {
    dispatch({ type: 'ADD_LOG', entry: { time: now, message: 'Auto-paying (demo mode)', type: 'system' } })
    return
  }

  // Payment link for manual payment — extract URL and dispatch
  if (msg.includes('🔗 Payment link:')) {
    // Message format: "🔗 Payment link: <url> (₹XXXX)"
    const urlMatch = msg.match(/🔗 Payment link:\s*([^\s(]+)/)
    const url = urlMatch?.[1]?.trim() ?? ''
    if (url) {
      dispatch({
        type: 'SET_PAYMENT_LINK',
        paymentLink: {
          id: url.split('/').pop()?.split('?')[0] || 'plink_unknown',
          url,
          status: 'created',
          amount_paise: 0,
          session_id: '',
        },
      })
    }
    dispatch({ type: 'ADD_LOG', entry: { time: now, message: `🔗 Payment link ready`, type: 'system' } })
    showToast('Payment link ready - click to pay')
    return
  }

  if (msg.includes('✅ Payment completed')) {
    dispatch({ type: 'SET_BUYER_STATUS', status: 'idle', text: 'Done' })
    dispatch({ type: 'SET_MERCHANT_STATUS', status: 'idle', text: 'Done' })
    const count = msg.match(/(\d+)/)?.[1] ?? '0'
    showToast(`🎉 Order complete — ${count} item${count !== '1' ? 's' : ''} purchased!`)
    dispatch({ type: 'CLEAR_CART' })
    dispatch({ type: 'SET_CART_OPEN', open: true })
    return
  }

  if (msg.includes('Payment not received')) {
    dispatch({ type: 'SET_BUYER_STATUS', status: 'idle', text: 'Payment pending' })
    dispatch({ type: 'SET_MERCHANT_STATUS', status: 'idle', text: 'Waiting' })
    showToast('Payment not received - check payment link')
    return
  }

  if (msg.includes('🏁 Complete')) {
    dispatch({ type: 'SET_BUYER_STATUS', status: 'idle', text: msg })
  }
}

export function useApp(): AppContextValue {
  const ctx = useContext(AppContext)
  if (!ctx) throw new Error('useApp must be used within AppProvider')
  return ctx
}
