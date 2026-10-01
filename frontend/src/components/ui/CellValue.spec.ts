/**
 * 定宽截断 + 展开看全文的单元格。
 *
 * 这一组盯的是三件事：截断是否真的会发生（而不是把表格撑开）、悬浮与点击两种展开
 * 方式的区别（一个会自己关、一个不会），以及面板不会自己跑掉或留下挡点击的空层。
 */
import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

import CellValue from './CellValue.vue'

const LONG = '{"name":"张三","intro":"一段很长的自我介绍，长到足以被截断"}' 

function make(props: Record<string, unknown> = {}) {
  return mount(CellValue, {
    props: { text: LONG, label: '完整提交内容', ...props },
    attachTo: document.body,
  })
}

const more = (wrapper: ReturnType<typeof make>) => wrapper.find('.cell__more')
const panel = (wrapper: ReturnType<typeof make>) => wrapper.find('.cell__panel')

describe('截断', () => {
  it('文本放在会被截断的元素里', () => {
    const wrapper = make()
    const text = wrapper.find('.cell__text')

    expect(text.text()).toBe(LONG)
    // 截断靠 CSS（overflow + ellipsis + nowrap），这里只能断言结构到位；
    // happy-dom 没有布局引擎，算不出 scrollWidth
    expect(text.classes()).toContain('cell__text')
    wrapper.unmount()
  })

  it('默认收起', () => {
    const wrapper = make()
    expect(panel(wrapper).exists()).toBe(false)
    wrapper.unmount()
  })
})

describe('悬浮展开', () => {
  it('鼠标进入按钮区域就展开', async () => {
    const wrapper = make()
    await wrapper.find('.cell__anchor').trigger('mouseenter')

    expect(panel(wrapper).exists()).toBe(true)
    expect(panel(wrapper).text()).toContain('张三')
    wrapper.unmount()
  })

  it('鼠标移开就收起', async () => {
    const wrapper = make()
    const anchor = wrapper.find('.cell__anchor')

    await anchor.trigger('mouseenter')
    await anchor.trigger('mouseleave')
    expect(panel(wrapper).exists()).toBe(false)
    wrapper.unmount()
  })

  it('悬浮触发点只在按钮与面板上，不在整个单元格上', async () => {
    // 否则鼠标横扫一行会让每列的面板依次弹出，比截断本身更烦人
    const wrapper = make()
    await wrapper.find('.cell').trigger('mouseenter')
    expect(panel(wrapper).exists()).toBe(false)
    wrapper.unmount()
  })

  it('展开的是 detail，不是被截断的 text', async () => {
    const wrapper = make({ text: '摘要', detail: '摘要\n\n原始数据\n{"a":1}' })
    await wrapper.find('.cell__anchor').trigger('mouseenter')

    expect(panel(wrapper).text()).toContain('原始数据')
    wrapper.unmount()
  })

  it('没给 detail 时展开 text 本身', async () => {
    const wrapper = make({ text: '只有这一段' })
    await wrapper.find('.cell__anchor').trigger('mouseenter')

    expect(panel(wrapper).text()).toBe('只有这一段')
    wrapper.unmount()
  })
})

describe('点击钉住', () => {
  it('点一下钉住，鼠标移开也不关', async () => {
    // 不钉住就没法选中复制
    const wrapper = make()
    const anchor = wrapper.find('.cell__anchor')

    await more(wrapper).trigger('click')
    await anchor.trigger('mouseleave')

    expect(panel(wrapper).exists()).toBe(true)
    expect(panel(wrapper).classes()).toContain('cell__panel--pinned')
    wrapper.unmount()
  })

  it('再点一下取消钉住并收起', async () => {
    const wrapper = make()
    await more(wrapper).trigger('click')
    await more(wrapper).trigger('click')

    expect(panel(wrapper).exists()).toBe(false)
    wrapper.unmount()
  })

  it('按钮反映展开状态', async () => {
    const wrapper = make()
    expect(more(wrapper).attributes('aria-expanded')).toBe('false')

    await more(wrapper).trigger('click')
    expect(more(wrapper).attributes('aria-expanded')).toBe('true')
    wrapper.unmount()
  })
})

describe('关闭方式', () => {
  it('Escape 关闭', async () => {
    const wrapper = make()
    await more(wrapper).trigger('click')

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await wrapper.vm.$nextTick()
    expect(panel(wrapper).exists()).toBe(false)
    wrapper.unmount()
  })

  it('点组件外部关闭', async () => {
    const wrapper = make()
    await more(wrapper).trigger('click')

    document.body.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    await wrapper.vm.$nextTick()
    expect(panel(wrapper).exists()).toBe(false)
    wrapper.unmount()
  })

  it('在组件内部按下不关闭', async () => {
    const wrapper = make()
    await more(wrapper).trigger('click')

    await more(wrapper).trigger('mousedown')
    await wrapper.vm.$nextTick()
    expect(panel(wrapper).exists()).toBe(true)
    wrapper.unmount()
  })

  it('Escape 之后再悬浮仍能展开', async () => {
    // 关闭要清掉钉住状态，否则下次悬浮会被 pinned 挡住
    const wrapper = make()
    const anchor = wrapper.find('.cell__anchor')

    await more(wrapper).trigger('click')
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await wrapper.vm.$nextTick()

    await anchor.trigger('mouseenter')
    expect(panel(wrapper).exists()).toBe(true)
    await anchor.trigger('mouseleave')
    expect(panel(wrapper).exists()).toBe(false)
    wrapper.unmount()
  })
})

describe('无障碍与清理', () => {
  it('按钮有说明展开内容的名称', () => {
    const wrapper = make({ label: '完整提交内容' })
    expect(more(wrapper).attributes('aria-label')).toBe('完整提交内容')
    wrapper.unmount()
  })

  it('按钮与面板用 aria-controls 关联', async () => {
    const wrapper = make()
    await more(wrapper).trigger('click')

    const id = more(wrapper).attributes('aria-controls')
    expect(id).toBeTruthy()
    expect(panel(wrapper).attributes('id')).toBe(id)
    wrapper.unmount()
  })

  it('卸载时移除文档级监听', () => {
    const remove = vi.spyOn(document, 'removeEventListener')
    const wrapper = make()
    wrapper.unmount()

    expect(remove).toHaveBeenCalledWith('mousedown', expect.any(Function))
    expect(remove).toHaveBeenCalledWith('keydown', expect.any(Function))
    remove.mockRestore()
  })
})
