/**
 * 可拖拽的左右分栏。
 *
 * 宽度是受控的（`v-model`），所以这里验证的是**拖动被正确换算成了新宽度**，以及
 * 边界收得住 —— 拖过头把主区挤没是这类控件最常见的毛病。
 */
import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

import SplitPane from './SplitPane.vue'

function make(props: Record<string, unknown> = {}) {
  return mount(SplitPane, {
    props: { modelValue: 300, ...props },
    slots: { default: '<p class="left">列表</p>', side: '<p class="right">队列</p>' },
    attachTo: document.body,
  })
}

const handle = (wrapper: ReturnType<typeof make>) => wrapper.find('.split__handle')

/** 从某个横坐标开始拖到另一个横坐标 */
async function drag(wrapper: ReturnType<typeof make>, fromX: number, toX: number) {
  await handle(wrapper).trigger('pointerdown', { clientX: fromX })
  window.dispatchEvent(new PointerEvent('pointermove', { clientX: toX }))
  await wrapper.vm.$nextTick()
  window.dispatchEvent(new PointerEvent('pointerup'))
  await wrapper.vm.$nextTick()
}

/** 最后一次报上去的宽度 */
function lastWidth(wrapper: ReturnType<typeof make>): number | undefined {
  const events = wrapper.emitted('update:modelValue')
  return events?.[events.length - 1]?.[0] as number | undefined
}

describe('结构', () => {
  it('两侧都渲染出来', () => {
    const wrapper = make()
    expect(wrapper.find('.left').exists()).toBe(true)
    expect(wrapper.find('.right').exists()).toBe(true)
    wrapper.unmount()
  })

  it('侧栏宽度由 modelValue 决定', async () => {
    const wrapper = make({ modelValue: 320 })
    expect(wrapper.find('.split__side').attributes('style')).toContain('320px')

    await wrapper.setProps({ modelValue: 400 })
    expect(wrapper.find('.split__side').attributes('style')).toContain('400px')
    wrapper.unmount()
  })
})

describe('拖动', () => {
  it('往左拖，侧栏变宽', async () => {
    // 分隔线跟着指针走：指针左移 = 侧栏右边界左移 = 侧栏变宽
    const wrapper = make({ modelValue: 300 })
    await drag(wrapper, 500, 400)

    expect(lastWidth(wrapper)).toBe(400)
    wrapper.unmount()
  })

  it('往右拖，侧栏变窄', async () => {
    const wrapper = make({ modelValue: 300 })
    await drag(wrapper, 500, 560)

    expect(lastWidth(wrapper)).toBe(240)
    wrapper.unmount()
  })

  it('拖不过最小值', async () => {
    // 侧栏被拖到看不见就再也抓不回来了
    const wrapper = make({ modelValue: 300, min: 220 })
    await drag(wrapper, 500, 900)

    expect(lastWidth(wrapper)).toBe(220)
    wrapper.unmount()
  })

  it('拖不过最大值', async () => {
    const wrapper = make({ modelValue: 300, max: 400 })
    await drag(wrapper, 500, 0)

    expect(lastWidth(wrapper)).toBe(400)
    wrapper.unmount()
  })

  it('拖动过程中连续上报', async () => {
    const wrapper = make({ modelValue: 300 })
    await handle(wrapper).trigger('pointerdown', { clientX: 500 })

    window.dispatchEvent(new PointerEvent('pointermove', { clientX: 480 }))
    await wrapper.vm.$nextTick()
    window.dispatchEvent(new PointerEvent('pointermove', { clientX: 460 }))
    await wrapper.vm.$nextTick()

    expect(wrapper.emitted('update:modelValue')).toEqual([[320], [340]])
    wrapper.unmount()
  })

  it('没有按下时移动不改宽度', async () => {
    const wrapper = make({ modelValue: 300 })
    window.dispatchEvent(new PointerEvent('pointermove', { clientX: 100 }))
    await wrapper.vm.$nextTick()

    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
    wrapper.unmount()
  })

  it('松开之后移动不再改宽度', async () => {
    const wrapper = make({ modelValue: 300 })
    await drag(wrapper, 500, 450)
    const count = wrapper.emitted('update:modelValue')!.length

    window.dispatchEvent(new PointerEvent('pointermove', { clientX: 100 }))
    await wrapper.vm.$nextTick()

    expect(wrapper.emitted('update:modelValue')).toHaveLength(count)
    wrapper.unmount()
  })

  it('指针在分隔线之外松开也能结束拖动', async () => {
    // 拖到页面别处松手是常事，只听分隔线自己会漏掉
    const wrapper = make({ modelValue: 300 })
    await handle(wrapper).trigger('pointerdown', { clientX: 500 })
    window.dispatchEvent(new PointerEvent('pointerup'))
    await wrapper.vm.$nextTick()

    window.dispatchEvent(new PointerEvent('pointermove', { clientX: 100 }))
    await wrapper.vm.$nextTick()

    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
    wrapper.unmount()
  })

  it('卸载时移除文档级监听', async () => {
    const remove = vi.spyOn(window, 'removeEventListener')
    const wrapper = make()
    await handle(wrapper).trigger('pointerdown', { clientX: 500 })
    wrapper.unmount()

    expect(remove).toHaveBeenCalledWith('pointermove', expect.any(Function))
    expect(remove).toHaveBeenCalledWith('pointerup', expect.any(Function))
    remove.mockRestore()
  })
})

describe('键盘', () => {
  it('方向键也能调 —— 纯拖拽对键盘用户等于没有', async () => {
    const wrapper = make({ modelValue: 300 })
    await handle(wrapper).trigger('keydown', { key: 'ArrowLeft' })

    expect(lastWidth(wrapper)).toBe(310)
    wrapper.unmount()
  })

  it('按住 Shift 步子更大', async () => {
    const wrapper = make({ modelValue: 300 })
    await handle(wrapper).trigger('keydown', { key: 'ArrowRight', shiftKey: true })

    expect(lastWidth(wrapper)).toBe(260)
    wrapper.unmount()
  })

  it('键盘同样受边界约束', async () => {
    const wrapper = make({ modelValue: 220, min: 220 })
    await handle(wrapper).trigger('keydown', { key: 'ArrowRight' })

    expect(lastWidth(wrapper)).toBe(220)
    wrapper.unmount()
  })

  it('别的键不管', async () => {
    const wrapper = make()
    await handle(wrapper).trigger('keydown', { key: 'a' })

    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
    wrapper.unmount()
  })
})

describe('无障碍', () => {
  it('分隔线是 separator，并给出取值范围', () => {
    const wrapper = make({ modelValue: 300, min: 220 })
    const el = handle(wrapper)

    expect(el.attributes('role')).toBe('separator')
    expect(el.attributes('aria-orientation')).toBe('vertical')
    expect(el.attributes('aria-valuenow')).toBe('300')
    expect(el.attributes('aria-valuemin')).toBe('220')
    wrapper.unmount()
  })

  it('分隔线可以被 Tab 聚焦', () => {
    const wrapper = make()
    expect(handle(wrapper).attributes('tabindex')).toBe('0')
    wrapper.unmount()
  })
})
