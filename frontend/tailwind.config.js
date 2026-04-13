/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      fontFamily: {
        mono: ['"JetBrains Mono"', '"Fira Code"', 'Consolas', 'monospace'],
        sans: ['"DM Sans"', 'system-ui', 'sans-serif'],
      },
      colors: {
        bg:      '#0a0a0a',
        surface: '#111111',
        border:  '#1e1e1e',
        dim:     '#2a2a2a',
        muted:   '#4a4a4a',
        subtle:  '#6b6b6b',
        text:    '#e8e8e8',
        bright:  '#ffffff',
        accent:  '#00d4ff',
        green:   '#00ff88',
        amber:   '#ffb800',
        red:     '#ff4444',
        purple:  '#a78bfa',
      },
      animation: {
        'blink':    'blink 1.2s step-end infinite',
        'slide-up': 'slideUp 0.2s ease-out',
        'fade-in':  'fadeIn 0.3s ease-out',
        'pulse-dot':'pulseDot 2s ease-in-out infinite',
      },
      keyframes: {
        blink:    { '0%,100%': { opacity: 1 }, '50%': { opacity: 0 } },
        slideUp:  { from: { opacity: 0, transform: 'translateY(8px)' }, to: { opacity: 1, transform: 'translateY(0)' } },
        fadeIn:   { from: { opacity: 0 }, to: { opacity: 1 } },
        pulseDot: { '0%,100%': { opacity: 0.3 }, '50%': { opacity: 1 } },
      },
    },
  },
  plugins: [],
}
