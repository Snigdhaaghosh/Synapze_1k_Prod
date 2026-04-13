import { create } from 'zustand'
import { persist } from 'zustand/middleware'

// ── Auth store ─────────────────────────────────────────────────────────────
export const useAuthStore = create(
  persist(
    (set, get) => ({
      accessToken: null,
      refreshToken: null,
      user: null,
      // Separate setters for access vs refresh token
      setTokens: (accessToken, refreshToken, user) =>
        set({ accessToken, refreshToken, user }),
      setAccessToken: (accessToken) =>
        set({ accessToken }),
      setToken: (token, user) =>
        set({ accessToken: token, user }),  // backward compat
      clear: () =>
        set({ accessToken: null, refreshToken: null, user: null }),
    }),
    { name: 'synapze-auth' }
  )
)

// ── Chat store ─────────────────────────────────────────────────────────────
export const useChatStore = create((set, get) => ({
  sessions: [],
  activeSessionId: null,
  messages: {},
  streaming: false,
  activeTools: [],
  abortController: null,

  newSession: () => {
    const id = crypto.randomUUID()
    const session = { id, label: `Session ${new Date().toLocaleTimeString()}`, createdAt: Date.now() }
    set(s => ({
      sessions: [session, ...s.sessions],
      activeSessionId: id,
      messages: { ...s.messages, [id]: [] },
    }))
    return id
  },

  setActiveSession: (id) => set({ activeSessionId: id }),

  renameSession: (id, label) => set(s => ({
    sessions: s.sessions.map(s => s.id === id ? { ...s, label } : s)
  })),

  deleteSession: (id) => set(s => {
    const sessions = s.sessions.filter(sess => sess.id !== id)
    const messages = { ...s.messages }
    delete messages[id]
    return {
      sessions,
      messages,
      activeSessionId: s.activeSessionId === id
        ? (sessions[0]?.id ?? null)
        : s.activeSessionId,
    }
  }),

  addMessage: (sessionId, msg) => set(s => ({
    messages: { ...s.messages, [sessionId]: [...(s.messages[sessionId] ?? []), msg] }
  })),

  updateLastAssistant: (sessionId, text) => set(s => {
    const msgs = [...(s.messages[sessionId] ?? [])]
    const last = msgs.findLastIndex(m => m.role === 'assistant' && m.streaming)
    if (last >= 0) msgs[last] = { ...msgs[last], content: text }
    return { messages: { ...s.messages, [sessionId]: msgs } }
  }),

  updateStreamingMsg: (sessionId, updater) => set(s => {
    const msgs = [...(s.messages[sessionId] ?? [])]
    const last = msgs.findLastIndex(m => m.streaming)
    if (last >= 0) msgs[last] = updater(msgs[last])
    return { messages: { ...s.messages, [sessionId]: msgs } }
  }),

  setStreaming: (v) => set({ streaming: v }),
  setAbortController: (ac) => set({ abortController: ac }),

  cancelStreaming: () => {
    const { abortController } = get()
    abortController?.abort()
    set(s => {
      const msgs = Object.fromEntries(
        Object.entries(s.messages).map(([sid, msgs]) => [
          sid,
          msgs.map(m => m.streaming ? { ...m, streaming: false, content: m.content + ' [cancelled]' } : m)
        ])
      )
      return { streaming: false, abortController: null, messages: msgs }
    })
  },
}))
