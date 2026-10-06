/**
 * 邀请码的两张界面（任务 4.1 / 4.3 / 5.1）。
 *
 * 管理台那个 Tab 管全部；个人中心那张卡片管自己名下的。**两者对不同角色显示不同
 * 的东西** —— 那是这一组最容易写歪的地方，因此逐条钉住。
 */
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

const api = vi.hoisted(() => ({
  listMyInvitations: vi.fn(),
  issueInvitation: vi.fn(),
  deleteInvitation: vi.fn(),
  listAllInvitations: vi.fn(),
  createInvitation: vi.fn(),
  revokeInvitation: vi.fn(),
  readSwitches: vi.fn(),
  writeSwitch: vi.fn(),
}))

vi.mock('@/api/invitations', () => api)

import InvitationsView from './InvitationsView.vue'
import InvitationCard from '@/views/profile/InvitationCard.vue'
import { useAuthStore } from '@/stores/auth'
import { useConfirm } from '@/composables/useConfirm'
import { useToast } from '@/composables/useToast'
import type { InvitationCode, User } from '@/types/api'

function code(overrides: Partial<InvitationCode> = {}): InvitationCode {
  return {
    id: 1,
    token: 'ABCDEFGHJK',
    name: '给张三',
    max_uses: 1,
    used_count: 0,
    expires_at: new Date(Date.now() + 7 * 86400_000).toISOString(),
    revoked_at: null,
    created_at: new Date().toISOString(),
    is_platform: false,
    usages: [],
    ...overrides,
  }
}

function account(role: 'user' | 'admin'): User {
  return {
    id: 1,
    username: role === 'admin' ? 'root' : 'alice',
    display_name: role === 'admin' ? 'Root' : 'Alice',
    role,
    email: 'a@example.com',
    email_verified: true,
  }
}

async function mountWith(component: unknown, role: 'user' | 'admin') {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: { template: '<div />' } },
      { path: '/profile', name: 'profile', component: { template: '<div />' } },
      {
        path: '/admin/invitations',
        name: 'admin-invitations',
        component: { template: '<div />' },
      },
    ],
  })
  await router.push('/')
  await router.isReady()

  const wrapper = mount(component as never, {
    global: { plugins: [router, createPinia()] },
    attachTo: document.body,
    // 父组件就是这么给它外观的（`panel` 提供表面）；见"父组件传下来的 class"那条
    attrs: { class: 'panel' },
  }) as ReturnType<typeof mount>
  // 挂载之后再设：mount 里那个 pinia 才是组件用的实例
  useAuthStore().setUser(account(role))
  await wrapper.vm.$nextTick()
  await vi.waitFor(() => expect(api.listMyInvitations).toHaveBeenCalled())
  return wrapper
}

beforeEach(() => {
  vi.clearAllMocks()
  setActivePinia(createPinia())
  useToast().clear()
  useConfirm().clear()
  api.listMyInvitations.mockResolvedValue([])
  api.listAllInvitations.mockResolvedValue([])
  api.readSwitches.mockResolvedValue({
    invitations_paused: false,
    invitation_issuance_paused: false,
  })
})

