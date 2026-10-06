/**
 * 调试来源的校验（add-develop-harness 任务 4.6 的来源限制一半）。
 *
 * 这一组是**安全边界**的断言，不是便利性断言：调试台会把身份描述符交给被加载的
 * 页面，并把它变成当前活动的操作代理。少了这道校验，它就是一个开放代理。
 *
 * 因此这里刻意覆盖了几种"看起来像站内路径"的写法 —— `//host` 是协议相对地址，
 * 光判断"以 / 开头"会把它放过去。
 */
import { describe, expect, it } from 'vitest'

import { ALLOWED_SOURCE_PREFIXES, describeSourceRejection, resolveDebugSource } from './source'

const ORIGIN = 'http://localhost:5173'

describe('接受站内的活动内容路径', () => {
  it('接受草稿路径', () => {
    expect(resolveDebugSource('/draft/demo/index.html', ORIGIN)).toBe(
      '/draft/demo/index.html',
    )
  })

  it('接受已投放内容的路径', () => {
    expect(resolveDebugSource('/content/2026spring/index.html', ORIGIN)).toBe(
      '/content/2026spring/index.html',
    )
  })

  it('保留查询串（版本参数决定缓存行为）', () => {
    expect(resolveDebugSource('/content/2026spring/index.html?v=5', ORIGIN)).toBe(
      '/content/2026spring/index.html?v=5',
    )
  })

  it('丢掉 hash（页内锚点与加载来源无关）', () => {
    expect(resolveDebugSource('/draft/demo/index.html#step2', ORIGIN)).toBe(
      '/draft/demo/index.html',
    )
  })

  it('接受完整的同源地址', () => {
    expect(resolveDebugSource(`${ORIGIN}/draft/demo/index.html`, ORIGIN)).toBe(
      '/draft/demo/index.html',
    )
  })

  it('两端空白被忽略', () => {
    expect(resolveDebugSource('  /draft/demo/index.html  ', ORIGIN)).toBe(
      '/draft/demo/index.html',
    )
  })
})

describe('拒绝站外来源', () => {
  // 这一组是这道边界的全部意义所在
  const rejected = [
    'https://evil.example/index.html',
    'http://evil.example/draft/x/index.html',
    // 协议相对地址：浏览器会把它解析成 http://evil.example/...，光看"以 / 开头"会放过
    '//evil.example/draft/x/index.html',
    // 用站外来源但路径看着像站内，是最容易骗过前缀判断的一种
    'https://evil.example/draft/x/index.html',
  ]

  for (const raw of rejected) {
    it(`拒绝 ${raw}`, () => {
      expect(resolveDebugSource(raw, ORIGIN)).toBeNull()
    })
  }

  it('拒绝 javascript: 之类的非 http 方案', () => {
    expect(resolveDebugSource('javascript:alert(1)', ORIGIN)).toBeNull()
    expect(resolveDebugSource('data:text/html,<h1>x</h1>', ORIGIN)).toBeNull()
  })

  it('拒绝换了端口的同主机地址（那是另一个源）', () => {
    expect(resolveDebugSource('http://localhost:9999/draft/x/index.html', ORIGIN)).toBeNull()
  })
})

describe('拒绝站内但不属于活动内容的路径', () => {
  // 放开到"任意站内路径"会让调试台变成"用宿主身份打开站内任意页"的入口
  const rejected = [
    '/admin/events',
    '/profile',
    '/',
    '/draft', // 前缀本身，没有落在某个草稿目录下
    '/api/v1/events',
    '/data/2026spring/secret.pdf',
  ]

  for (const raw of rejected) {
    it(`拒绝 ${raw}`, () => {
      expect(resolveDebugSource(raw, ORIGIN)).toBeNull()
    })
  }
})

describe('拒绝空值与非法值', () => {
  it('拒绝缺失与空白', () => {
    expect(resolveDebugSource(undefined, ORIGIN)).toBeNull()
    expect(resolveDebugSource(null, ORIGIN)).toBeNull()
    expect(resolveDebugSource('', ORIGIN)).toBeNull()
    expect(resolveDebugSource('   ', ORIGIN)).toBeNull()
  })

  it('拒绝非字符串（查询参数可以是数组）', () => {
    expect(resolveDebugSource(['/draft/a/index.html'], ORIGIN)).toBeNull()
    expect(resolveDebugSource(42, ORIGIN)).toBeNull()
  })
})

describe('拒绝时给出的说明', () => {
  it('没给 src 时说清该怎么给', () => {
    expect(describeSourceRejection(undefined)).toContain('?src=')
  })

  it('给了但不合规时，说明里既含原值也含允许的前缀', () => {
    const text = describeSourceRejection('https://evil.example/x')
    expect(text).toContain('https://evil.example/x')
    for (const prefix of ALLOWED_SOURCE_PREFIXES) {
      expect(text).toContain(prefix)
    }
  })
})
