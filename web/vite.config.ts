import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { VitePWA } from 'vite-plugin-pwa'

const WEB_ROOT = path.dirname(fileURLToPath(import.meta.url))
const API_TARGET = process.env.MAA_API_TARGET ?? 'http://127.0.0.1:8002'

export default defineConfig({
  root: WEB_ROOT,
  base: '/',
  resolve: {
    alias: { '@': path.resolve(WEB_ROOT, 'src') },
  },
  plugins: [
    react(),
    tailwindcss(),
    VitePWA({
      registerType: 'prompt',
      injectRegister: false,
      // generateSW 没有 push 事件处理，而 Web Push 的通知完全依赖它（M14）。
      // 改用 injectManifest + 自己的 src/sw.ts：预缓存仍走 Workbox，push 与
      // notificationclick 由我们实现。
      strategies: 'injectManifest',
      srcDir: 'src',
      filename: 'sw.ts',
      injectManifest: {
        globPatterns: ['**/*.{css,html,ico,png,svg,webmanifest,js}'],
        maximumFileSizeToCacheInBytes: 3 * 1024 * 1024,
      },
      includeAssets: [
        'favicon.svg',
        'apple-touch-icon.png',
        'pwa-192x192.png',
        'pwa-512x512.png',
        'pwa-maskable-512x512.png',
      ],
      manifest: {
        name: 'MAA-API 控制台',
        short_name: 'MAA-API',
        description: '明日方舟自动化服务控制台',
        theme_color: '#101411',
        background_color: '#101411',
        display: 'standalone',
        orientation: 'any',
        start_url: '/',
        scope: '/',
        icons: [
          { src: '/pwa-192x192.png', sizes: '192x192', type: 'image/png' },
          { src: '/pwa-512x512.png', sizes: '512x512', type: 'image/png' },
          {
            src: '/pwa-maskable-512x512.png',
            sizes: '512x512',
            type: 'image/png',
            purpose: 'maskable',
          },
        ],
      },
    }),
  ],
  server: {
    host: true,
    port: 5173,
    fs: { allow: [path.resolve(WEB_ROOT, '..')] },
    proxy: {
      '/api': { target: API_TARGET, changeOrigin: true, ws: true },
      '/openapi.json': { target: API_TARGET, changeOrigin: true },
      '/docs': { target: API_TARGET, changeOrigin: true },
      '/redoc': { target: API_TARGET, changeOrigin: true },
    },
  },
  build: {
    outDir: path.resolve(WEB_ROOT, '../static'),
    emptyOutDir: true,
    sourcemap: true,
    rolldownOptions: {
      output: {
        entryFileNames: 'assets/[name]-[hash].js',
        chunkFileNames: 'assets/[name]-[hash].js',
        assetFileNames: 'assets/[name]-[hash][extname]',
      },
    },
  },
})
