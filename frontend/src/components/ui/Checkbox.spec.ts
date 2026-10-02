/**
 * 复选框。
 *
 * ## 这一组为什么对着**可见的方块**触发事件
 *
 * 曾经把 `@pointerdown` 挂在视觉隐藏的 `<input>` 上，测试也就对着它触发 —— 全部
 * 通过，而真实浏览器里**一次都没生效**：输入框只有 1px 且被 `clip-path` 裁掉，
 * 点击落在旁边的方块上，事件根本不经过它。
 *
 * 所以这里的指针事件一律打在 `.checkbox` / `.checkbox__box` 上，也就是用户真正
 * 点到的地方。这个差别就是"测试全绿但功能是坏的"的来源。
 */
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import Checkbox from './Checkbox.vue'

function make(props: Record<string, unknown> = {}) {
  return mount(Checkbox, {
    props: { modelValue: false, label: '选择', ...props },
    attachTo: document.body,
  })
}

const input = (wrapper: ReturnType<typeof make>) =>
  wrapper.find('input[type="checkbox"]').element as HTMLInputElement

describe('语义', () => {
  it('真的有一个 checkbox 输入框', () => {
    // 只是视觉上藏起来，不能用 display:none 把键盘可达性一起干掉
    const wrapper = make()
    expect(wrapper.find('input[type="checkbox"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('modelValue 决定是否勾选', async () => {
    const wrapper = make({ modelValue: true })
    expect(input(wrapper).checked).toBe(true)

    await wrapper.setProps({ modelValue: false })
    expect(input(wrapper).checked).toBe(false)
    wrapper.unmount()
  })

  it('label 作为无障碍名称', () => {
    const wrapper = make({ label: '选择提交 42' })
    expect(wrapper.find('input').attributes('aria-label')).toBe('选择提交 42')
    wrapper.unmount()
  })

  it('勾选态反映在外观上', async () => {
    const wrapper = make({ modelValue: false })
    expect(wrapper.find('.checkbox').classes()).not.toContain('checkbox--on')

    await wrapper.setProps({ modelValue: true })
    expect(wrapper.find('.checkbox').classes()).toContain('checkbox--on')
    wrapper.unmount()
  })
})

describe('鼠标按下', () => {
  it('点**可见的方块**就能翻转 —— 用户真正点到的地方', async () => {
    // 这条就是那个 bug 的回归测试：处理函数挂错元素时它会失败
    const wrapper = make({ modelValue: false })
    await wrapper.find('.checkbox__box').trigger('pointerdown')

    expect(wrapper.emitted('update:modelValue')).toEqual([[true]])
    wrapper.unmount()
  })

  it('点根元素的任何位置也一样', async () => {
    const wrapper = make({ modelValue: false })
    await wrapper.find('.checkbox').trigger('pointerdown')

    expect(wrapper.emitted('update:modelValue')).toEqual([[true]])
    wrapper.unmount()
  })

  it('已勾选时按下变成取消', async () => {
    const wrapper = make({ modelValue: true })
    await wrapper.find('.checkbox__box').trigger('pointerdown')

    expect(wrapper.emitted('update:modelValue')).toEqual([[false]])
    wrapper.unmount()
  })

  it('一次按下只翻转一次', async () => {
    /*
      外层不能用 `<label>`：它会把点击转发给输入框，和这里的处理叠加就是两次翻转。
      一次按下只应产生一个 update。
    */
    const wrapper = make({ modelValue: false })
    await wrapper.find('.checkbox__box').trigger('pointerdown')

    expect(wrapper.emitted('update:modelValue')).toHaveLength(1)
    wrapper.unmount()
  })

  it('同时报出 press，带上这次的新值', () => {
    // 父级靠它记下起点，并知道要把滑过的项刷成什么
    const wrapper = make({ modelValue: false })
    const event = new Event('pointerdown', { bubbles: true, cancelable: true })
    wrapper.find('.checkbox').element.dispatchEvent(event)

    expect(wrapper.emitted('press')).toEqual([[true]])
    wrapper.unmount()
  })

  it('按下时阻止默认行为', () => {
    // 不拦的话，按住拖动会一路选中沿途的文字
    const wrapper = make()
    const event = new Event('pointerdown', { bubbles: true, cancelable: true })
    wrapper.find('.checkbox').element.dispatchEvent(event)

    expect(event.defaultPrevented).toBe(true)
    wrapper.unmount()
  })
})

describe('键盘', () => {
  it('change 事件（空格键触发）照常往上抛', async () => {
    // 鼠标路径被 preventDefault 拦住了，所以 change 只可能来自键盘
    const wrapper = make({ modelValue: false })
    const element = input(wrapper)
    element.checked = true
    await wrapper.find('input').trigger('change')

    expect(wrapper.emitted('update:modelValue')).toEqual([[true]])
    wrapper.unmount()
  })
})

describe('禁用', () => {
  it('输入框被禁用，外观有对应标记', () => {
    const wrapper = make({ disabled: true })
    expect(input(wrapper).disabled).toBe(true)
    expect(wrapper.find('.checkbox').classes()).toContain('checkbox--off')
    wrapper.unmount()
  })

  it('按下不发任何事件', async () => {
    const wrapper = make({ disabled: true })
    await wrapper.find('.checkbox__box').trigger('pointerdown')

    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
    expect(wrapper.emitted('press')).toBeUndefined()
    wrapper.unmount()
  })
})
