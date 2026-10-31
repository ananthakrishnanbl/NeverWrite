import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Node's process object; the frontend doesn't install @types/node
declare const process: { env: Record<string, string | undefined> }

// Set NEVERWRITE_API to proxy to an API on another port, e.g. http://127.0.0.1:8002
const api = process.env.NEVERWRITE_API ?? 'http://localhost:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': api,
      '/files': api,
    }
  }
})
