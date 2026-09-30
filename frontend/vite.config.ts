import { fileURLToPath, URL } from 'node:url'

// 从 vitest/config 取 defineConfig：它接受 Vite 配置 + `test` 段，
// 而 vite 自己的 defineConfig 不认识 `test`，会报类型错误
import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    // 开发时由 Vite 代理到后端，使 /api 与 /content 在开发和生产下**同源**。
    // 这一点很重要：活动页的沙箱行为、以及 /api 无 CORS 头这条封锁链，
    // 都依赖"同源"这个前提；跨源开发会掩盖真实的隔离行为。
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: false },
      '/content': { target: 'http://127.0.0.1:8000', changeOrigin: false },
    },
  },
  build: {
    rollupOptions: {
      input: {
        // SPA 入口
        main: fileURLToPath(new URL('./index.html', import.meta.url)),
        // SDK 独立构建：产出 dist/sdk/v1/cea.js，由 nginx 直出，
        // 供活动页以经典 <script src> 引入（不用 ES module，见 sdk/index.ts）
        'sdk/v1/cea': fileURLToPath(new URL('./src/bridge/sdk/index.ts', import.meta.url)),
      },
      output: {
        entryFileNames: (chunk) =>
          chunk.name.startsWith('sdk/') ? '[name].js' : 'assets/[name]-[hash].js',
        chunkFileNames: 'assets/[name]-[hash].js',
        assetFileNames: 'assets/[name]-[hash][extname]',
      },
    },
  },
  test: {
    environment: 'happy-dom',
    include: ['src/**/*.spec.ts'],
    globals: true,
  },
})
