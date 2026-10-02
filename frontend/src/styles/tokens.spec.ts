/**
 * 任务 12.2：设计令牌与规格逐一对应。
 *
 * 直接读 CSS 源文件而不是 getComputedStyle：变量可能被别处覆盖，
 * 而这里要断言的是"定义本身与规格一致"。
 */
import { readdirSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

// 用 cwd 相对路径而不是 import.meta.url：happy-dom 环境下后者不是 file: URL，
// fileURLToPath 会直接抛错。vitest 的工作目录就是 frontend/。
const stylesDir = resolve(process.cwd(), 'src/styles')
const read = (name: string) => readFileSync(resolve(stylesDir, name), 'utf-8')

const tokensCss = read('tokens.css')
const baseCss = read('base.css')
const componentsCss = read('components.css')
const allCss = `${tokensCss}\n${baseCss}\n${componentsCss}`

/** 规格要求的取值，逐个对应 */
const REQUIRED_TOKENS: Record<string, string> = {
  '--bg': '#0b0b0d',
  '--panel': '#101014',
  '--bone': '#cfcac4',
  '--mute': '#8b8e97',
  '--dim': '#5f626b',
  '--red': '#d0202f',
  '--red-hi': '#ff4a55',
  '--line': 'rgba(255, 255, 255, 0.09)',
}

describe('设计令牌', () => {
  it.each(Object.entries(REQUIRED_TOKENS))('%s 取值为 %s', (name, value) => {
    const pattern = new RegExp(`${name}\\s*:\\s*${value.replace(/[()]/g, '\\$&')}\\s*;`)
    expect(tokensCss).toMatch(pattern)
  })

  it('定义了两套字体栈：等宽与无衬线', () => {
    expect(tokensCss).toMatch(/--mono\s*:[^;]*monospace/)
    expect(tokensCss).toMatch(/--sans\s*:[^;]*sans-serif/)
  })

  it('等宽栈以 ui-monospace 开头，无衬线栈包含中文字体回退', () => {
    const mono = /--mono\s*:\s*([^;]+);/.exec(tokensCss)?.[1] ?? ''
    expect(mono.trim().startsWith('ui-monospace')).toBe(true)

    const sans = /--sans\s*:\s*([^;]+);/.exec(tokensCss)?.[1] ?? ''
    expect(sans).toContain('Microsoft YaHei')
  })

  it('圆角只有两档', () => {
    expect(tokensCss).toMatch(/--radius-control\s*:\s*7px/)
    expect(tokensCss).toMatch(/--radius-surface\s*:\s*10px/)
  })
})

describe('样式约束', () => {
  it('不使用渐变', () => {
    // 渐变会破坏海报那种平涂的硬边质感
    expect(allCss).not.toMatch(/linear-gradient|radial-gradient|conic-gradient/)
  })

  it('过渡时长不超过 200ms', () => {
    const durations = [...allCss.matchAll(/(\d+)ms/g)].map((match) => Number(match[1]))
    // 允许 0.01ms 这类"关闭动效"的写法
    const real = durations.filter((value) => value > 1)
    expect(real.length).toBeGreaterThan(0)
    for (const value of real) expect(value).toBeLessThanOrEqual(200)
  })

  it('焦点样式为亮强调色 2px 实线且偏移 3px', () => {
    expect(baseCss).toMatch(/outline:\s*2px solid var\(--red-hi\)/)
    expect(baseCss).toMatch(/outline-offset:\s*3px/)
  })

  it('尊重系统的减少动效偏好', () => {
    expect(baseCss).toContain('prefers-reduced-motion')
  })

  it('数字与标识使用等宽字体', () => {
    expect(baseCss).toMatch(/\.num\s*\{[^}]*var\(--mono\)/)
    // 表格里的数字列同样走等宽，列宽才稳定
    expect(componentsCss).toMatch(/\.table[^{]*\.num[^{]*\{[^}]*var\(--mono\)/)
  })

  it('标题使用等宽字体', () => {
    expect(baseCss).toMatch(/h1,\s*h2,\s*h3\s*\{[^}]*var\(--mono\)/)
  })
})

describe('标签配色不留空档', () => {
  /*
    用了未定义的类名不会报错，只是**默默地没有样式** —— 页面上看不出异常，
    只有仔细比对才会发现某个标签"好像变淡了"。

    「停用」标签就这样失效过：提交状态改造时把 tag--rejected 改名为 tag--ignored，
    而 UsersView 还在用旧名字。所以这里逐个核对源码里出现的后缀。
  */
  const viewDir = resolve(process.cwd(), 'src')

  /** 递归收集所有 .vue / .ts 源码（排除测试与样式文件本身） */
  function collectSources(dir: string): string[] {
    return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
      const full = resolve(dir, entry.name)
      if (entry.isDirectory()) return collectSources(full)
      if (!/\.(vue|ts)$/.test(entry.name)) return []
      if (/\.spec\.ts$/.test(entry.name)) return []
      return [readFileSync(full, 'utf-8')]
    })
  }

  it('源码里静态用到的 tag--* 都在 components.css 里有定义', () => {
    const defined = new Set(
      [...componentsCss.matchAll(/\.(tag--[a-z][a-z0-9-]*)/g)].map((m) => m[1]),
    )
    expect(defined.size).toBeGreaterThan(0)

    const used = new Set<string>()
    for (const source of collectSources(viewDir)) {
      for (const match of source.matchAll(/'tag--([a-z][a-z0-9-]*)'|"tag--([a-z][a-z0-9-]*)"|`tag--([a-z][a-z0-9-]*)`/g)) {
        used.add(`tag--${match[1] ?? match[2] ?? match[3]}`)
      }
    }

    const missing = [...used].filter((name) => !defined.has(name)).sort()
    expect(missing).toEqual([])
  })

  it('动态拼出来的状态后缀也都有定义', () => {
    // statusTone() 返回的词会拼进 `tag--${tone}`，静态扫描抓不到，所以单独核对
    const defined = new Set(
      [...componentsCss.matchAll(/\.(tag--[a-z][a-z0-9-]*)/g)].map((m) => m[1]),
    )
    const statusSource = readFileSync(resolve(viewDir, 'domain/submission.ts'), 'utf-8')
    const tones = [...statusSource.matchAll(/:\s*'([a-z]+)',?\s*$/gm)].map((m) => `tag--${m[1]}`)

    expect(tones.length).toBeGreaterThan(0)
    for (const tone of tones) expect(defined).toContain(tone)
  })
})
