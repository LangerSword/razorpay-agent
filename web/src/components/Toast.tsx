import { useApp } from '../context/AppContext'

export default function Toast() {
  const { state } = useApp()

  return (
    <div className={`toast ${state.toast ? 'show' : ''}`}>
      {state.toast}
    </div>
  )
}
