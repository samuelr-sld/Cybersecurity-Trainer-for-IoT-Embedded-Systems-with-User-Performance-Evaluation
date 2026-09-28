import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // The backend's development CORS allowlist (backend/app/config.py) names
  // :5173 only. Without strictPort a second dev server silently moves to
  // :5174, where every /api request is blocked; fail loudly instead.
  server: { port: 5173, strictPort: true },
})