describe('管理台：邀请码 Tab', () => {
  /**
   * 挂载管理页。
   *
   * **数据必须在挂载前给**：这一页在 `onMounted` 里取一次列表，挂载之后再改 mock
   * 不会重拉 —— 那样的用例会靠默认的空列表通过（或失败），证明不了任何事。
   * 这个坑本会话已经踩过一次（提交者显示那组）。
   */
  async function mountAdmin(codes: InvitationCode[] = []) {
    api.listAllInvitations.mockResolvedValue(codes)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/', name: 'home', component: { template: '<div />' } }],
    })
    await router.push('/')
    await router.isReady()
    const wrapper = mount(InvitationsView, {
      global: { plugins: [router, createPinia()] },
      attachTo: document.body,
    })
    await vi.waitFor(() => expect(api.listAllInvitations).toHaveBeenCalled())
    await wrapper.vm.$nextTick()
    return wrapper
  }

  it('列出四列：token、名称、有效期、可用次数', async () => {
    const wrapper = await mountAdmin([
      code({ max_uses: 5, used_count: 2, name: '给社团' }),
    ])

    const headers = wrapper.findAll('th').map((th) => th.text())
    expect(headers).toContain('token')
    expect(headers).toContain('名称')
    expect(headers).toContain('有效期至')
    expect(headers).toContain('可用次数')

    const cells = wrapper.find('tbody tr').findAll('td').map((td) => td.text())
    expect(cells).toContain('ABCDEFGHJK')
    expect(cells).toContain('给社团')
    expect(cells).toContain('5')
    expect(cells).toContain('2')
    wrapper.unmount()
  })

  it('创建时提交四项', async () => {
    api.createInvitation.mockResolvedValue(code())
    const wrapper = await mountAdmin()

    const inputs = wrapper.findAll('form input')
    await inputs[0]!.setValue('CUSTOM-TOKEN')
    await inputs[1]!.setValue('给社团')
    await inputs[2]!.setValue('30')
    await inputs[3]!.setValue('10')
    await wrapper.find('form').trigger('submit')

    await vi.waitFor(() =>
      expect(api.createInvitation).toHaveBeenCalledWith({
        token: 'CUSTOM-TOKEN',
        name: '给社团',
        days: 30,
        max_uses: 10,
      }),
    )
    wrapper.unmount()
  })

  it('token 留空时交给服务端生成', async () => {
    api.createInvitation.mockResolvedValue(code())
    const wrapper = await mountAdmin()

    const inputs = wrapper.findAll('form input')
    await inputs[1]!.setValue('自动')
    await wrapper.find('form').trigger('submit')

    await vi.waitFor(() =>
      expect(api.createInvitation).toHaveBeenCalledWith(
        expect.objectContaining({ token: undefined }),
      ),
    )
    wrapper.unmount()
  })

  it('失效要先确认，且确认框是 danger', async () => {
    api.revokeInvitation.mockResolvedValue(code({ revoked_at: new Date().toISOString() }))
    const wrapper = await mountAdmin([code()])

    const button = wrapper.findAll('button').find((b) => b.text().includes('失效'))
    expect(button, '没有失效按钮').toBeTruthy()
    await button!.trigger('click')
    await vi.waitFor(() => expect(useConfirm().request.value).not.toBeNull())
    expect(useConfirm().request.value?.danger).toBe(true)

    useConfirm().settle(true)
    await vi.waitFor(() => expect(api.revokeInvitation).toHaveBeenCalledWith(1))
    wrapper.unmount()
  })

  it('数字输入不被 <label> 包住，标题用 for 指向它', async () => {
    /*
      **这条是浏览器里复现出来的 bug 的守门人。**

      `<label>` 的悬浮会传播给它的**被标注控件**，而那是内部第一个可标注元素 —— 在
      NumberInput 里就是 `−` 按钮。两个后果：鼠标停在 `+` 上时 `−` 也高亮；点标题
      文字会把数字减一。

      jsdom 不会模拟 label 的点击转发，所以这里断言的是**结构**：`.stepper` 不在任何
      label 里，且每个标题的 `for` 都能指到对应输入框的 `id`。
    */
    const wrapper = await mountAdmin()

    expect(wrapper.find('label .stepper').exists()).toBe(false)
    expect(wrapper.find('label .stepper__btn').exists()).toBe(false)

    for (const field of wrapper.findAll('.stepper')) {
      const id = field.find('input').attributes('id')
      expect(id, '输入框没有 id，label 就指不到它').toBeTruthy()

      const caption = wrapper.find(`label[for="${id}"]`)
      expect(caption.exists(), `没有 for="${id}" 的标题`).toBe(true)
      // 标题要真的有字：空标题等于没有名字
      expect(caption.text().trim().length).toBeGreaterThan(0)
    }
    wrapper.unmount()
  })

  it('标题点击能聚焦到输入框（for 生效）', async () => {
    /* 用 `<div>` 换掉 `<label>` 之后，唯一失去的是"点标题聚焦" —— 显式 for 把它补回来 */
    const wrapper = await mountAdmin()

    const caption = wrapper.find('label[for="invite-days"]')
    expect(caption.exists()).toBe(true)
    expect(wrapper.find('#invite-days').exists()).toBe(true)
    wrapper.unmount()
  })

  it('按来源与状态筛选', async () => {
    /*
      列表里三种东西混在一起：平台的、用户申请的、以及各种失效状态。管理员要找的
      往往是"我建的那些还有多少能用"，因此两个维度都要能筛。
    */
    const wrapper = await mountAdmin([
      code({ id: 1, token: 'PLATFORM01', name: '平台可用', is_platform: true }),
      code({ id: 2, token: 'USERCODE01', name: '用户可用', is_platform: false }),
      code({
        id: 3,
        token: 'EXPIRED001',
        name: '平台过期',
        is_platform: true,
        expires_at: new Date(Date.now() - 86400_000).toISOString(),
      }),
      code({
        id: 4,
        token: 'REVOKED001',
        name: '用户失效',
        is_platform: false,
        revoked_at: new Date().toISOString(),
      }),
    ])

    const rows = () => wrapper.findAll('tbody tr').map((tr) => tr.find('td').text())
    expect(rows()).toHaveLength(4)

    // 来源
    const selects = wrapper.findAllComponents({ name: 'Select' })
    await selects[0]!.vm.$emit('update:modelValue', 'platform')
    await wrapper.vm.$nextTick()
    expect(rows()).toEqual(['PLATFORM01', 'EXPIRED001'])

    // 再叠状态：平台 + 可用
    await selects[1]!.vm.$emit('update:modelValue', 'usable')
    await wrapper.vm.$nextTick()
    expect(rows()).toEqual(['PLATFORM01'])

    // 状态换成失效
    await selects[1]!.vm.$emit('update:modelValue', 'revoked')
    await wrapper.vm.$nextTick()
    expect(rows()).toEqual([])
    expect(wrapper.text()).toContain('没有符合筛选条件')

    // 清掉来源，只看失效
    await selects[0]!.vm.$emit('update:modelValue', '')
    await wrapper.vm.$nextTick()
    expect(rows()).toEqual(['REVOKED001'])
    wrapper.unmount()
  })

  it('筛选生效时把筛出的条数也说出来', async () => {
    // 只说"共 N 张"会让人以为列表漏了数据
    const wrapper = await mountAdmin([
      code({ id: 1, is_platform: true }),
      code({ id: 2, is_platform: false }),
    ])

    expect(wrapper.text()).toContain('共 2 张')

    const selects = wrapper.findAllComponents({ name: 'Select' })
    await selects[0]!.vm.$emit('update:modelValue', 'user')
    await wrapper.vm.$nextTick()

    expect(wrapper.text()).toContain('当前筛选出 1 张')
    wrapper.unmount()
  })

  it('两个开关都渲染，并说明它们各自的作用', async () => {
    const wrapper = await mountAdmin()

    const text = wrapper.text()
    expect(text).toContain('暂停邀请')
    expect(text).toContain('暂停申请')
    // 两者的区别必须写出来：一个是应急刹车，一个只管新码
    expect(text).toContain('含此前已发出的')
    expect(text).toContain('已发出的照常可用')
    /*
      生效时机要写，但**写给使用者看**：管理员关心的是"现在改会不会影响正在注册的
      人"，而不是"要不要重启进程"—— 后者是实现细节，不该出现在界面文案里。
    */
    expect(text).toContain('改完立即生效')
    expect(text).not.toContain('重启')
    wrapper.unmount()
  })

  it('打开暂停邀请前先确认 —— 它影响所有人', async () => {
    api.writeSwitch.mockResolvedValue({
      invitations_paused: true,
      invitation_issuance_paused: false,
    })
    const wrapper = await mountAdmin()

    /*
      开关是**受控**的：它用 `@click.prevent` 挡掉浏览器的默认切换，只发一个
      `request` 事件 —— 因为这里要先弹确认框，而取消时开关不能已经翻过去。
      所以测试触发的是 `click`，不是 `change`。
    */
    const boxes = wrapper.findAll('input[role="switch"]')
    await boxes[0]!.trigger('click')
    await vi.waitFor(() => expect(useConfirm().request.value).not.toBeNull())
    expect(useConfirm().request.value?.danger).toBe(true)

    useConfirm().settle(true)
    await vi.waitFor(() =>
      expect(api.writeSwitch).toHaveBeenCalledWith('invitations_paused', true),
    )
    wrapper.unmount()
  })

  it('取消确认时不改开关', async () => {
    /* 这条是"受控"的存在理由：DOM 不能自己翻过去，否则界面会与服务端不一致。 */
    const wrapper = await mountAdmin()

    const boxes = wrapper.findAll('input[role="switch"]')
    await boxes[0]!.trigger('click')
    await vi.waitFor(() => expect(useConfirm().request.value).not.toBeNull())
    useConfirm().settle(false)
    await wrapper.vm.$nextTick()

    expect(api.writeSwitch).not.toHaveBeenCalled()
    expect((boxes[0]!.element as HTMLInputElement).checked).toBe(false)
    wrapper.unmount()
  })

  it('暂停申请不必确认 —— 它只挡新码', async () => {
    api.writeSwitch.mockResolvedValue({
      invitations_paused: false,
      invitation_issuance_paused: true,
    })
    const wrapper = await mountAdmin()

    const boxes = wrapper.findAll('input[role="switch"]')
    await boxes[1]!.trigger('click')

    await vi.waitFor(() =>
      expect(api.writeSwitch).toHaveBeenCalledWith('invitation_issuance_paused', true),
    )
    expect(useConfirm().request.value).toBeNull()
    wrapper.unmount()
  })
})

