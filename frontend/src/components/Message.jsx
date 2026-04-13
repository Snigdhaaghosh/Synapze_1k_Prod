import { renderMarkdown } from '../lib/markdown'
import { ToolPill } from './ToolIndicator'
import clsx from 'clsx'

export default function Message({ msg }) {
  const isUser = msg.role === 'user'
  const isAssistant = msg.role === 'assistant'

  if (isUser) {
    return (
      <div className="flex justify-end px-4 py-1 animate-slide-up">
        <div className="max-w-[80%]">
          <div className="bg-dim border border-border rounded-xl rounded-tr-sm px-4 py-2.5">
            <p className="font-mono text-xs text-text whitespace-pre-wrap leading-relaxed">
              {msg.content}
            </p>
          </div>
        </div>
      </div>
    )
  }

  if (isAssistant) {
    return (
      <div className="flex gap-3 px-4 py-1 animate-slide-up">
        {/* Avatar */}
        <div className="shrink-0 mt-0.5">
          <div className="w-5 h-5 rounded border border-accent/30 bg-accent/5 flex items-center justify-center">
            <span className="text-accent font-mono text-[9px] font-semibold">S</span>
          </div>
        </div>

        <div className="flex-1 min-w-0">
          {/* Tool calls inline */}
          {msg.tools?.length > 0 && (
            <div className="flex flex-wrap gap-0.5 mb-2">
              {msg.tools.map((t, i) => (
                <ToolPill key={i} tool={t.name} done={t.done} success={t.success} />
              ))}
            </div>
          )}

          {/* Response content */}
          {msg.content && (
            <div
              className={clsx(
                'msg-content font-sans text-sm text-text leading-relaxed',
                msg.streaming && 'cursor'
              )}
              dangerouslySetInnerHTML={{ __html: renderMarkdown(msg.content) }}
            />
          )}

          {/* Empty streaming state */}
          {msg.streaming && !msg.content && !msg.tools?.length && (
            <div className="flex gap-1 items-center py-1">
              {[0,1,2].map(i => (
                <div
                  key={i}
                  className="w-1 h-1 rounded-full bg-accent/40 animate-pulse-dot"
                  style={{ animationDelay: `${i * 0.2}s` }}
                />
              ))}
            </div>
          )}

          {/* Token info */}
          {!msg.streaming && msg.tokens && (
            <p className="font-mono text-[10px] text-muted mt-1.5">
              {msg.tokens.toLocaleString()} tokens
            </p>
          )}
        </div>
      </div>
    )
  }

  return null
}
