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
              external: ['electron', 'electron-updater', 'path', 'fs', 'child_process', 'os', 'crypto', 'stream', 'util', 'url', 'events'],
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
              external: ['electron', 'electron-updater', 'path', 'fs', 'child_process', 'os', 'crypto', 'stream', 'util', 'url', 'events'],
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
    port: 5173,
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
})
