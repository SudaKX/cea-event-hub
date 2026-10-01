import { fileURLToPath, URL } from 'node:url'

import { defineConfig } from 'vite'

/**
 * SDK 的独立构建。
 *
 * 产出到 `public/sdk/v1/cea.js`，而不是 `dist/`：`public/` 下的文件由 Vite
 * **静态提供**，因此
 *
 *   - 开发时 `/sdk/v1/cea.js` 能直接取到，不会被 SPA 的 history fallback
 *     吞成 index.html（那会让活动页拿到一段 HTML，解析失败，`window.CEA`
 *     不存在，看起来像"桥接坏了"）
 *   - 构建时自动被复制进 `dist/`，生产由 nginx 从 `/sdk/` 直出
 *
 * 格式固定为 **IIFE**：`<script type="module">` 受 CORS 限制，而活动页处于
 * 不透明源、对同主机也算跨源。经典脚本没有这个问题，对写单文件 HTML 的作者
 * 也少一个概念。
 */
export default defineConfig({
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  build: {
    outDir: 'public/sdk/v1',
    emptyOutDir: true,
    // **必须关掉。** Vite 默认会把 publicDir（`public/`）的内容复制进 outDir，
    // 而这里的 outDir 就在 `public/` 里面 —— 于是它把自己的产物再复制进自己，
    // 无限递归下去。实测能刷出几千个嵌套目录（public/sdk/v1/sdk/v1/sdk/...）
    // 并且永远跑不完，表现为 `npm run build:sdk` 静默挂死。
    copyPublicDir: false,
    // 不压缩：活动作者可能会打开它看协议，可读性比几 KB 更值
    minify: false,
    // 刻意**不用 build.lib**。`lib.name` 会让 Rollup 为 IIFE 生成
    // `var CEA = <exports>`，在全局作用域把这个名字覆盖掉 —— 模块内部的
    // `window.CEA = api` 会被冲掉，活动页拿到一个空对象，症状是
    // `CEA.ready` 为 undefined。
    //
    // 这里改用普通构建 + 显式 iife 格式且**不设 name**：模块自己负责挂全局。
    // 前提是入口没有运行时导出（见 src/bridge/sdk/index.ts 的文件头说明）。
    rollupOptions: {
      input: fileURLToPath(new URL('./src/bridge/sdk/index.ts', import.meta.url)),
      output: {
        format: 'iife',
        entryFileNames: 'cea.js',
      },
    },
  },
})