describe('个人中心：邀请码卡片', () => {
  it('普通用户能申请，且看到申请规则', async () => {
    api.issueInvitation.mockResolvedValue(code())
    const wrapper = await mountWith(InvitationCard, 'user')

    const text = wrapper.text()
    expect(text).toContain('7 天')
    expect(text).toContain('同时只能有一张未使用的')
    expect(text).toContain('每 24 小时只能申请一张')

    await wrapper.find('form input').setValue('给张三')
    await wrapper.find('form').trigger('submit')
    await vi.waitFor(() => expect(api.issueInvitation).toHaveBeenCalledWith('给张三'))
    wrapper.unmount()
  })

  it('管理员不申请自己的码，只给去管理台的入口', async () => {
    /*
      那份额度属于普通用户；管理员建码是运营动作，在管理台统一做。给他一个申请框
      会让他以为功能装错了地方。
    */
    const wrapper = await mountWith(InvitationCard, 'admin')

    expect(wrapper.text()).toContain('管理员不申请个人邀请码')
    expect(wrapper.find('form').exists()).toBe(false)
    const link = wrapper.findAll('a').find((a) => a.text().includes('去管理台'))
    expect(link).toBeTruthy()
    expect(link!.attributes('href')).toBe('/admin/invitations')
    wrapper.unmount()
  })

  it('已有可用码时不再显示申请表单', async () => {
    api.listMyInvitations.mockResolvedValue([code()])
    const wrapper = await mountWith(InvitationCard, 'user')

    expect(wrapper.find('form').exists()).toBe(false)
    expect(wrapper.text()).toContain('你已经有一张可用的邀请码')
    wrapper.unmount()
  })

  it('只对未使用的码提供删除', async () => {
    api.listMyInvitations.mockResolvedValue([
      code({ id: 1, token: 'UNUSED0001' }),
      code({ id: 2, token: 'USED000001', used_count: 1 }),
    ])
    const wrapper = await mountWith(InvitationCard, 'user')

    const deleteButtons = wrapper.findAll('button').filter((b) => b.text() === '删除')
    expect(deleteButtons).toHaveLength(1)
    wrapper.unmount()
  })

  it('显示使用信息：谁、在何时', async () => {
    api.listMyInvitations.mockResolvedValue([
      code({
        used_count: 1,
        usages: [{ username: 'invitee', used_at: '2026-03-01T12:00:00Z' }],
      }),
    ])
    const wrapper = await mountWith(InvitationCard, 'user')

    expect(wrapper.text()).toContain('invitee')
    expect(wrapper.text()).toContain('使用')
    wrapper.unmount()
  })

  it('使用者账号已删除时如实说明，不假装有人用过', async () => {
    api.listMyInvitations.mockResolvedValue([
      code({
        used_count: 1,
        usages: [{ username: null, used_at: '2026-03-01T12:00:00Z' }],
      }),
    ])
    const wrapper = await mountWith(InvitationCard, 'user')

    expect(wrapper.text()).toContain('账号已删除')
    wrapper.unmount()
  })

  it('父组件传下来的 class 落在根节点上', async () => {
    /*
      **这条是截图抓出来的 bug 的守门人。** 这个组件的模板一度是两个并列的
      `<template v-if>` / `<template v-else>`，各自包一个 `<section>` —— 多根
      （fragment）时 Vue 无法把父组件传的 class 落到根上，`panel` 被**静默丢弃**，
      于是这张卡的内容没有面板底、散落在两列之间。

      组件测试查的是文字，因此当时全绿；只有看图才发现。所以这里断言的是**结构**：
      挂载时传 `class="panel"`，根元素必须带着它。
    */
    const wrapper = await mountWith(InvitationCard, 'user')

    expect(wrapper.element.tagName).toBe('SECTION')
    expect(wrapper.classes()).toContain('panel')
    wrapper.unmount()
  })
})
