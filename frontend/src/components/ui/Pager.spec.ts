/**
 * 分页控件。
 *
 * 它不取数据，只把"第几页""每页多少条"报给调用方。测试重点因此落在两处：
 * **区间显示算得对不对**（末页那段最容易错），以及**什么时候不该发事件**
 * （已经在第一页还点"上一页"、已经在末页还点"下一页"）。
 */
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import Pager from './Pager.vue'

function make(props: Record<string, unknown> = {}) {
  return mount(Pager, {
    props: { page: 1, pageSize: 20, total: 137, ...props },
    attachTo: document.body,
  })
}

/** 按 aria-label 或文本找按钮 */
function button(wrapper: ReturnType<typeof make>, label: string) {
  const byLabel = wrapper.findAll('button').find((b) => b.attributes('aria-label') === label)
  if (byLabel) return byLabel
  return wrapper.findAll('button').find((b) => b.text() === label)!
}

describe('区间显示', () => {
  it('第一页显示 1 到每页条数', () => {
    const wrapper = make({ page: 1 })
    expect(wrapper.find('.pager__range').text()).toContain('第 1–20 条')
    expect(wrapper.find('.pager__range').text()).toContain('共 137 条')
    wrapper.unmount()
  })

  it('末页只到总数，不会越过', () => {
    // 137 条、每页 20 → 第 7 页是 121–137，不是 121–140
    const wrapper = make({ page: 7 })
    expect(wrapper.find('.pager__range').text()).toContain('第 121–137 条')
    wrapper.unmount()
  })

  it('整除时末页正好铺满', () => {
    const wrapper = make({ page: 5, total: 100 })
    expect(wrapper.find('.pager__range').text()).toContain('第 81–100 条')
    wrapper.unmount()
  })

  it('总数为 0 时整块不显示', () => {
    // 列表本身已经有"还没有提交"的空状态，分页器再显示一遍是噪音
    const wrapper = make({ total: 0 })
    expect(wrapper.find('.pager').exists()).toBe(false)
    wrapper.unmount()
  })
})

describe('页数', () => {
  it('向上取整', () => {
    const wrapper = make({ total: 137, pageSize: 20 })
    expect(wrapper.find('.pager__position').text()).toBe('1 / 7')
    wrapper.unmount()
  })

  it('刚好铺满时不出现空页', () => {
    const wrapper = make({ total: 100, pageSize: 20 })
    expect(wrapper.find('.pager__position').text()).toBe('1 / 5')
    wrapper.unmount()
  })

  it('只有一页时页码显示 1 / 1', () => {
    const wrapper = make({ total: 3, pageSize: 20 })
    expect(wrapper.find('.pager__position').text()).toBe('1 / 1')
    wrapper.unmount()
  })

  it('每页条数为 0 也不会除出 Infinity', () => {
    const wrapper = make({ total: 10, pageSize: 0 })
    expect(wrapper.find('.pager__position').text()).toBe('1 / 10')
    wrapper.unmount()
  })
})

describe('翻页', () => {
  it('下一页发出当前页 +1', async () => {
    const wrapper = make({ page: 3 })
    await button(wrapper, '下一页').trigger('click')
    expect(wrapper.emitted('update:page')).toEqual([[4]])
    wrapper.unmount()
  })

  it('上一页发出当前页 -1', async () => {
    const wrapper = make({ page: 3 })
    await button(wrapper, '上一页').trigger('click')
    expect(wrapper.emitted('update:page')).toEqual([[2]])
    wrapper.unmount()
  })

  it('第一页与最后一页', async () => {
    const wrapper = make({ page: 4 })
    await button(wrapper, '第一页').trigger('click')
    await button(wrapper, '最后一页').trigger('click')
    expect(wrapper.emitted('update:page')).toEqual([[1], [7]])
    wrapper.unmount()
  })

  it('已经在第一页时，上一页与第一页都不可点', () => {
    const wrapper = make({ page: 1 })
    expect(button(wrapper, '上一页').attributes('disabled')).toBeDefined()
    expect(button(wrapper, '第一页').attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it('已经在最后一页时，下一页与最后一页都不可点', () => {
    const wrapper = make({ page: 7 })
    expect(button(wrapper, '下一页').attributes('disabled')).toBeDefined()
    expect(button(wrapper, '最后一页').attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it('只有一页时四个按钮都不可点', () => {
    const wrapper = make({ page: 1, total: 5 })
    for (const label of ['第一页', '上一页', '下一页', '最后一页']) {
      expect(button(wrapper, label).attributes('disabled')).toBeDefined()
    }
    wrapper.unmount()
  })

  it('重复点当前页不会发出事件', async () => {
    // 发了就会白跑一次请求
    const wrapper = make({ page: 7 })
    await button(wrapper, '下一页').trigger('click')
    expect(wrapper.emitted('update:page')).toBeUndefined()
    wrapper.unmount()
  })
})

describe('每页条数', () => {
  it('列出可选项', () => {
    const wrapper = make()
    expect(wrapper.find('.pager__size').exists()).toBe(true)
    wrapper.unmount()
  })

  it('传空数组就不显示这个选择器', () => {
    const wrapper = make({ pageSizeOptions: [] })
    expect(wrapper.find('.pager__size').exists()).toBe(false)
    wrapper.unmount()
  })

  it('改条数时同时发出新条数与页码 1', async () => {
    // 只改条数不回第一页的话，会停在一个新分页下可能不存在的页码上
    const wrapper = make({ page: 5 })
    await wrapper.find('.pager__size .select__trigger').trigger('click')
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()

    const option = wrapper.findAll('[role="option"]').find((o) => o.text() === '50 / 页')!
    await option.trigger('click')

    expect(wrapper.emitted('update:pageSize')).toEqual([[50]])
    expect(wrapper.emitted('update:page')).toEqual([[1]])
    wrapper.unmount()
  })
})

describe('无障碍', () => {
  it('整块是带名称的导航区', () => {
    const wrapper = make()
    const nav = wrapper.find('nav')
    expect(nav.exists()).toBe(true)
    expect(nav.attributes('aria-label')).toBe('分页')
    wrapper.unmount()
  })

  it('首末页按钮有文字之外的名称', () => {
    // 它们只显示 « »，读屏念不出来
    const wrapper = make({ page: 4 })
    expect(button(wrapper, '第一页').exists()).toBe(true)
    expect(button(wrapper, '最后一页').exists()).toBe(true)
    wrapper.unmount()
  })
})
