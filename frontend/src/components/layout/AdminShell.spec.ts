/**
 * 管理台外壳的导航选中态。
 *
 * 这里出过一个 bug：进入 `events/*`（活动详情）时，"活动"这一项的选中态消失。
 *
 * 原因是 `router-link-active` 的判定基于**路由记录**而不是路径前缀：活动详情
 * `events/:eventId` 在路由表里是 `events` 的**兄弟**而不是子路由，于是当前路由的
 * matched 里没有 `events` 那条记录，vue-router 认为这个链接不活跃。
 *
 * 所以选中态改成按**解析出来的路径**判断（见 AdminShell 的 isActive）。
 */
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'

import AdminShell from './AdminShell.vue'
import { useAuthStore } from '@/stores/auth'

/**
 * 与真实路由表同构：活动详情是 `events` 的**兄弟**，这正是 bug 的来源。
 *
 * `/admin` 那条用占位组件而不是 AdminShell 本身 —— 否则外壳会把自己渲染两遍，
 * 每个导航项出现两次，断言里全是重复项。
 */
function makeRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      /*
        `home` 必须在这个桩里：外壳左下角有一个"返回主页"，指向命名路由 `home`。
        桩缺了它，`RouterLink` 解析不出 href，7 条用例会一起变红 —— 而失败信息与
        被改的行为毫无关系（改一个按钮，红的却是"选中态"）。
      */
      { path: '/', name: 'home', component: { template: '<div />' } },
      {
        path: '/admin',
        component: { template: '<div />' },
        children: [
          { path: '', redirect: '/admin/events' },
          { path: 'events', name: 'admin-events', component: { template: '<div />' } },
          {
            path: 'events/:eventId',
            name: 'admin-event-detail',
            component: { template: '<div />' },
            props: true,
          },
          {
            path: 'submissions',
            name: 'admin-submissions',
            component: { template: '<div />' },
          },
          /*
            `admin-invitations` 必须在这个桩里，理由与上面 `home` 那条一样：导航项
            指向命名路由，桩缺了它，`RouterLink` 解析不出 href，7 条用例会一起变红，
            而失败信息（"选中态不对"）与被改的东西毫无关系。
          */
          {
            path: 'invitations',
            name: 'admin-invitations',
            component: { template: '<div />' },
          },
          { path: 'users', name: 'admin-users', component: { template: '<div />' } },
        ],
      },
    ],
  })
}

async function mountShell(path: string, role: 'admin' | 'user' = 'admin') {
  const pinia = createPinia()
  setActivePinia(pinia)
  /*
    必须走 `setUser` 而不是赋值：store 把 `user` 暴露成 `readonly`，直接赋值会被
    静默忽略（而 `isAdmin` 一直为 false，表现为"用户"入口莫名其妙不见了）。
    只读是刻意的 —— 绕过 `setUser` 就不会触发 onChange，桥接层收不到登录态变化。
  */
  useAuthStore().setUser({ id: 1, role, display_name: '测试' } as never)

  const router = makeRouter()
  await router.push(path)
  await router.isReady()

  const wrapper = mount(AdminShell, {
    global: { plugins: [router, pinia] },
    attachTo: document.body,
  })
  await wrapper.vm.$nextTick()
  return { wrapper, router }
}

/** 当前处于选中态的导航项文字 */
function activeLabels(wrapper: ReturnType<typeof mount>): string[] {
  return wrapper
    .findAll('.shell__link')
    .filter((link) => link.classes().some((c) => c.includes('active')))
    .map((link) => link.find('.shell__link-label').text())
}

beforeEach(() => {
  localStorage.clear()
  setActivePinia(createPinia())
})

describe('导航选中态', () => {
  it('活动列表页选中「活动」', async () => {
    const { wrapper } = await mountShell('/admin/events')
    expect(activeLabels(wrapper)).toEqual(['活动'])
    wrapper.unmount()
  })

  it('活动详情页同样选中「活动」', async () => {
    // 这条就是那个 bug：详情在路由表里是活动的兄弟，vue-router 的活跃判定不认它
    const { wrapper } = await mountShell('/admin/events/spring-2026')
    expect(activeLabels(wrapper)).toEqual(['活动'])
    wrapper.unmount()
  })

  it('提交页选中「提交」', async () => {
    const { wrapper } = await mountShell('/admin/submissions')
    expect(activeLabels(wrapper)).toEqual(['提交'])
    wrapper.unmount()
  })

  it('用户页选中「用户」', async () => {
    const { wrapper } = await mountShell('/admin/users')
    expect(activeLabels(wrapper)).toEqual(['用户'])
    wrapper.unmount()
  })

  it('任何一页都只有一项是选中的', async () => {
    for (const path of [
      '/admin/events',
      '/admin/events/spring-2026',
      '/admin/submissions',
      '/admin/users',
    ]) {
      const { wrapper } = await mountShell(path)
      expect(activeLabels(wrapper)).toHaveLength(1)
      wrapper.unmount()
    }
  })

  it('切换路由时选中态跟着走', async () => {
    // 选中态是按当前路由算出来的，不是挂载时算一次
    const { wrapper, router } = await mountShell('/admin/events')
    expect(activeLabels(wrapper)).toEqual(['活动'])

    await router.push('/admin/submissions')
    await wrapper.vm.$nextTick()
    expect(activeLabels(wrapper)).toEqual(['提交'])

    await router.push('/admin/events/spring-2026')
    await wrapper.vm.$nextTick()
    expect(activeLabels(wrapper)).toEqual(['活动'])
    wrapper.unmount()
  })

  it('左下角有一个回主页的出口', async () => {
    /*
      管理台是**另一套版式**（固定视口、没有站点页头），从书签直接进来时浏览器"后退"
      未必能一步回到首页 —— 所以这里要有一个明确的出口，与首页那个"管理台"入口互成
      镜像。

      位置也要断言：它在 `.shell__foot` 里（侧栏底部那组），而不是混在导航项中间，
      否则读起来像第四个管理页面。
    */
    const { wrapper } = await mountShell('/admin/events')

    const foot = wrapper.find('.shell__foot')
    expect(foot.exists()).toBe(true)

    const home = foot.findAll('a').find((a) => a.text().includes('返回主页'))
    expect(home, '侧栏底部没有返回主页的链接').toBeTruthy()
    expect(home!.attributes('href')).toBe('/')
    wrapper.unmount()
  })

  it('邀请入口与用户入口同档：仅管理员可见', async () => {
    /*
      两者管的都是账号与准入门槛，越权访问的影响面比活动与提交更大 —— 因此不是
      "登录就能看到"，而是与用户管理一样只对管理员显示。
    */
    const asAdmin = await mountShell('/admin/events', 'admin')
    expect(asAdmin.wrapper.text()).toContain('邀请')
    asAdmin.wrapper.unmount()

    const asUser = await mountShell('/admin/events', 'user')
    expect(asUser.wrapper.text()).not.toContain('邀请')
    // 活动与提交对普通用户仍然可见 —— 隐藏的是"管理账号与准入"那一档
    expect(asUser.wrapper.text()).toContain('活动')
    asUser.wrapper.unmount()
  })

  it('非管理员看不到用户入口', async () => {
    const { wrapper } = await mountShell('/admin/events', 'user')

    const labels = wrapper.findAll('.shell__link-label').map((n) => n.text())
    expect(labels).toEqual(['活动', '提交'])
    wrapper.unmount()
  })
})
