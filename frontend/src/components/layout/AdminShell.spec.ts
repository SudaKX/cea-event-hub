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

  it('非管理员看不到用户入口', async () => {
    const { wrapper } = await mountShell('/admin/events', 'user')

    const labels = wrapper.findAll('.shell__link-label').map((n) => n.text())
    expect(labels).toEqual(['活动', '提交'])
    wrapper.unmount()
  })
})
