/**
 * 通知宿主组件。
 *
 * 除了渲染，这里盯的是**无障碍角色**：失败要打断朗读、其余要等用户停下来。
 * 全用 `alert` 会很吵，全用 `status` 又会让失败被淹没。
 */
import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useToast } from '@/composables/useToast'

import ToastHost from './ToastHost.vue'

beforeEach(() => {
  useToast().clear()
})

afterEach(() => {
  useToast().clear()
})

describe('渲染', () => {
  it('没有通知时不渲染任何条目', () => {
    const wrapper = mount(ToastHost)
    expect(wrapper.findAll('.toast')).toHaveLength(0)
    wrapper.unmount()
  })

  it('按顺序渲染多条', async () => {
    const wrapper = mount(ToastHost)
    useToast().ok('第一条')
    useToast().fail('第二条')
    await wrapper.vm.$nextTick()

    const texts = wrapper.findAll('.toast__text').map((node) => node.text())
    expect(texts).toEqual(['第一条', '第二条'])
    wrapper.unmount()
  })

  it('语气体现在 class 上，配色不靠文字颜色单独承载', async () => {
    const wrapper = mount(ToastHost)
    useToast().ok('成了')
    useToast().fail('没成')
    useToast().info('知道了')
    await wrapper.vm.$nextTick()

    expect(wrapper.findAll('.toast')[0]!.classes()).toContain('toast--ok')
    expect(wrapper.findAll('.toast')[1]!.classes()).toContain('toast--error')
    expect(wrapper.findAll('.toast')[2]!.classes()).toContain('toast--info')
    wrapper.unmount()
  })

  it('每条都有图标，且图标按语气换形状', async () => {
    /*
      图标是"一眼看出成功还是失败"的主要手段 —— 左侧那条 4px 色边在余光里几乎
      看不见。三种语气必须给出不同的图形，不能是同一个图标换个颜色。
    */
    const wrapper = mount(ToastHost)
    useToast().ok('成了')
    useToast().fail('没成')
    useToast().info('知道了')
    await wrapper.vm.$nextTick()

    const paths = wrapper.findAll('.toast__icon svg').map((svg) => svg.html())
    expect(paths).toHaveLength(3)
    expect(new Set(paths).size).toBe(3)
    wrapper.unmount()
  })

  it('图标对读屏隐藏 —— 含义由文本承载', async () => {
    const wrapper = mount(ToastHost)
    useToast().ok('成了')
    await wrapper.vm.$nextTick()

    expect(wrapper.find('.toast__icon').attributes('aria-hidden')).toBe('true')
    wrapper.unmount()
  })
})

describe('无障碍', () => {
  it('失败用 alert，其余用 status', async () => {
    /*
      alert 是 assertive，会打断读屏当前的朗读；status 是 polite，等用户停下来。
      失败值得打断，成功不值得。
    */
    const wrapper = mount(ToastHost)
    useToast().ok('成了')
    useToast().fail('没成')
    await wrapper.vm.$nextTick()

    const items = wrapper.findAll('.toast')
    expect(items[0]!.attributes('role')).toBe('status')
    expect(items[1]!.attributes('role')).toBe('alert')
    wrapper.unmount()
  })

  it('关闭按钮有无障碍名称', async () => {
    const wrapper = mount(ToastHost)
    useToast().ok('成了')
    await wrapper.vm.$nextTick()

    expect(wrapper.find('.toast__close').attributes('aria-label')).toBe('关闭通知')
    wrapper.unmount()
  })
})

describe('交互', () => {
  it('点关闭按钮移除该条', async () => {
    const wrapper = mount(ToastHost)
    useToast().ok('成了')
    await wrapper.vm.$nextTick()

    await wrapper.find('.toast__close').trigger('click')
    await wrapper.vm.$nextTick()

    expect(wrapper.findAll('.toast')).toHaveLength(0)
    wrapper.unmount()
  })

  it('指针进入暂停计时，离开后重新计时', async () => {
    vi.useFakeTimers()
    try {
      const wrapper = mount(ToastHost)
      useToast().ok('成了')
      await wrapper.vm.$nextTick()

      await wrapper.find('.toast').trigger('pointerenter')
      vi.advanceTimersByTime(10_000)
      expect(wrapper.findAll('.toast')).toHaveLength(1)

      await wrapper.find('.toast').trigger('pointerleave')
      vi.advanceTimersByTime(3200)
      await wrapper.vm.$nextTick()
      expect(wrapper.findAll('.toast')).toHaveLength(0)
      wrapper.unmount()
    } finally {
      vi.useRealTimers()
    }
  })
})
