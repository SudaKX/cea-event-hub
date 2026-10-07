/**
 * 从 index.html 里抽出可测的几块东西。
 *
 * 页面是**单文件**的（活动页一贯如此），但它的核心逻辑刻意写成了不碰 DOM 的纯函数，
 * 所以单测可以直接把 `<script id="puzzle-core">` 抽出来在 Node 里跑，不必搭假浏览器。
 * 构建脚本也走这里取核心 —— 编码算法只有一份实现，不会两边漂移。
 */
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

export const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
export const SOURCE_PATH = join(ROOT, 'index.html')

export function readSource() {
  return readFileSync(SOURCE_PATH, 'utf8')
}

/** 取 `<script id="...">…</script>` 的内容。找不到就抛 —— 块被改名时必须立刻暴露。 */
export function extractBlock(html, id) {
  const pattern = new RegExp(`<script id="${id}"[^>]*>([\\s\\S]*?)</script>`)
  const match = pattern.exec(html)
  if (!match) throw new Error(`index.html 里找不到 <script id="${id}">`)
  return match[1]
}

/** 核心只依赖 TextEncoder/TextDecoder/atob/btoa，Node 全都有 */
export function loadCore(html = readSource()) {
  return new Function(`${extractBlock(html, 'puzzle-core')}\nreturn PuzzleCore`)()
}

export function loadFs(html = readSource()) {
  return JSON.parse(extractBlock(html, 'puzzle-fs'))
}
