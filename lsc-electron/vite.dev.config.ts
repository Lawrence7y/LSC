import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    // 使用 5300，避开本机 Windows 排除端口 5192-5291
    host: '127.0.0.1',
    port: 5300,
    strictPort: true,
    proxy: {
      '/ws': {
        target: 'ws://localhost:9876',
        ws: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
})
