import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The console talks to the control plane on the same origin; in dev Vite
// proxies both the REST API and the live WebSocket stream to the backend.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/v1': { target: 'http://localhost:8000', changeOrigin: true, ws: true },
      '/health': { target: 'http://localhost:8000', changeOrigin: true },
    },
  },
  build: { outDir: 'dist', emptyOutDir: true },
})
