/**
 * 个人中心。
 *
 * 它是登录后的**默认落脚点**，也是非管理员唯一的登出入口 —— 在那之前，登出按钮只
 * 存在于管理台外壳里，普通成员登录之后没有出口。所以这一页要守住两件事：身份显示
 * 得对，以及登出确实能走通。
 */
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

const logout = vi.hoisted(() => vi.fn())
vi.mock('@/api/auth', () => ({
  logout: (...args: unknown[]) => logout(...args),
}))

/*
  这一页现在还会渲染邀请码卡片，而那张卡片自己去取数据。不 mock 的话，未处理的
  请求会让**这一页自己的**用例一起变红 —— 失败信息与"身份显示对不对"毫无关系。
  卡片自己的行为由 `InvitationCard.spec.ts` 覆盖。
*/
vi.mock('@/api/invitations', () => ({
  listMyInvitations: vi.fn().mockResolvedValue([]),
  issueInvitation: vi.fn(),
  deleteInvitation: vi.fn(),
  listAllInvitations: vi.fn().mockResolvedValue([]),
  createInvitation: vi.fn(),
  revokeInvitation: vi.fn(),
  readSwitches: vi
    .fn()
    .mockResolvedValue({ invitations_paused: false, invitation_issuance_paused: false }),
  writeSwitch: vi.fn(),
}))

import ProfileView from './ProfileView.vue'
import { useAuthStore } from '@/stores/auth'
import { useToast } from '@/composables/useToast'
import type { User } from '@/types/api'

function user(overrides: Partial<User> = {}): User {
  return {
    id: 7,
    username: 'alice',
    display_name: '张爱丽',
    role: 'user',
    email: 'alice@example.com',
    email_verified: true,
    ...overrides,
  }
}

async function mountView(account: User | null = user()) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/profile', name: 'profile', component: { template: '<div />' } },
      { path: '/login', name: 'login', component: { template: '<div />' } },
      { path: '/', name: 'home', component: { template: '<div />' } },
      /*
        邀请码卡片对管理员有一个指向管理台的入口，因此这个桩必须包含那条命名路由。
        **本会话第五次踩这个坑了**：页面里每加一个指向新命名路由的链接，所有相关
        桩路由都要跟着补 —— 否则 `RouterLink` 解析失败，红的是一整片与改动无关的
        用例（这里是 6 条）。
      */
      {
        path: '/admin/invitations',
        name: 'admin-invitations',
        component: { template: '<div />' },
      },
    ],
  })
  await router.push('/profile')
  await router.isReady()

  const wrapper = mount(ProfileView, {
    global: { plugins: [router, createPinia()] },
    attachTo: document.body,
  })

  /*
    **必须在挂载之后设**：`mount` 里那个 `createPinia()` 才是组件用的实例，挂载前
    拿到的 store 属于另一个 pinia，设了也传不进去。
  */
  if (account) useAuthStore().setUser(account)
  await wrapper.vm.$nextTick()
  return { wrapper, router }
}

beforeEach(() => {
  vi.clearAllMocks()
  setActivePinia(createPinia())
  useToast().clear()
})

describe('身份信息', () => {
  it('显示显示名、用户名、身份与邮箱', async () => {
    const { wrapper } = await mountView()

    const text = wrapper.text()
    expect(text).toContain('张爱丽')
    expect(text).toContain('alice')
    expect(text).toContain('普通成员')
    expect(text).toContain('alice@example.com')
    wrapper.unmount()
  })

  it('管理员显示为「管理员」而不是 admin', async () => {
    // 这一页是给人看的，角色得是中文，否则与"个人中心"其余部分的语气不一致
    const { wrapper } = await mountView(user({ role: 'admin' }))

    expect(wrapper.text()).toContain('管理员')
    expect(wrapper.text()).not.toContain('admin')
    wrapper.unmount()
  })

  it('邮箱未绑定时显示「未绑定」，且不显示未验证标记', async () => {
    const { wrapper } = await mountView(user({ email: null }))

    expect(wrapper.text()).toContain('未绑定')
    // 没有邮箱就谈不上"未验证" —— 两个标记同时出现会让人以为邮箱填错了
    expect(wrapper.text()).not.toContain('未验证')
    wrapper.unmount()
  })

  it('邮箱未验证时给出标记', async () => {
    const { wrapper } = await mountView(user({ email_verified: false }))

    expect(wrapper.text()).toContain('未验证')
    wrapper.unmount()
  })

  it('未登录时不渲染身份信息', async () => {
    const { wrapper } = await mountView(null)

    expect(wrapper.text()).not.toContain('alice')
    expect(wrapper.find('button').exists()).toBe(false)
    wrapper.unmount()
  })

  it('文案是写给使用者的，不掺实施计划', async () => {
    /*
      **这一页的文案一度出现过"不在本期范围内（见变更的 proposal）"。** 使用者不知道
      "本期"是什么，更不会去看 proposal —— 界面文案只该说"现在有什么、你能做什么"。
      这条断言把那类词挡在门外。
    */
    const { wrapper } = await mountView()

    const text = wrapper.text()
    for (const leak of ['本期', 'proposal', '变更的', '规格', '决策', '重启', '服务端']) {
      expect(text, `界面文案里出现了「${leak}」`).not.toContain(leak)
    }
    // 而且该说的要说到：没有的功能如实说，并给出可走的路
    expect(text).toContain('目前还没有')
    expect(text).toContain('忘记密码')
    wrapper.unmount()
  })
})

describe('退出登录', () => {
  it('提供登出按钮，且任何已登录用户都能看到', async () => {
    // 普通成员同样要有 —— 这正是这一页存在的一半理由
    for (const role of ['user', 'admin'] as const) {
      const { wrapper } = await mountView(user({ role }))
      const button = wrapper.findAll('button').find((b) => b.text().includes('退出登录'))
      expect(button, `${role} 看不到退出登录按钮`).toBeTruthy()
      wrapper.unmount()
    }
  })

  it('点击后请求登出并回到登录页', async () => {
    logout.mockResolvedValue(undefined)
    const { wrapper, router } = await mountView()

    const button = wrapper.findAll('button').find((b) => b.text().includes('退出登录'))
    await button!.trigger('click')

    await vi.waitFor(() => expect(logout).toHaveBeenCalledTimes(1))
    await vi.waitFor(() => expect(router.currentRoute.value.name).toBe('login'))
    wrapper.unmount()
  })

  it('请求失败时也回到未登录态，不把人留在已失效的身份上', async () => {
    /*
      store 把登出当成**尽力而为**：服务端可能已经删了会话，也可能网络不通，无论哪种
      情况本地状态都必须清掉。所以这里断言的不是"提示了错误"，而是"仍然退出了" ——
      视图里因此没有 try/catch，那会是一段永远不会执行的代码。

      代价记在这里：请求失败时用户看不到任何提示，而服务端会话与浏览器里的凭据可能
      仍然存在（刷新页面会重新登录）。要消除它得让 store 把失败上报出来，那是另一件事。
    */
    logout.mockRejectedValue(new Error('boom'))
    const { wrapper, router } = await mountView()

    const button = wrapper.findAll('button').find((b) => b.text().includes('退出登录'))
    await button!.trigger('click')

    await vi.waitFor(() => expect(router.currentRoute.value.name).toBe('login'))
    expect(useAuthStore().isLoggedIn).toBe(false)
    wrapper.unmount()
  })
})
