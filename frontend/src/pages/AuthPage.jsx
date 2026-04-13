import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuthStore } from '../lib/store'
import { api } from '../lib/api'

const ASCII = `
 ███████╗██╗   ██╗███╗  ██╗ █████╗ ██████╗ ███████╗███████╗
 ██╔════╝╚██╗ ██╔╝████╗ ██║██╔══██╗██╔══██╗╚══███╔╝██╔════╝
 ███████╗ ╚████╔╝ ██╔██╗██║███████║██████╔╝  ███╔╝ █████╗
 ╚════██║  ╚██╔╝  ██║╚████║██╔══██║██╔═══╝  ███╔╝  ██╔══╝
 ███████║   ██║   ██║ ╚███║██║  ██║██║     ███████╗███████╗
 ╚══════╝   ╚═╝   ╚═╝  ╚══╝╚═╝  ╚═╝╚═╝     ╚══════╝╚══════╝`.trim()

export default function AuthPage() {
  const [mode, setMode]       = useState('google')  // 'google' | 'token'
  const [token, setToken]     = useState('')
  const [error, setError]     = useState('')
  const [loading, setLoading] = useState(false)
  const { setTokens, setToken: setLegacyToken } = useAuthStore()
  const navigate = useNavigate()

  // ── Google OAuth flow ────────────────────────────────────────────────────
  // After the server redirects back with tokens, the response JSON lands here
  // via a deep-link. For now the user pastes the access+refresh pair.
  // In production, wire up a proper redirect handler.

  async function handleTokenSubmit(e) {
    e.preventDefault()
    if (!token.trim()) return
    setLoading(true)
    setError('')

    try {
      // Try to parse as JSON (access_token + refresh_token response)
      let accessToken = token.trim()
      let refreshToken = null

      if (token.trim().startsWith('{')) {
        const parsed = JSON.parse(token.trim())
        accessToken  = parsed.access_token || parsed.token
        refreshToken = parsed.refresh_token || null
      }

      // Temporarily set to verify
      useAuthStore.getState().setTokens(accessToken, refreshToken, null)
      const user = await api.me()
      setTokens(accessToken, refreshToken, user)
      navigate('/')
    } catch (err) {
      useAuthStore.getState().clear()
      if (err.message.includes('JSON')) {
        setError('Invalid JSON format. Paste the full response or just the access_token.')
      } else {
        setError('Invalid or expired token. Please re-authenticate with Google.')
      }
    } finally {
      setLoading(false)
    }
  }

  function openGoogleAuth() {
    window.open(`${window.location.origin}/auth/google`, '_blank')
    setMode('token')
  }

  return (
    <div className="min-h-screen bg-bg flex flex-col items-center justify-center px-4">

      <pre className="text-accent font-mono text-[7px] sm:text-[9px] leading-tight mb-8 select-none opacity-80">
        {ASCII}
      </pre>

      <p className="text-subtle font-mono text-xs mb-10 tracking-widest uppercase">
        autonomous ai co-worker · enterprise
      </p>

      <div className="w-full max-w-sm">
        {/* Mode tabs */}
        <div className="flex border border-border rounded-lg overflow-hidden mb-6 font-mono text-xs">
          {[['google', 'google oauth'], ['token', 'paste token']].map(([k, label]) => (
            <button
              key={k}
              onClick={() => setMode(k)}
              className={`flex-1 py-2.5 transition-colors ${
                mode === k
                  ? 'bg-surface text-accent border-r border-border'
                  : 'text-muted hover:text-text'
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        {/* Google OAuth tab */}
        {mode === 'google' && (
          <div className="space-y-4">
            <div className="bg-surface border border-border rounded-lg p-4 space-y-2">
              <p className="text-subtle font-mono text-xs leading-relaxed">
                1. Click below to open Google auth in a new tab<br />
                2. Authenticate with your Google account<br />
                3. Copy the <span className="text-accent">access_token</span> from the JSON response<br />
                4. Switch to "paste token" tab and paste it
              </p>
            </div>

            {/*<button
              onClick={openGoogleAuth}
              className="w-full bg-accent/10 hover:bg-accent/20 border border-accent/30 hover:border-accent/60 text-accent font-mono text-xs py-3 rounded-lg transition-all"
            >
              open google auth ↗
            </button>*/}

            <button
              onClick={() => setMode('token')}
              className="w-full text-muted hover:text-text font-mono text-xs py-2 transition-colors"
            >
              i have a token → paste it
            </button>
          </div>
        )}

        {/* Token paste tab */}
        {mode === 'token' && (
          <form onSubmit={handleTokenSubmit} className="space-y-3">
            <div className="space-y-1">
              <label className="font-mono text-[10px] text-muted">
                paste access_token or full JSON response
              </label>
              <div className="relative">
                <span className="absolute left-3 top-3 text-accent font-mono text-xs select-none">$</span>
                <textarea
                  value={token}
                  onChange={e => setToken(e.target.value)}
                  placeholder='eyJhbGci... or {"access_token":"...","refresh_token":"..."}'
                  rows={4}
                  className="w-full bg-surface border border-border rounded-lg pl-7 pr-4 py-2.5 font-mono text-xs text-text placeholder-muted focus:outline-none focus:border-accent transition-colors resize-none"
                  autoFocus
                  spellCheck={false}
                />
              </div>
            </div>

            {error && (
              <div className="bg-red/10 border border-red/30 rounded-lg px-3 py-2">
                <p className="text-red font-mono text-xs">{error}</p>
              </div>
            )}

            <button
              type="submit"
              disabled={loading || !token.trim()}
              className="w-full bg-accent/10 hover:bg-accent/20 border border-accent/30 hover:border-accent/60 text-accent font-mono text-xs py-3 rounded-lg transition-all disabled:opacity-30 disabled:cursor-not-allowed"
            >
              {loading ? 'verifying...' : 'connect →'}
            </button>

            <button
              type="button"
              onClick={() => setMode('google')}
              className="w-full text-muted hover:text-accent font-mono text-xs py-2 transition-colors"
            >
              ← back to google auth
            </button>
          </form>
        )}
      </div>

      <p className="mt-12 text-muted font-mono text-[10px]">
        synapze enterprise · your data stays on your server
      </p>
    </div>
  )
}
