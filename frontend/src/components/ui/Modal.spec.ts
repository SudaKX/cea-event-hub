/**
 * 模态对话框外壳。
 *
 * 用原生 `<dialog>` + `showModal()`，所以这里验证的是"开合"这条容易做错的链路，
 * 以及一条踩过的坑：**`<dialog>` 上不能写 `display`**。
 *
 * 测试环境没有布局引擎，算不出"可见/不可见"，所以那条只能读源码断言 —— 因此配了
 * 变异测试。
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import Modal from './Modal.vue'

const source = readFileSync(resolve(process.cwd(), 'src/components/ui/Modal.vue'), 'utf-8')

/** 剥掉注释再断言：注释里出现的属性名会让 toContain 假阳性 */
const withoutComments = (css: string) => css.replace(/\/\*[\s\S]*?\*\//g, '')

function make(props: Record<string, unknown> = {}) {
  return mount(Modal, {
    props: { open: false, title: '标题', ...props },
    slots: { default: '<p class="content">内容</p>' },
    attachTo: document.body,
  })
}

const dialogEl = (wrapper: ReturnType<typeof make>) =>
  wrapper.find('dialog').element as HTMLDialogElement

describe('开合', () => {
  it('初始关闭，且 dialog 始终在 DOM 里', () => {
    // showModal() 需要一个已挂载的元素，所以不能用 v-if 把整块摘掉
    const wrapper = make()
    expect(wrapper.find('dialog').exists()).toBe(true)
    expect(dialogEl(wrapper).open).toBe(false)
    wrapper.unmount()
  })

  it('open 为真时打开', async () => {
    const wrapper = make()
    await wrapper.setProps({ open: true })

    expect(dialogEl(wrapper).open).toBe(true)
    wrapper.unmount()
  })

  it('挂载时就已经是打开状态也能显示', async () => {
    // immediate 那次 watch 跑在模板 ref 绑定之前，少了 onMounted 补的那次就静默不显示
    const wrapper = make({ open: true })

    expect(dialogEl(wrapper).open).toBe(true)
    wrapper.unmount()
  })

  it('open 变回假时关闭', async () => {
    const wrapper = make({ open: true })
    await wrapper.setProps({ open: false })

    expect(dialogEl(wrapper).open).toBe(false)
    wrapper.unmount()
  })

  it('关闭事件往上抛，交给调用方清状态', async () => {
    // Esc 与 close() 都走 <dialog> 的 close 事件
    const wrapper = make({ open: true })
    dialogEl(wrapper).close()
    await wrapper.vm.$nextTick()

    expect(wrapper.emitted('close')).toHaveLength(1)
    wrapper.unmount()
  })

  it('点遮罩关闭', async () => {
    // <dialog> 默认不这么做，但用户预期是点了外面就该关
    const wrapper = make({ open: true })
    await wrapper.find('dialog').trigger('click')

    expect(wrapper.emitted('close')).toHaveLength(1)
    wrapper.unmount()
  })

  it('点内容不关闭', async () => {
    const wrapper = make({ open: true })
    await wrapper.find('.content').trigger('click')

    expect(wrapper.emitted('close')).toBeUndefined()
    wrapper.unmount()
  })

  it('关闭按钮往上抛', async () => {
    const wrapper = make({ open: true })
    const close = wrapper.findAll('button').find((b) => b.text() === '关闭')!
    await close.trigger('click')

    expect(wrapper.emitted('close')).toHaveLength(1)
    wrapper.unmount()
  })
})

describe('内容与无障碍', () => {
  it('渲染标题与插槽', () => {
    const wrapper = make({ open: true, title: '审核动作说明' })

    expect(wrapper.find('.modal__title').text()).toBe('审核动作说明')
    expect(wrapper.find('.content').exists()).toBe(true)
    wrapper.unmount()
  })

  it('标题同时作为无障碍名称', () => {
    const wrapper = make({ open: true, title: '标题' })
    const id = wrapper.find('dialog').attributes('aria-labelledby')

    expect(id).toBeTruthy()
    expect(wrapper.find(`#${id}`).text()).toBe('标题')
    wrapper.unmount()
  })

  it('关闭时内容整块不渲染，只留一个空的 dialog 壳', () => {
    // 内容跟着 open 走，所以关闭态连 DOM 都没有，不存在"看不见但占位"
    const wrapper = make()
    expect(wrapper.find('.modal__body').exists()).toBe(false)
    expect(wrapper.find('dialog').element.childElementCount).toBe(0)
    wrapper.unmount()
  })
})

describe('关闭时不留占位', () => {
  /*
    对应一个真实缺陷：页面上凭空多出一块空卡片。

    原因是把 `display: flex` 写在了 `<dialog>` 上 —— 作者样式的 `display` 会盖掉
    浏览器默认的 `dialog:not([open]) { display: none }`，于是关闭状态的对话框
    依然被渲染。布局挪进内层容器之后，那条默认样式始终有效。
  */
  const dialogRule = withoutComments(/\.modal\s*\{[^}]*\}/.exec(source)?.[0] ?? '')
  const bodyRule = withoutComments(/\.modal__body\s*\{[^}]*\}/.exec(source)?.[0] ?? '')

  it('dialog 自身的样式里没有 display', () => {
    expect(dialogRule.length).toBeGreaterThan(0)
    expect(dialogRule).not.toContain('display')
  })

  it('flex 布局在内层容器上', () => {
    expect(bodyRule).toContain('display: flex')
    expect(bodyRule).toContain('flex-direction: column')
  })

  it('模板里确实套了那层内层容器', () => {
    const wrapper = make({ open: true })
    expect(wrapper.find('dialog > .modal__body').exists()).toBe(true)
    wrapper.unmount()
  })
})
