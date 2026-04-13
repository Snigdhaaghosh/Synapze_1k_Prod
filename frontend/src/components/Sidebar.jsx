import { useState, useEffect } from 'react'
import { useChatStore, useAuthStore } from '../lib/store'
import { api } from '../lib/api'
import clsx from 'clsx'

function timeAgo(ts) {
  const diff = Date.now() - new Date(ts).getTime()
  if (diff < 60_000)    return 'just now'
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`
  if (diff < 86_400_000)return `${Math.floor(diff / 3_600_000)}h ago`
  return `${Math.floor(diff / 86_400_000)}d ago`
}

export default function Sidebar({ onClose, onLoadHistory }) {
  const {
    sessions, activeSessionId, setActiveSession,
    newSession, deleteSession, renameSession, addMessage,
  } = useChatStore()

  const { user, clear: logout } = useAuthStore()

  const [editingId, setEditingId] = useState(null)
  const [editVal, setEditVal]     = useState('')
  const [loadingServer, setLoadingServer] = useState(false)
  const [serverSessions, setServerSessions] = useState([])
  const [showServerSessions, setShowServerSessions] = useState(false)

  // ── Load server sessions on mount ─────────────────────────────────────────
  useEffect(() => {
    loadSessions()
  }, [])

  async function loadSessions() {
    setLoadingServer(true)
    try {
      const data = await api.sessions()
      setServerSessions(data.sessions || [])
    } catch (e) {
      console.warn('Could not load server sessions:', e.message)
    } finally {
      setLoadingServer(false)
    }
  }

  function handleNew() {
    newSession()
    onClose?.()
  }

  async function handleSelect(id) {
    setActiveSession(id)
    // Load messages from server if local copy is empty
    const localMsgs = useChatStore.getState().messages[id] ?? []
    if (localMsgs.length === 0 && onLoadHistory) {
      await onLoadHistory(id)
    }
    onClose?.()
  }

  function startRename(e, session) {
    e.stopPropagation()
    setEditingId(session.id)
    setEditVal(session.label)
  }

  function commitRename(id) {
    if (editVal.trim()) renameSession(id, editVal.trim())
    setEditingId(null)
  }

  async function handleLogout() {
    try { await api.logout() } catch {}
    logout()
  }

  // Merge local + server sessions, deduplicated by id
  const allIds = new Set(sessions.map(s => s.id))
  const serverOnly = serverSessions.filter(s => !allIds.has(s.session_id))

  return (
    <div className="flex flex-col h-full bg-surface border-r border-border w-64 shrink-0">

      {/* Header */}
      <div className="flex items-center justify-between px-4 py-4 border-b border-border">
        <div>
          <span className="font-mono text-xs text-accent font-semibold tracking-wider">SYNAPZE</span>
          <span className="font-mono text-[9px] text-muted ml-2">enterprise</span>
        </div>
        <button
          onClick={handleNew}
          className="text-muted hover:text-accent font-mono text-lg leading-none transition-colors"
          title="New session"
        >+</button>
      </div>

      {/* Sessions list */}
      <div className="flex-1 overflow-y-auto py-2">

        {/* Local sessions */}
        {sessions.length === 0 && serverOnly.length === 0 && (
          <p className="text-muted font-mono text-xs px-4 py-3">no sessions yet</p>
        )}

        {sessions.map(session => (
          <SessionRow
            key={session.id}
            id={session.id}
            label={session.label}
            subtitle={timeAgo(session.createdAt)}
            active={activeSessionId === session.id}
            editing={editingId === session.id}
            editVal={editVal}
            onSelect={() => handleSelect(session.id)}
            onEditChange={setEditVal}
            onEditStart={(e) => startRename(e, session)}
            onEditCommit={() => commitRename(session.id)}
            onEditCancel={() => setEditingId(null)}
            onDelete={(e) => { e.stopPropagation(); deleteSession(session.id) }}
          />
        ))}

        {/* Server-side sessions not in local state */}
        {serverOnly.length > 0 && (
          <>
            <div
              className="flex items-center gap-2 px-4 py-2 cursor-pointer"
              onClick={() => setShowServerSessions(v => !v)}
            >
              <span className="font-mono text-[9px] text-muted uppercase tracking-widest">
                {showServerSessions ? '▾' : '▸'} server history ({serverOnly.length})
              </span>
            </div>

            {showServerSessions && serverOnly.map(session => (
              <SessionRow
                key={session.session_id}
                id={session.session_id}
                label={session.title || `Session ${session.session_id.slice(0, 8)}…`}
                subtitle={`${session.message_count} msgs · ${timeAgo(session.last_active)}`}
                active={activeSessionId === session.session_id}
                editing={false}
                onSelect={() => {
                  // Pull this server session into local state and load history
                  useChatStore.setState(s => ({
                    sessions: [
                      { id: session.session_id, label: session.title || `Session ${session.session_id.slice(0, 8)}…`, createdAt: new Date(session.created_at).getTime() },
                      ...s.sessions,
                    ],
                    activeSessionId: session.session_id,
                    messages: { ...s.messages, [session.session_id]: [] },
                  }))
                  onLoadHistory?.(session.session_id)
                  onClose?.()
                }}
                onDelete={() => {}}
                server
              />
            ))}
          </>
        )}
      </div>

      {/* Footer */}
      <div className="border-t border-border px-4 py-3">
        <div className="flex items-center justify-between">
          <div className="min-w-0">
            <p className="font-mono text-[9px] text-muted">signed in as</p>
            <p className="font-mono text-xs text-text truncate max-w-[130px]">
              {user?.email ?? 'unknown'}
            </p>
          </div>
          <div className="flex gap-2 items-center">
            <button
              onClick={loadSessions}
              className="font-mono text-[9px] text-muted hover:text-accent transition-colors"
              title="Refresh sessions"
            >↻</button>
            <button
              onClick={handleLogout}
              className="font-mono text-[10px] text-muted hover:text-red-400 transition-colors"
            >
              logout
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

function SessionRow({
  id, label, subtitle, active, editing, editVal,
  onSelect, onEditChange, onEditStart, onEditCommit, onEditCancel,
  onDelete, server = false,
}) {
  return (
    <div
      onClick={onSelect}
      className={clsx(
        'group flex items-start gap-2 px-4 py-2.5 cursor-pointer transition-colors',
        active ? 'bg-dim text-text' : 'hover:bg-dim/50 text-subtle hover:text-text'
      )}
    >
      <span className={clsx(
        'font-mono text-xs mt-0.5 shrink-0',
        active ? 'text-accent' : 'text-accent/40'
      )}>›</span>

      <div className="flex-1 min-w-0">
        {editing ? (
          <input
            autoFocus
            value={editVal}
            onChange={e => onEditChange(e.target.value)}
            onBlur={onEditCommit}
            onKeyDown={e => {
              if (e.key === 'Enter') onEditCommit()
              if (e.key === 'Escape') onEditCancel()
            }}
            onClick={e => e.stopPropagation()}
            className="w-full bg-bg border border-accent/40 rounded px-1.5 py-0.5 font-mono text-xs text-text focus:outline-none"
          />
        ) : (
          <p className="font-mono text-xs truncate">{label}</p>
        )}
        <p className="font-mono text-[9px] text-muted mt-0.5">
          {server && <span className="text-accent/40 mr-1">cloud</span>}
          {subtitle}
        </p>
      </div>

      {!server && (
        <div className="flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity shrink-0">
          {onEditStart && (
            <button
              onClick={onEditStart}
              className="text-muted hover:text-text font-mono text-[10px] px-1"
              title="Rename"
            >✎</button>
          )}
          <button
            onClick={onDelete}
            className="text-muted hover:text-red-400 font-mono text-[10px] px-1"
            title="Delete"
          >✕</button>
        </div>
      )}
    </div>
  )
}
