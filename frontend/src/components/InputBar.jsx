import { useState, useRef, useEffect } from 'react'

const SLASH_COMMANDS = [
  { cmd: '/new',      desc: 'New session' },
  { cmd: '/clear',    desc: 'Clear history' },
  { cmd: '/history',  desc: 'Show history' },
  { cmd: '/schedule', desc: 'Schedule task' },
  { cmd: '/status',   desc: 'Server status' },
  { cmd: '/memory',   desc: 'Search memory' },
  { cmd: '/help',     desc: 'Show help' },
]

export default function InputBar({ onSend, onCommand, disabled }) {
  const [value, setValue]         = useState('')
  const [suggestions, setSugg]    = useState([])
  const [suggIdx, setSuggIdx]     = useState(0)
  const textareaRef               = useRef(null)

  // Auto-resize textarea
  useEffect(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 160) + 'px'
  }, [value])

  // Focus on mount
  useEffect(() => { textareaRef.current?.focus() }, [])

  function handleChange(e) {
    const v = e.target.value
    setValue(v)

    // Show slash suggestions
    if (v.startsWith('/') && !v.includes(' ')) {
      const filtered = SLASH_COMMANDS.filter(c => c.cmd.startsWith(v))
      setSugg(filtered)
      setSuggIdx(0)
    } else {
      setSugg([])
    }
  }

  function submit() {
    const trimmed = value.trim()
    if (!trimmed || disabled) return
    setValue('')
    setSugg([])

    if (trimmed.startsWith('/')) {
      onCommand?.(trimmed)
    } else {
      onSend?.(trimmed)
    }
  }

  function applySuggestion(cmd) {
    setValue(cmd + ' ')
    setSugg([])
    textareaRef.current?.focus()
  }

  function handleKeyDown(e) {
    if (suggestions.length > 0) {
      if (e.key === 'ArrowDown') { e.preventDefault(); setSuggIdx(i => (i+1) % suggestions.length) }
      if (e.key === 'ArrowUp')   { e.preventDefault(); setSuggIdx(i => (i-1+suggestions.length) % suggestions.length) }
      if (e.key === 'Tab' || (e.key === 'Enter' && !e.shiftKey)) {
        e.preventDefault()
        applySuggestion(suggestions[suggIdx].cmd)
        return
      }
      if (e.key === 'Escape') { setSugg([]); return }
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      submit()
    }
  }

  return (
    <div className="relative px-4 pb-4">
      {/* Slash suggestions */}
      {suggestions.length > 0 && (
        <div className="absolute bottom-full left-4 right-4 mb-1 bg-surface border border-border rounded-lg overflow-hidden shadow-xl">
          {suggestions.map((s, i) => (
            <button
              key={s.cmd}
              onClick={() => applySuggestion(s.cmd)}
              className={`w-full flex items-center gap-3 px-3 py-2 font-mono text-xs text-left transition-colors ${
                i === suggIdx ? 'bg-dim text-text' : 'text-subtle hover:bg-dim/50 hover:text-text'
              }`}
            >
              <span className="text-accent">{s.cmd}</span>
              <span className="text-muted">{s.desc}</span>
            </button>
          ))}
        </div>
      )}

      <div className="flex items-end gap-2 bg-surface border border-border rounded-xl px-3 py-2 focus-within:border-accent/40 transition-colors">
        <span className="text-accent font-mono text-sm pb-0.5 shrink-0 select-none">›</span>

        <textarea
          ref={textareaRef}
          value={value}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          disabled={disabled}
          placeholder={disabled ? 'synapze is thinking...' : 'message or /command'}
          rows={1}
          className="flex-1 bg-transparent font-mono text-xs text-text placeholder-muted resize-none focus:outline-none leading-relaxed py-0.5 disabled:opacity-50"
          style={{ minHeight: '20px' }}
          spellCheck={false}
        />

        <button
          onClick={submit}
          disabled={disabled || !value.trim()}
          className="shrink-0 text-accent/60 hover:text-accent disabled:opacity-20 transition-colors font-mono text-sm pb-0.5"
          title="Send (Enter)"
        >
          ↵
        </button>
      </div>

      <p className="font-mono text-[9px] text-muted/40 text-center mt-1.5">
        enter to send · shift+enter new line · /help for commands
      </p>
    </div>
  )
}
