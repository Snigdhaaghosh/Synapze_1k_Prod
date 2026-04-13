import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import './index.css'
import { useAuthStore } from './lib/store'
import ChatPage from './pages/ChatPage'
import AuthPage from './pages/AuthPage'

function RequireAuth({ children }) {
  const token = useAuthStore(s => s.token)
  return token ? children : <Navigate to="/auth" replace />
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/auth" element={<AuthPage />} />
        <Route path="/" element={<RequireAuth><ChatPage /></RequireAuth>} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  </React.StrictMode>
)
