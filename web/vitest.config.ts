import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vitest/config'

const WEB_ROOT = path.dirname(fileURLToPath(import.meta.url))

export default defineConfig({
  root: WEB_ROOT,
  resolve: {
    alias: { '@': path.resolve(WEB_ROOT, 'src') },
  },
  server: { fs: { allow: [path.resolve(WEB_ROOT, '..')] } },
  test: {
    environment: 'jsdom',
    restoreMocks: true,
    clearMocks: true,
    setupFiles: ['src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
  },
})
