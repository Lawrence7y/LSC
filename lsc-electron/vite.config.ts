import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'
import electron from 'vite-plugin-electron'

export default defineConfig({
  plugins: [
    react(),
    electron([
      {
        entry: 'electron/main.ts',
        onstart: (options) => options.startup(),
        vite: {
          build: {
            sourcemap: true,
            minify: false,
            outDir: 'dist-electron/main',
            rollupOptions: {
              external: ['electron', 'path', 'fs', 'child_process', 'os', 'crypto', 'stream', 'util', 'url', 'events'],
            },
          },
        },
      },
      {
        entry: 'electron/preload.ts',
        onstart: (options) => options.reload(),
        vite: {
          build: {
            sourcemap: true,
            minify: false,
            outDir: 'dist-electron/preload',
            rollupOptions: {
              external: ['electron', 'path', 'fs', 'child_process', 'os', 'crypto', 'stream', 'util', 'url', 'events'],
            },
          },
        },
      },
    ]),
  ],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    // 5173 落在本机 Windows 排除端口 5150-5249，会导致 EACCES
    host: '127.0.0.1',
    port: 5250,
    strictPort: true,
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
})
