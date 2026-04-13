import { useAuthStore } from './store'

const BASE = 'https://synapze-1k-prod-1.onrender.com'

// ── Token refresh ──────────────────────────────────────────────────────────

let _refreshPromise = null   // deduplicate concurrent refresh attempts

async function refreshAccessToken() {
  if (_refreshPromise) return _refreshPromise

  _refreshPromise = (async () => {
    const refreshToken = useAuthStore.getState().refreshToken
    if (!refreshToken) throw new Error('No refresh token')

    const res = await fetch('/auth/refresh', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refreshToken }),
    })

    if (!res.ok) {
      useAuthStore.getState().clear()
      window.location.href = '/auth'
      throw new Error('Refresh failed')
    }

    const data = await res.json()
    useAuthStore.getState().setAccessToken(data.access_token)
    return data.access_token
  })()

  try {
    return await _refreshPromise
  } finally {
    _refreshPromise = null
  }
}

// ── Headers ────────────────────────────────────────────────────────────────

function headers(extra = {}) {
  const token = useAuthStore.getState().accessToken
  return {
    'Content-Type': 'application/json',
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...extra,
  }
}

// ── Request with auto-refresh ──────────────────────────────────────────────

async function request(method, path, body, retry = true) {
  const res = await fetch(BASE + path, {
    method,
    headers: headers(),
    body: body ? JSON.stringify(body) : undefined,
  })

  // Auto-refresh on 401 (expired access token)
  if (res.status === 401 && retry) {
    try {
      await refreshAccessToken()
      return request(method, path, body, false)  // retry once
    } catch {
      useAuthStore.getState().clear()
      window.location.href = '/auth'
      throw new Error('Session expired')
    }
  }

  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    const message = err.message || err.detail || `HTTP ${res.status}`
    throw Object.assign(new Error(message), { status: res.status, code: err.error })
  }

  return res.json()
}

// ── API surface ────────────────────────────────────────────────────────────

export const api = {
  health:      ()       => request('GET',    '/health'),
  me:          ()       => request('GET',    '/auth/me'),
  logout:      ()       => request('POST',   '/auth/logout'),
  chat:        (body)   => request('POST',   '/agent/chat', body),
  sessions:    ()       => request('GET',    '/agent/sessions'),
  history:     (sid)    => request('GET',    `/agent/sessions/${sid}/history`),
  clear:       (sid)    => request('DELETE', `/agent/sessions/${sid}`),
  schedule:    (body)   => request('POST',   '/tasks/schedule', body),
  taskStatus:  (id)     => request('GET',    `/tasks/${id}`),
  cancelTask:  (id)     => request('DELETE', `/tasks/${id}`),
}

// ── Streaming fetch ────────────────────────────────────────────────────────

export async function* streamChat(message, sessionId, { signal } = {}) {
  let token = useAuthStore.getState().accessToken

  const makeRequest = async (t) => fetch(`${BASE}/agent/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${t}`,
    },
    body: JSON.stringify({ message, session_id: sessionId }),
    signal,
  })

  let res = await makeRequest(token)

  // Auto-refresh on 401
  if (res.status === 401) {
    try {
      token = await refreshAccessToken()
      res = await makeRequest(token)
    } catch {
      useAuthStore.getState().clear()
      window.location.href = '/auth'
      return
    }
  }

  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.message || `HTTP ${res.status}`)
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() ?? ''
      for (const line of lines) {
        if (!line.trim()) continue
        try { yield JSON.parse(line) }
        catch { /* skip malformed */ }
      }
    }
  } catch (err) {
    if (err.name !== 'AbortError') throw err
  } finally {
    reader.releaseLock()
  }
}
