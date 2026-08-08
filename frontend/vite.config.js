import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  // Must be absolute: Zerops' static service falls back to index.html for client
  // routes, so a relative base makes /build/:id request /build/assets/*.js and 404.
  base: '/',
})
