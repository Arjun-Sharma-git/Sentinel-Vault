import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The API runs on 8010 (Sentinel uses 8000, so both can run side by side).
const apiPort = process.env.VAULT_API_PORT ?? '8010'
const proxy = { '/api': { target: `http://127.0.0.1:${apiPort}`, changeOrigin: true, xfwd: true } }

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { port: 5174, strictPort: true, proxy },
  preview: { port: 5174, strictPort: true, proxy },
})
