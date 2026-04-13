# Synapze V2 — Frontend

React + Tailwind minimal terminal-style chat UI.

## Stack
- React 18 + Vite
- Tailwind CSS v3
- Zustand (state management)
- Zero external UI libraries

## Design
- Dark terminal aesthetic — JetBrains Mono font
- Electric blue (`#00d4ff`) accent
- Live streaming — tokens appear as Claude types
- Tool activity — see exactly which tools are running in real time
- Slash commands — `/new`, `/clear`, `/history`, `/schedule`, `/memory`, `/status`, `/help`
- Session management — sidebar with rename + delete
- Responsive — works on mobile too

## File structure
```
frontend/
├── index.html
├── package.json
├── vite.config.js          ← proxies API calls to backend
├── tailwind.config.js
├── postcss.config.js
└── src/
    ├── main.jsx            ← entry + router
    ├── index.css           ← global styles + markdown
    ├── pages/
    │   ├── AuthPage.jsx    ← token paste + Google OAuth guide
    │   └── ChatPage.jsx    ← main chat layout
    ├── components/
    │   ├── Sidebar.jsx     ← sessions list
    │   ├── Message.jsx     ← user + assistant bubbles
    │   ├── InputBar.jsx    ← textarea with slash autocomplete
    │   └── ToolIndicator.jsx ← live tool execution pills
    └── lib/
        ├── store.js        ← Zustand (auth + chat state)
        ├── api.js          ← all backend calls + streaming
        └── markdown.js     ← lightweight md→html (no deps)
```

## Running locally

```bash
cd frontend
npm install
npm run dev
# opens http://localhost:5173
# API proxied to http://localhost:8000
```

## Production build

```bash
cd frontend
npm run build
# outputs to frontend/dist/
```

Serve `dist/` from Nginx or any static host. Add to `docker/nginx.conf`:

```nginx
location / {
    root /app/frontend/dist;
    try_files $uri $uri/ /index.html;
}
```

Or add to `docker-compose.yml`:

```yaml
frontend:
  image: node:20-alpine
  working_dir: /app
  command: sh -c "npm install && npm run build && npx serve dist -p 3000"
  volumes:
    - ./frontend:/app
  ports:
    - "3000:3000"
```

## Auth flow

1. User visits `/auth`
2. Pastes JWT token (get it from `http://your-server:8000/auth/google`)
   OR clicks "open auth page" to do Google OAuth
3. Token verified against `/auth/me`
4. Stored in localStorage via Zustand persist
5. Redirected to `/` (chat page)
6. All API calls use `Authorization: Bearer <token>`

## Environment

Vite proxy in `vite.config.js` forwards all `/agent`, `/auth`, `/tasks`, `/health`
requests to `http://localhost:8000` during development.

For production, update `VITE_API_BASE` or configure Nginx to proxy both
the frontend static files and backend API from the same domain.
