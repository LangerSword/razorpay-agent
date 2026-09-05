export interface Product {
  id: string
  title: string
  category: string
  price_inr: number
  image_url: string
  description: string
  rating: number
  reviews: number
  stock: number
  tags: string[]
  stagnant: boolean
  days_in_stock?: number
}

export interface CartItem {
  name: string
  price: number
  image: string
  category: string
}

export interface BuyerMessage {
  message: string
}

export type AgentStatus = 'idle' | 'active' | 'waiting'

export interface Settings {
  provider: string
  apiKey: string
  baseUrl: string
  model: string
}

export interface PaymentLink {
  id: string
  url: string
  status: string
  amount_paise: number
  session_id: string
}

export interface AppState {
  products: Product[]
  cart: CartItem[]
  running: boolean
  curatedIds: string[]
  totalDonated: number
  buyerStatus: AgentStatus
  buyerText: string
  merchantStatus: AgentStatus
  merchantText: string
  log: LogEntry[]
  toast: string | null
  cartOpen: boolean
  modalProduct: Product | null
  activeFilter: string
  settings: Settings
  settingsOpen: boolean
  paymentLink: PaymentLink | null
}

export interface LogEntry {
  time: string
  message: string
  type: 'system' | 'buyer' | 'merchant' | 'tool'
}

export type Action =
  | { type: 'SET_PRODUCTS'; products: Product[]; curatedIds: string[] }
  | { type: 'ADD_TO_CART'; item: CartItem }
  | { type: 'CLEAR_CART' }
  | { type: 'SET_RUNNING'; running: boolean }
  | { type: 'SET_BUYER_STATUS'; status: AgentStatus; text: string }
  | { type: 'SET_MERCHANT_STATUS'; status: AgentStatus; text: string }
  | { type: 'ADD_LOG'; entry: LogEntry }
  | { type: 'INCREMENT_DONATED' }
  | { type: 'SET_TOAST'; toast: string | null }
  | { type: 'SET_CART_OPEN'; open: boolean }
  | { type: 'SET_MODAL_PRODUCT'; product: Product | null }
  | { type: 'SET_FILTER'; filter: string }
  | { type: 'SET_SETTINGS'; settings: Settings }
  | { type: 'SET_SETTINGS_OPEN'; open: boolean }
  | { type: 'SET_PAYMENT_LINK'; paymentLink: PaymentLink | null }
  | { type: 'RESET' }
