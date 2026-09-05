import { useState } from 'react'
import { useApp } from '../context/AppContext'

interface Settings {
  provider: string
  apiKey: string
  baseUrl: string
  model: string
}

const DEFAULT_SETTINGS: Settings = {
  provider: 'openai',
  apiKey: '',
  baseUrl: 'https://api.openai.com/v1',
  model: 'gpt-4o-mini',
}

const PROVIDERS: Record<string, { label: string; baseUrl: string; model: string }> = {
  openai: { label: 'OpenAI', baseUrl: 'https://api.openai.com/v1', model: 'gpt-4o-mini' },
  anthropic: { label: 'Anthropic', baseUrl: 'https://api.anthropic.com/v1', model: 'claude-sonnet-4-20250514' },
  nous: { label: 'Nous Portal', baseUrl: 'https://portal.nousresearch.com/api', model: 'Hermes-3-Llama-3.1-405B' },
  custom: { label: 'Custom', baseUrl: '', model: '' },
}

export default function SettingsPanel({ onClose }: { onClose: () => void }) {
  const { dispatch } = useApp()
  const [settings, setSettings] = useState<Settings>(() => {
    const saved = localStorage.getItem('common_settings')
    return saved ? JSON.parse(saved) : DEFAULT_SETTINGS
  })
  const [saved, setSaved] = useState(false)

  const handleSave = () => {
    localStorage.setItem('common_settings', JSON.stringify(settings))
    setSaved(true)
    setTimeout(() => setSaved(false), 2000)
    dispatch({ type: 'SET_SETTINGS', settings })
  }

  const handleProviderChange = (provider: string) => {
    const p = PROVIDERS[provider]
    setSettings({
      ...settings,
      provider,
      baseUrl: p.baseUrl,
      model: p.model,
    })
  }

  return (
    <div className="modal-overlay active" onClick={onClose}>
      <div className="modal" onClick={e => e.stopPropagation()} style={{ maxWidth: '480px' }}>
        <button className="modal-close" onClick={onClose}>✕</button>
        <div style={{ padding: '24px' }}>
          <h2 style={{ fontSize: '18px', fontWeight: 600, marginBottom: '4px' }}>Settings</h2>
          <p style={{ fontSize: '12px', color: 'var(--ink-subtle)', marginBottom: '20px' }}>
            Plug your own API key to power the AI agents. Keys are stored locally in your browser.
          </p>

          <div className="control-row" style={{ marginBottom: '12px' }}>
            <label>Provider</label>
            <select
              value={settings.provider}
              onChange={e => handleProviderChange(e.target.value)}
            >
              {Object.entries(PROVIDERS).map(([key, p]) => (
                <option key={key} value={key}>{p.label}</option>
              ))}
            </select>
          </div>

          <div className="control-row" style={{ marginBottom: '12px' }}>
            <label>API Key</label>
            <input
              type="password"
              value={settings.apiKey}
              onChange={e => setSettings({ ...settings, apiKey: e.target.value })}
              placeholder="sk-..."
              style={{ fontFamily: 'var(--font-mono)' }}
            />
          </div>

          {settings.provider === 'custom' && (
            <div className="control-row" style={{ marginBottom: '12px' }}>
              <label>Base URL</label>
              <input
                type="text"
                value={settings.baseUrl}
                onChange={e => setSettings({ ...settings, baseUrl: e.target.value })}
                placeholder="https://api.example.com/v1"
              />
            </div>
          )}

          <div className="control-row" style={{ marginBottom: '16px' }}>
            <label>Model</label>
            <input
              type="text"
              value={settings.model}
              onChange={e => setSettings({ ...settings, model: e.target.value })}
              placeholder="gpt-4o-mini"
            />
          </div>

          <div style={{ display: 'flex', gap: '8px' }}>
            <button className="btn btn-primary" onClick={handleSave}>
              {saved ? '✓ Saved' : 'Save Settings'}
            </button>
            <button className="btn btn-secondary" onClick={onClose}>
              Cancel
            </button>
          </div>

          <div style={{ marginTop: '16px', padding: '10px', background: 'var(--bg-elevated)', borderRadius: 'var(--radius-sm)' }}>
            <p style={{ fontSize: '10px', color: 'var(--ink-subtle)', lineHeight: 1.5 }}>
              <strong>BYOK:</strong> Your key is sent with each request to power the buyer and merchant agents. 
              It is never stored on the server. Without a key, agents use a built-in scripted fallback.
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}
