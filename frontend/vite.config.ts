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
    // Windows 上 Vite 会为原子写入创建 `.<名字>.<pid>.<hash>.tmpdir/` 临时目录，
    // 而它自己的文件监视器随后又去 watch 这个目录。文件仍被占用时 `fs.watch`
    // 抛 EBUSY，**整个开发服务器直接退出** —— 表现为改几次文件后前端突然没了，
    // 报错却是 fs.watch 而不是任何业务代码。
    //
    // 这些临时目录不需要被监视（它们是写入过程中的中间态），忽略即可。
    watch: {
      ignored: ['**/.*.tmpdir/**', '**/*.tmp'],
    },
    // 开发时由 Vite 代理到后端，使 /api 与 /content 在开发和生产下**同源**。
    // 这一点很重要：活动页的沙箱行为、以及 /api 无 CORS 头这条封锁链，
    // 都依赖"同源"这个前提；跨源开发会掩盖真实的隔离行为。
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: false },
      '/content': { target: 'http://127.0.0.1:8000', changeOrigin: false },
      // /data 也代理过去，只为**拿到后端的 404**。
      //
      // 不代理的话，它会被 SPA 的 history fallback 接住并返回 index.html —— 字节
      // 并没有泄露（返回的是应用外壳，不是文件内容），但响应是 200，与生产环境
      // （nginx 直接 return 404）不一致，容易被误读成"私有目录被暴露了"。
      // 让开发环境的行为与生产一致，比省这一条代理更有价值。
      '/data': { target: 'http://127.0.0.1:8000', changeOrigin: false },
    },
  },
  build: {
    rollupOptions: {
      input: {
        // SPA 入口。SDK 不在这里构建 —— 它走 vite.sdk.config.ts 产出到
        // public/sdk/v1/cea.js，由 Vite 静态提供并随构建复制进 dist/。
        main: fileURLToPath(new URL('./index.html', import.meta.url)),
      },
      output: {
        entryFileNames: 'assets/[name]-[hash].js',
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
