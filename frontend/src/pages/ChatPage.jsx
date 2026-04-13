import { useEffect, useRef, useState, useCallback } from 'react'
import { useChatStore, useAuthStore } from '../lib/store'
import { api, streamChat } from '../lib/api'
import Sidebar from '../components/Sidebar'
import Message from '../components/Message'
import InputBar from '../components/InputBar'
import { ActiveToolsBar } from '../components/ToolIndicator'
import clsx from 'clsx'

const SUGGESTIONS = [
  'Read my unread emails and summarize the important ones',
  'What does my calendar look like this week?',
  'Book a 1-hour meeting tomorrow at 3pm',
  'Send a WhatsApp message to +91...',
  'Post in #general on Slack: "Done for today"',
  'Remember that Rahul prefers calls over emails',
]

const HELP_TEXT = `
Synapze Enterprise — Commands

  /new            Start a new session
  /clear          Clear current session history
  /sessions       Load sessions from server
  /schedule       Schedule a future task
  /status         Check server status
  /memory <query> Search saved memories
  /cancel         Cancel current streaming response
  /help           Show this help

Tips:
  • Shift+Enter for new line
  • Tab to autocomplete /commands
  • Esc to cancel a running response
`.trim()

// ── Error Boundary ─────────────────────────────────────────────────────────
import { Component } from 'react'
class ErrorBoundary extends Component {
  state = { error: null }
  static getDerivedStateFromError(error) { return { error } }
  render() {
    if (this.state.error) {
      return (
        <div className="flex items-center justify-center h-full p-8">
          <div className="text-center space-y-3 max-w-sm">
            <p className="font-mono text-sm text-red">Something went wrong</p>
            <p className="font-mono text-xs text-muted">{this.state.error.message}</p>
            <button
              onClick={() => this.setState({ error: null })}
              className="font-mono text-xs text-accent hover:underline"
            >
              try again
            </button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}

// ── Main Page ──────────────────────────────────────────────────────────────
export default function ChatPage() {
  const {
    sessions, activeSessionId, messages,
    streaming, newSession, setActiveSession,
    addMessage, updateLastAssistant, updateStreamingMsg,
    setStreaming, cancelStreaming, setAbortController,
  } = useChatStore()

  const { user, logout: authLogout } = useAuthStore()
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [serverOk, setServerOk]       = useState(null)
  const [loadingHistory, setLoadingHistory] = useState(false)
  const messagesEndRef = useRef(null)

  // Init: ensure session exists
  useEffect(() => {
    if (sessions.length === 0) newSession()
  }, [])

  // Health check on mount
  useEffect(() => {
    api.health()
      .then(() => setServerOk(true))
      .catch(() => setServerOk(false))
  }, [])

  // Auto-scroll
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, activeSessionId])

  // Cancel on Escape key
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape' && streaming) cancelStreaming()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [streaming])

  const currentMessages = activeSessionId ? (messages[activeSessionId] ?? []) : []

  // ── Load history from server for a session ───────────────────────────────
  const loadServerHistory = useCallback(async (sessionId) => {
    setLoadingHistory(true)
    try {
      const data = await api.history(sessionId)
      const msgs = (data.history || []).map((m, i) => ({
        id: `server-${i}`,
        role: m.role,
        content: m.content,
        streaming: false,
        tools: m.tool_calls || [],
        tokens: 0,
      }))
      useChatStore.setState(s => ({
        messages: { ...s.messages, [sessionId]: msgs }
      }))
    } catch (e) {
      console.error('Failed to load history:', e)
    } finally {
      setLoadingHistory(false)
    }
  }, [])

  // ── Send message ──────────────────────────────────────────────────────────
  const handleSend = useCallback(async (text) => {
    if (!activeSessionId || streaming) return

    addMessage(activeSessionId, {
      role: 'user', content: text, id: crypto.randomUUID()
    })

    const assistantId = crypto.randomUUID()
    addMessage(activeSessionId, {
      role: 'assistant', content: '', streaming: true,
      tools: [], tokens: 0, id: assistantId,
    })

    setStreaming(true)

    const ac = new AbortController()
    setAbortController(ac)

    let tools = []

    try {
      for await (const event of streamChat(text, activeSessionId, { signal: ac.signal })) {

        if (event.type === 'text') {
          updateLastAssistant(activeSessionId,
            (useChatStore.getState().messages[activeSessionId]
              ?.findLast(m => m.streaming)?.content ?? '') + event.chunk
          )
        }

        else if (event.type === 'tool_start') {
          tools = [...tools, { name: event.tool, done: false, success: null }]
          updateStreamingMsg(activeSessionId, m => ({ ...m, tools: [...tools] }))
        }

        else if (event.type === 'tool_result') {
          tools = tools.map(t =>
            t.name === event.tool ? { ...t, done: true, success: event.success !== false } : t
          )
          updateStreamingMsg(activeSessionId, m => ({ ...m, tools: [...tools] }))
        }

        else if (event.type === 'done') {
          updateStreamingMsg(activeSessionId, m => ({
            ...m, streaming: false, tokens: event.tokens, tools: [...tools]
          }))
          useChatStore.setState({ streaming: false, abortController: null })
        }

        else if (event.type === 'error') {
          updateStreamingMsg(activeSessionId, m => ({
            ...m, content: event.message || 'An error occurred.', streaming: false
          }))
          useChatStore.setState({ streaming: false, abortController: null })
        }
      }
    } catch (err) {
      if (err.name === 'AbortError') {
        updateStreamingMsg(activeSessionId, m => ({
          ...m,
          content: (m.content || '') + '\n\n*[response cancelled]*',
          streaming: false,
        }))
      } else {
        updateStreamingMsg(activeSessionId, m => ({
          ...m, content: `Error: ${err.message}`, streaming: false
        }))
      }
      useChatStore.setState({ streaming: false, abortController: null })
    }
  }, [activeSessionId, streaming])

  // ── Handle /commands ──────────────────────────────────────────────────────
  const handleCommand = useCallback(async (cmd) => {
    const parts = cmd.trim().split(' ')
    const c = parts[0].toLowerCase()

    const sysMsg = (text) => addMessage(activeSessionId, {
      role: 'assistant', content: text, id: crypto.randomUUID(),
      streaming: false, tools: [], tokens: 0, isSystem: true,
    })

    switch (c) {
      case '/help':
        sysMsg(HELP_TEXT)
        break

      case '/new': {
        const id = newSession()
        setActiveSession(id)
        break
      }

      case '/cancel':
        if (streaming) cancelStreaming()
        else sysMsg('No active response to cancel.')
        break

      case '/clear':
        try {
          await api.clear(activeSessionId)
          useChatStore.setState(s => ({
            messages: { ...s.messages, [activeSessionId]: [] }
          }))
          sysMsg('Session history cleared.')
        } catch (e) { sysMsg(`Error: ${e.message}`) }
        break

      case '/sessions':
        try {
          const data = await api.sessions()
          const list = (data.sessions || []).map(s =>
            `  ${s.session_id.slice(0, 8)}… — ${s.message_count} msgs — ${new Date(s.last_active).toLocaleString()}`
          ).join('\n')
          sysMsg(`Your sessions:\n${list || '  (none)'}`)
        } catch (e) { sysMsg(`Error: ${e.message}`) }
        break

      case '/status':
        try {
          const d = await api.health()
          sysMsg(`Server: online ✓  version: ${d.version}  env: ${d.env}`)
        } catch { sysMsg('Server: offline ✗') }
        break

      case '/memory': {
        const q = parts.slice(1).join(' ')
        if (!q) { sysMsg('Usage: /memory <search query>'); break }
        await handleSend(`Search my memory for: ${q}`)
        break
      }

      case '/schedule':
        sysMsg('Schedule: describe your task and when to run it, e.g. "remind me tomorrow at 9am to call Priya"')
        break

      default:
        sysMsg(`Unknown command: ${c}  — type /help`)
    }
  }, [activeSessionId, streaming, handleSend])

  return (
    <ErrorBoundary>
      <div className="flex h-screen bg-bg overflow-hidden">

        {/* Mobile sidebar overlay */}
        <div className={clsx(
          'fixed inset-0 z-20 lg:hidden transition-opacity',
          sidebarOpen ? 'opacity-100 pointer-events-auto' : 'opacity-0 pointer-events-none'
        )}>
          <div className="absolute inset-0 bg-black/60" onClick={() => setSidebarOpen(false)} />
          <div className="relative z-10 h-full">
            <Sidebar onClose={() => setSidebarOpen(false)} onLoadHistory={loadServerHistory} />
          </div>
        </div>

        {/* Desktop sidebar */}
        <div className="hidden lg:flex">
          <Sidebar onLoadHistory={loadServerHistory} />
        </div>

        {/* Main chat area */}
        <div className="flex-1 flex flex-col min-w-0">

          {/* Top bar */}
          <div className="flex items-center justify-between px-4 py-3 border-b border-border shrink-0">
            <div className="flex items-center gap-3">
              <button
                className="lg:hidden text-muted hover:text-text transition-colors"
                onClick={() => setSidebarOpen(true)}
              >☰</button>
              <div className="flex items-center gap-2">
                <div className={clsx(
                  'w-1.5 h-1.5 rounded-full',
                  serverOk === null ? 'bg-muted animate-pulse' :
                  serverOk ? 'bg-green-400 animate-pulse' : 'bg-red-500'
                )} />
                <span className="font-mono text-xs text-subtle">
                  {serverOk === null ? 'connecting…' : serverOk ? 'connected' : 'server offline'}
                </span>
              </div>
            </div>

            <div className="flex items-center gap-3">
              {streaming && (
                <button
                  onClick={cancelStreaming}
                  className="flex items-center gap-1.5 px-2 py-1 rounded border border-border hover:border-red-500/50 hover:text-red-400 transition-colors"
                  title="Cancel (Esc)"
                >
                  <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse" />
                  <span className="font-mono text-[10px] text-accent/80">stop</span>
                </button>
              )}
              {loadingHistory && (
                <span className="font-mono text-[10px] text-muted animate-pulse">loading…</span>
              )}
              <span className="font-mono text-[10px] text-muted hidden sm:block">
                {user?.email}
              </span>
            </div>
          </div>

          {/* Messages */}
          <div className="flex-1 overflow-y-auto py-4">
            {currentMessages.length === 0 ? (
              <EmptyState onSuggest={handleSend} />
            ) : (
              currentMessages.map(msg => <Message key={msg.id} msg={msg} />)
            )}
            <div ref={messagesEndRef} />
          </div>

          <ActiveToolsBar tools={[]} />

          <InputBar
            onSend={handleSend}
            onCommand={handleCommand}
            disabled={streaming}
          />
        </div>
      </div>
    </ErrorBoundary>
  )
}

function EmptyState({ onSuggest }) {
  return (
    <div className="flex flex-col items-center justify-center h-full px-6 gap-6">
      <div className="text-center">
        <div className="font-mono text-3xl text-accent/20 mb-3 select-none">◈</div>
        <p className="font-mono text-sm text-subtle">what can i do for you today?</p>
      </div>
      <div className="w-full max-w-lg grid grid-cols-1 sm:grid-cols-2 gap-2">
        {SUGGESTIONS.map((s, i) => (
          <button
            key={i}
            onClick={() => onSuggest(s)}
            className="text-left px-3 py-2.5 bg-surface hover:bg-dim border border-border hover:border-subtle rounded-lg font-mono text-[10px] text-muted hover:text-text transition-all leading-relaxed"
          >
            {s}
          </button>
        ))}
      </div>
    </div>
  )
}
