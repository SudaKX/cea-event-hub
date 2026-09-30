/**
 * 任务 12.2：设计令牌与规格逐一对应。
 *
 * 直接读 CSS 源文件而不是 getComputedStyle：变量可能被别处覆盖，
 * 而这里要断言的是"定义本身与规格一致"。
 */
import { readFileSync } from 'node:fs'
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
