/**
 * 确认对话框的宿主组件。
 *
 * 重点是**焦点落在取消那一侧**：确认框存在的意义就是拦住一次误触，而
 * `<dialog>` 会把焦点（以及回车键的落点）给第一个可聚焦元素。顺序错了，
 * 一路回车下去就真的删掉了。
 */
import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { useConfirm } from '@/composables/useConfirm'

import ConfirmHost from './ConfirmHost.vue'

beforeEach(() => {
  useConfirm().clear()
})

afterEach(() => {
  useConfirm().clear()
})

/**
 * 打开一个确认框并把 DOM 刷出来。
 *
 * 返回的是 `{ pending }` 而**不是** promise 本身：这样 `await open(...)` 只等
 * DOM，不会连"用户的选择"一起等（那会直接死锁）。要断言结果时解构出来。
 */
async function open(wrapper: ReturnType<typeof mount>, options: Record<string, unknown> = {}) {
  const pending = useConfirm().ask({ title: '删除活动', message: '不可撤销。', ...options })
  await wrapper.vm.$nextTick()
  return { pending }
}

describe('渲染', () => {
  it('没有请求时对话框是关着的', () => {
    const wrapper = mount(ConfirmHost, { attachTo: document.body })
    expect((wrapper.find('dialog').element as HTMLDialogElement).open).toBe(false)
    wrapper.unmount()
  })

  it('有请求时打开并显示标题与正文', async () => {
    const wrapper = mount(ConfirmHost, { attachTo: document.body })
    await open(wrapper, { title: '删除活动', message: '提交与附件会一并移除。' })

    const dialog = wrapper.find('dialog').element as HTMLDialogElement
    expect(dialog.open).toBe(true)
    expect(wrapper.text()).toContain('删除活动')
    expect(wrapper.text()).toContain('提交与附件会一并移除。')
    wrapper.unmount()
  })

  it('用的是窄尺寸档', () => {
    // 确认框只有一两句话，720px 宽配一行字很难看，也不利于一眼看完
    const wrapper = mount(ConfirmHost, { attachTo: document.body })
    expect(wrapper.find('dialog').classes()).toContain('modal--sm')
    wrapper.unmount()
  })

  it('没有右上角的关闭按钮', async () => {
    /*
      它与"取消"语义完全重复。留着会让人多想一秒"这两个有什么区别"，
      而 Esc 与点遮罩仍然可关，退路一条没少。
    */
    const wrapper = mount(ConfirmHost, { attachTo: document.body })
    await open(wrapper)

    const header = wrapper.find('.modal__head')
    expect(header.exists()).toBe(true)
    expect(header.findAll('button')).toHaveLength(0)
    // 整框只有两个按钮：取消与确认
    expect(wrapper.findAll('button')).toHaveLength(2)
    wrapper.unmount()
  })

  it('按钮文案可以覆盖', async () => {
    const wrapper = mount(ConfirmHost, { attachTo: document.body })
    await open(wrapper, { confirmText: '删除', cancelText: '算了' })

    const labels = wrapper.findAll('button').map((b) => b.text())
    expect(labels).toContain('删除')
    expect(labels).toContain('算了')
    wrapper.unmount()
  })
})

describe('按钮的键盘语义', () => {
  it('取消排在确认前面，且带 autofocus', async () => {
    /*
      `<dialog>` 的 showModal 会把焦点给第一个可聚焦元素，而它同时也是**回车键的
      落点**。一路回车下去的结果必须是"什么都没发生"。
    */
    const wrapper = mount(ConfirmHost, { attachTo: document.body })
    await open(wrapper)

    const buttons = wrapper.findAll('.confirm__actions button')
    expect(buttons[0]!.text()).toBe('取消')
    expect(buttons[0]!.attributes('autofocus')).toBeDefined()
    // 确认那侧**不该**抢初始焦点
    expect(buttons[1]!.attributes('autofocus')).toBeUndefined()
    wrapper.unmount()
  })

  it('危险动作用警示色，普通动作用主色', async () => {
    const wrapper = mount(ConfirmHost, { attachTo: document.body })
    await open(wrapper, { danger: true })

    let buttons = wrapper.findAll('.confirm__actions button')
    expect(buttons[1]!.classes()).toContain('btn--danger')

    useConfirm().settle(false)
    await open(wrapper, { danger: false })
    buttons = wrapper.findAll('.confirm__actions button')
    expect(buttons[1]!.classes()).toContain('btn--primary')
    wrapper.unmount()
  })
})

describe('来源标记', () => {
  it('活动页发起的确认会标出来', async () => {
    /*
      与通知不同：这个框会拦住用户、索要一次点击，而按钮文案由活动页给。
      浏览器的原生 confirm 同样会标出源，这里是同一个道理。
    */
    const wrapper = mount(ConfirmHost, { attachTo: document.body })
    await open(wrapper, { fromEvent: true })

    expect(wrapper.find('.confirm__origin').exists()).toBe(true)
    wrapper.unmount()
  })

  it('宿主自己的确认不带这个标记', async () => {
    const wrapper = mount(ConfirmHost, { attachTo: document.body })
    await open(wrapper, { fromEvent: false })

    expect(wrapper.find('.confirm__origin').exists()).toBe(false)
    wrapper.unmount()
  })
})

describe('交互', () => {
  it('点确认兑现 true', async () => {
    const wrapper = mount(ConfirmHost, { attachTo: document.body })
    const { pending } = await open(wrapper)

    await wrapper.findAll('.confirm__actions button')[1]!.trigger('click')
    await expect(pending).resolves.toBe(true)
    wrapper.unmount()
  })

  it('点取消兑现 false', async () => {
    const wrapper = mount(ConfirmHost, { attachTo: document.body })
    const { pending } = await open(wrapper)

    await wrapper.findAll('.confirm__actions button')[0]!.trigger('click')
    await expect(pending).resolves.toBe(false)
    wrapper.unmount()
  })

  it('Esc 关闭等同于取消', async () => {
    // `<dialog>` 的 close 事件覆盖 Esc 与 close() 两条路径
    const wrapper = mount(ConfirmHost, { attachTo: document.body })
    const { pending } = await open(wrapper)

    await wrapper.find('dialog').trigger('close')
    await expect(pending).resolves.toBe(false)
    wrapper.unmount()
  })
})
