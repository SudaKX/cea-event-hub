/**
 * 任务 15.7（可在本机验证的部分）：沙箱令牌集合。
 *
 * "活动页无法访问宿主存储与文档对象"是浏览器行为，无法在 Node 里断言；但
 * **保证这一点的那个配置**可以断言，而且必须断言 —— 有人往列表里加一个
 * `allow-same-origin` 就会让整个隔离机制静默失效，而所有其它测试仍然全绿。
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

const eventView = readFileSync(
  resolve(process.cwd(), 'src/views/event/EventView.vue'),
  'utf-8',
)
const guide = readFileSync(resolve(process.cwd(), '../docs/event-page-guide.md'), 'utf-8')

describe('沙箱令牌集合', () => {
  it('绝不包含 allow-same-origin', () => {
    // 同源 iframe 一旦拿到这个令牌，沙箱等于没有：
    // 它能访问 parent.document、读到宿主内存里的状态，甚至把自己身上的
    // sandbox 属性摘掉。此时"代理"只是一种礼貌约定，不是安全边界。
    const declaration = /const SANDBOX_TOKENS = \[([^\]]*)\]/.exec(eventView)?.[1] ?? ''
    expect(declaration).not.toContain('allow-same-origin')
  })

  it('包含活动页正常工作所需的令牌', () => {
    const declaration = /const SANDBOX_TOKENS = \[([^\]]*)\]/.exec(eventView)?.[1] ?? ''
    for (const token of ['allow-scripts', 'allow-forms', 'allow-modals', 'allow-popups']) {
      expect(declaration).toContain(token)
    }
  })

  it('不给 allow-top-navigation（防活动页劫持整页）', () => {
    const declaration = /const SANDBOX_TOKENS = \[([^\]]*)\]/.exec(eventView)?.[1] ?? ''
    expect(declaration).not.toContain('allow-top-navigation')
  })

  it('不给 allow-popups-to-escape-sandbox（否则弹窗继承不到约束）', () => {
    const declaration = /const SANDBOX_TOKENS = \[([^\]]*)\]/.exec(eventView)?.[1] ?? ''
    expect(declaration).not.toContain('allow-popups-to-escape-sandbox')
  })

  it('iframe 的 sandbox 属性绑定到这个列表', () => {
    expect(eventView).toMatch(/:sandbox="SANDBOX_TOKENS\.join\(' '\)"/)
  })

  it('用 src 挂载内容，而不是取 HTML 文本再注入', () => {
    // 注入会让 HTML 跑在宿主源里，隔离彻底失效，相对资源路径也会全断
    expect(eventView).toMatch(/:src="frameSrc"/)
    expect(eventView).not.toMatch(/srcdoc|innerHTML\s*=\s*fetchedHtml/)
  })
})

describe('活动页指南与实现一致', () => {
  it('指南明确说明不能使用本地存储', () => {
    expect(guide).toContain('localStorage')
    expect(guide).toContain('CEA.draft')
  })

  it('指南明确说明不能直接调用 /api', () => {
    expect(guide).toMatch(/fetch\('\/api/)
  })

  it('指南说明内容目录是公开的，不能放秘密', () => {
    expect(guide).toMatch(/公开只读|不要在这个文件里放秘密/)
  })

  it('指南给出 SDK 的引入方式', () => {
    expect(guide).toContain('/sdk/v1/cea.js')
  })
})
