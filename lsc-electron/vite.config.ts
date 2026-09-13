import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'
import electron from 'vite-plugin-electron'

export default defineConfig(({ mode }) => {
  const isProd = mode === 'production'
  const electronBuild = {
    sourcemap: !isProd,
    minify: isProd,
    rollupOptions: {
      external: ['electron', 'path', 'fs', 'child_process', 'os', 'crypto', 'stream', 'util', 'url', 'events'],
    },
  }

  return {
    plugins: [
      react(),
      electron([
        {
          entry: 'electron/main.ts',
          onstart: (options) => {
            // ELECTRON_RUN_AS_NODE=1 会导致 Electron 退化为纯 Node.js 进程，
            // 使 require('electron') 返回 npm 包路径而非内置 API。启动前必须清除。
            delete process.env.ELECTRON_RUN_AS_NODE
            options.startup()
          },
          vite: {
            build: {
              ...electronBuild,
              outDir: 'dist-electron/main',
            },
          },
        },
        {
          entry: 'electron/preload.ts',
          onstart: (options) => options.reload(),
          vite: {
            build: {
              ...electronBuild,
              outDir: 'dist-electron/preload',
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
      // 使用 5300，避开本机 Windows 排除端口 5192-5291
      host: '127.0.0.1',
      port: 5300,
      strictPort: true,
    },
    build: {
      outDir: 'dist',
      emptyOutDir: true,
    },
  }
})
