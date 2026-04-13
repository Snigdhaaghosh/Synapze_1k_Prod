// Lightweight markdown → HTML (no dependencies)
// Handles: code blocks, inline code, bold, italic, lists, links, line breaks

export function renderMarkdown(text) {
  if (!text) return ''

  let html = text
    // Escape HTML
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')

    // Fenced code blocks
    .replace(/```(\w*)\n?([\s\S]*?)```/g, (_, lang, code) =>
      `<pre><code class="lang-${lang}">${code.trim()}</code></pre>`
    )

    // Inline code
    .replace(/`([^`]+)`/g, '<code>$1</code>')

    // Bold
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')

    // Italic
    .replace(/\*(.+?)\*/g, '<em>$1</em>')

    // Links
    .replace(/\[(.+?)\]\((https?:\/\/[^\)]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener">$1</a>'
    )

    // Unordered lists (consecutive lines starting with - or *)
    .replace(/(^|\n)([ \t]*[-*] .+(\n[ \t]*[-*] .+)*)/g, (_, pre, block) => {
      const items = block.trim().split('\n').map(l =>
        `<li>${l.replace(/^[ \t]*[-*] /, '')}</li>`
      ).join('')
      return `${pre}<ul>${items}</ul>`
    })

    // Ordered lists
    .replace(/(^|\n)([ \t]*\d+\. .+(\n[ \t]*\d+\. .+)*)/g, (_, pre, block) => {
      const items = block.trim().split('\n').map(l =>
        `<li>${l.replace(/^[ \t]*\d+\. /, '')}</li>`
      ).join('')
      return `${pre}<ol>${items}</ol>`
    })

    // Line breaks → paragraphs (double newline)
    .replace(/\n\n+/g, '</p><p>')

    // Single newlines
    .replace(/\n/g, '<br />')

  return `<p>${html}</p>`
}
