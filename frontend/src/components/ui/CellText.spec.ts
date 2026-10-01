/**
 * 定宽截断的文本单元格。
 *
 * 截断是 CSS 行为，而测试环境（happy-dom）没有布局引擎 —— 算不出 `scrollWidth`
 * 与 `clientWidth`，也就断言不了"省略号真的出现了"。所以这里分两层：
 *
 * - **结构**：文本确实经过这个组件渲染（而不是被别处直接插值），组件也照常显示全文
 * - **源码**：截断那三条 CSS 还在。这一层是静态断言，单独看很弱，因此配了变异测试
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import CellText from './CellText.vue'

const source = readFileSync(resolve(process.cwd(), 'src/components/ui/CellText.vue'), 'utf-8')

/** 一个真实的匿名标识：`a:` 加 36 字符 UUID */
const LONG_ID = 'a:550e8400-e29b-41d4-a716-446655440000'

describe('渲染', () => {
  it('原样显示传入的文本', () => {
    // 组件只负责视觉截断，不改数据 —— 完整内容在详情对话框里
    const wrapper = mount(CellText, { props: { text: LONG_ID } })
    expect(wrapper.text()).toBe(LONG_ID)
    wrapper.unmount()
  })

  it('超长文本也不截断数据本身', () => {
    const long = 'x'.repeat(500)
    const wrapper = mount(CellText, { props: { text: long } })
    expect(wrapper.text()).toHaveLength(500)
    wrapper.unmount()
  })

  it('空字符串不报错', () => {
    const wrapper = mount(CellText, { props: { text: '' } })
    expect(wrapper.text()).toBe('')
    wrapper.unmount()
  })
})

describe('截断样式', () => {
  /**
   * **断言前必须先剥掉注释。**
   *
   * 这个组件的注释里就写着 `min-width: 0` 这样的属性名，用 `toContain` 直接匹配会
   * 命中注释而不是声明 —— 于是把声明删掉测试依然全绿。这不是假设：变异测试真的
   * 放过了它一次。
   */
  const rule = (/\.cell\s*\{[^}]*\}/.exec(source)?.[0] ?? '').replace(
    /\/\*[\s\S]*?\*\//g,
    '',
  )

  it('有 overflow 与 text-overflow 两条', () => {
    // 少了任何一条都不会出现省略号：前者让内容被裁，后者把裁掉的部分变成 …
    expect(rule).toContain('overflow: hidden')
    expect(rule).toContain('text-overflow: ellipsis')
  })

  it('有 white-space: nowrap', () => {
    // 不禁止换行的话文本会折行，高度撑开而不是截断
    expect(rule).toContain('white-space: nowrap')
  })

  it('有 min-width: 0，让它在 flex 里能收缩', () => {
    // flex 子项默认 min-width:auto，不会收缩到内容宽度以下，省略号永远不出现。
    // 这一条最容易漏，而漏掉之后的表现（溢出而不是截断）很容易被忽略
    expect(rule).toMatch(/min-width:\s*0\s*;/)
  })

  it('不写死字号，交给所在单元格决定', () => {
    // 各列字号本来就不一样，组件替它们决定会让表格看起来参差不齐
    expect(rule).not.toContain('font-size')
  })

  it('剥注释这一步本身是有效的', () => {
    // 否则上面几条会悄悄退化成"匹配注释"，等于没测
    expect(rule).not.toContain('/*')
    expect(rule.length).toBeGreaterThan(0)
  })
})
