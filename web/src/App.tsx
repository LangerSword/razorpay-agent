import { useState } from 'react'
import { useApp } from './context/AppContext'
import Header from './components/Header'
import Hero from './components/Hero'
import MissionSection from './components/MissionSection'
import AgentsBar from './components/AgentsBar'
import MainContent from './components/MainContent'
import Sidebar from './components/Sidebar'
import CartPanel from './components/CartPanel'
import ProductModal from './components/ProductModal'
import SettingsPanel from './components/SettingsPanel'
import Toast from './components/Toast'

export default function App() {
  const { state, dispatch, startDemo } = useApp()
  const [interests, setInterests] = useState('home, kitchen')
  const [budget, setBudget] = useState(10000)
  const [patience, setPatience] = useState(5)
  const [style, setStyle] = useState('analytical')
  const [demoAutoPay, setDemoAutoPay] = useState(true)

  const handleStart = () => {
    if (state.running) return
    startDemo({
      interests,
      budget,
      patience,
      demoAutoPay,
      style,
    })
  }

  const handleReset = () => {
    dispatch({ type: 'RESET' })
    setInterests('home, kitchen')
    setBudget(10000)
    setPatience(5)
    setStyle('analytical')
    setDemoAutoPay(true)
  }

  return (
    <>
      <div className="app">
        <div className="stage">
          <Header />
          <Hero totalDonated={state.totalDonated} />
          <MissionSection />
          <AgentsBar
            buyerStatus={state.buyerStatus}
            buyerText={state.buyerText}
            merchantStatus={state.merchantStatus}
            merchantText={state.merchantText}
          />
          <MainContent />
        </div>
        <Sidebar
          interests={interests}
          budget={budget}
          patience={patience}
          style={style}
          demoAutoPay={demoAutoPay}
          running={state.running}
          cartCount={state.cart.length}
          onInterestsChange={setInterests}
          onBudgetChange={setBudget}
          onPatienceChange={setPatience}
          onStyleChange={setStyle}
          onDemoAutoPayChange={setDemoAutoPay}
          onStart={handleStart}
          onReset={handleReset}
          onOpenCart={() => dispatch({ type: 'SET_CART_OPEN', open: true })}
        />
      </div>
      <CartPanel />
      <ProductModal />
      {state.settingsOpen && (
        <SettingsPanel onClose={() => dispatch({ type: 'SET_SETTINGS_OPEN', open: false })} />
      )}
      <Toast />
    </>
  )
}
