/**
 * 任务 15.7（可在本机验证的部分）：沙箱令牌集合。
 *
 * "活动页无法访问宿主存储与文档对象"是浏览器行为，无法在 Node 里断言；但
 * **保证这一点的那个配置**可以断言，而且必须断言 —— 有人往列表里加一个
 * `allow-same-origin` 就会让整个隔离机制静默失效，而所有其它测试仍然全绿。
 *
 * 令牌集合自 add-develop-harness 起抽到了 `src/bridge/sandbox.ts`：生产宿主与开发
 * 调试台共用同一份。两处各写一份的话，往生产那份加一个令牌却忘了调试台，本地验证
 * 就会在一个更宽松的环境里通过。因此这里既断言那份声明本身，也断言**两个视图都
 * 绑定了它**。
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

const read = (relative: string) =>
  readFileSync(resolve(process.cwd(), relative), 'utf-8')

const sandboxModule = read('src/bridge/sandbox.ts')
const eventView = read('src/views/event/EventView.vue')
const developView = read('src/views/develop/DevelopView.vue')
const guide = read('../docs/event-page-guide.md')

/** 两个承载活动内容的视图。沙箱约束对它们同样成立。 */
const HOSTING_VIEWS: Array<[string, string]> = [
  ['EventView.vue', eventView],
  ['DevelopView.vue', developView],
]

describe('沙箱令牌集合', () => {
  const declaration =
    /const SANDBOX_TOKENS = \[([^\]]*)\]/.exec(sandboxModule)?.[1] ?? ''

  it('声明确实被找到了', () => {
    // 正则失配时下面每一条都会"通过"，因为它们断言的是"不包含"。
    // 这一条是那些断言的前提，缺了它整个文件证明不了任何事。
    expect(declaration).toContain('allow-scripts')
  })

  it('绝不包含 allow-same-origin', () => {
    // 同源 iframe 一旦拿到这个令牌，沙箱等于没有：
    // 它能访问 parent.document、读到宿主内存里的状态，甚至把自己身上的
    // sandbox 属性摘掉。此时"代理"只是一种礼貌约定，不是安全边界。
    expect(declaration).not.toContain('allow-same-origin')
  })

  it('包含活动页正常工作所需的令牌', () => {
    for (const token of ['allow-scripts', 'allow-forms', 'allow-modals', 'allow-popups']) {
      expect(declaration).toContain(token)
    }
  })

  it('不给 allow-top-navigation（防活动页劫持整页）', () => {
    expect(declaration).not.toContain('allow-top-navigation')
  })

  it('不给 allow-popups-to-escape-sandbox（否则弹窗继承不到约束）', () => {
    expect(declaration).not.toContain('allow-popups-to-escape-sandbox')
  })

  for (const [name, source] of HOSTING_VIEWS) {
    it(`${name} 的 iframe 绑定到这个列表`, () => {
      expect(source).toMatch(/:sandbox="SANDBOX_TOKENS\.join\(' '\)"/)
    })

    it(`${name} 用 src 挂载内容，而不是取 HTML 文本再注入`, () => {
      // 注入会让 HTML 跑在宿主源里，隔离彻底失效，相对资源路径也会全断
      expect(source).toMatch(/:src="frameSrc"/)
      expect(source).not.toMatch(/srcdoc|innerHTML\s*=\s*fetchedHtml/)
    })
  }
})

describe('生产宿主不开拦截口子', () => {
  /*
    `BridgeHost` 上有一个**行为**口子 `shouldHold`：它能让宿主不把请求发出去
    （开发调试台要"先看清请求内容，再选择假响应或转发"）。

    它安全的唯一理由是**生产宿主不传它**。传了的话，生产环境的请求就可能被静默丢弃 ——
    活动页永远等不到结果，服务端什么都没收到，而且没有任何日志，排查会完全跑偏。

    这是读源码文本的断言（同本文件其它几条），因此它自己也要经得起变异：
    往 EventView 里加一句 `shouldHold: () => true` 必须让它失败。
  */
  it('EventView 不传 shouldHold', () => {
    expect(eventView).not.toMatch(/shouldHold/)
  })

  it('EventView 不传 onHold', () => {
    // onHold 单独出现也说明有人在生产宿主上接了这个口子
    expect(eventView).not.toMatch(/onHold/)
  })
})

describe('活动页指南与实现一致', () => {
  it('指南明确说明不能使用本地存储，并给出替代', () => {
    expect(guide).toContain('localStorage')
    expect(guide).toContain('CEA.storage')
  })

  it('指南说明储存的 key 由宿主构造', () => {
    // 这是"活动不能自由指定 localStorage key"那条约束的文档落点
    expect(guide).toMatch(/key 的所有权在宿主|宿主拼成/)
  })

  it('指南给出储存容量上限', () => {
    expect(guide).toContain('4096')
  })

  it('指南把 CEA.resize 标注为已移除，而不是当成可用方法', () => {
    // 它出现在文档里是应该的 —— 但要明确说"没有了"，否则老作者会继续调
    expect(guide).toMatch(/CEA\.resize\(\)[\s\S]{0,40}已移除/)
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
