import clsx from 'clsx'

const TOOL_ICONS = {
  gmail:    { icon: '✉', label: 'Gmail',    color: 'text-amber' },
  calendar: { icon: '◫', label: 'Calendar', color: 'text-green' },
  whatsapp: { icon: '◎', label: 'WhatsApp', color: 'text-green' },
  slack:    { icon: '◈', label: 'Slack',    color: 'text-purple' },
  browser:  { icon: '◉', label: 'Browser',  color: 'text-accent' },
  memory:   { icon: '◆', label: 'Memory',   color: 'text-purple' },
  default:  { icon: '◇', label: 'Tool',     color: 'text-subtle' },
}

function getToolMeta(name) {
  const prefix = name?.split('_')[0] ?? ''
  return TOOL_ICONS[prefix] ?? TOOL_ICONS.default
}

function getToolLabel(name) {
  if (!name) return 'tool'
  return name.replace(/_/g, ' ')
}

// Single tool pill — used inline in message stream
export function ToolPill({ tool, done, success, input }) {
  const meta = getToolMeta(tool)
  return (
    <div className={clsx(
      'inline-flex items-center gap-1.5 font-mono text-[10px] px-2 py-1 rounded border',
      'my-0.5 mr-1 transition-all duration-300',
      done
        ? success !== false
          ? 'bg-green/5 border-green/20 text-green/70'
          : 'bg-red/5 border-red/20 text-red/70'
        : 'bg-accent/5 border-accent/20 text-accent/70 animate-pulse-dot'
    )}>
      <span>{meta.icon}</span>
      <span>{getToolLabel(tool)}</span>
      {done
        ? <span>{success !== false ? '✓' : '✗'}</span>
        : <span className="inline-block w-2 h-2 rounded-full bg-current animate-pulse" />
      }
    </div>
  )
}

// Floating active tools bar — shown while streaming
export function ActiveToolsBar({ tools }) {
  if (!tools?.length) return null
  const pending = tools.filter(t => !t.done)
  if (!pending.length) return null

  return (
    <div className="flex items-center gap-2 px-4 py-2 border-t border-border bg-surface/80">
      <span className="text-muted font-mono text-[10px]">running</span>
      {pending.map((t, i) => (
        <div key={i} className="flex items-center gap-1 font-mono text-[10px] text-accent/80">
          <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse" />
          <span>{getToolLabel(t.name)}</span>
        </div>
      ))}
    </div>
  )
}
