/**
 * 登录页：登录成功之后去哪。
 *
 * 这里守的是一个**看起来微不足道、其实决定第一印象**的默认值：原先是 `/admin`，
 * 而管理台只对管理员开放 —— 普通成员登录成功后立刻被守卫弹回来，看起来像登录失败。
 */
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

const login = vi.hoisted(() => vi.fn())
vi.mock('@/api/auth', () => ({
  login: (...args: unknown[]) => login(...args),
  logout: vi.fn(),
  me: vi.fn().mockRejectedValue(new Error('401')),
}))

import LoginView from './LoginView.vue'
import { useToast } from '@/composables/useToast'
import type { User } from '@/types/api'

function user(role: 'user' | 'admin' = 'user'): User {
  return {
    id: 7,
    username: 'alice',
    display_name: '张爱丽',
    role,
    email: 'alice@example.com',
    email_verified: true,
  }
}

async function mountView(query = '') {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/login', name: 'login', component: { template: '<div />' } },
      { path: '/profile', name: 'profile', component: { template: '<div />' } },
      { path: '/admin/events', name: 'admin-events', component: { template: '<div />' } },
      // 与真实路由表同形：活动是带参数的动态段
      { path: '/:eventId', name: 'event', component: { template: '<div />' } },
    ],
  })
  await router.push(`/login${query}`)
  await router.isReady()

  const wrapper = mount(LoginView, {
    global: { plugins: [router, createPinia()] },
    attachTo: document.body,
  })

  await wrapper.find('input[name="username"]').setValue('alice')
  await wrapper.find('input[name="password"]').setValue('correct-horse')
  return { wrapper, router }
}

async function submit(wrapper: Awaited<ReturnType<typeof mountView>>['wrapper']) {
  await wrapper.find('form').trigger('submit')
}

beforeEach(() => {
  vi.clearAllMocks()
  setActivePinia(createPinia())
  useToast().clear()
  login.mockResolvedValue(user())
})

describe('登录成功后的去向', () => {
  it('没有指定目标时进个人中心，而不是管理台', async () => {
    const { wrapper, router } = await mountView()
    await submit(wrapper)

    await vi.waitFor(() => expect(router.currentRoute.value.name).toBe('profile'))
    wrapper.unmount()
  })

  it('普通用户登录后不会被送进管理台', async () => {
    // 这是原先那个默认值造成的实际后果
    login.mockResolvedValue(user('user'))
    const { wrapper, router } = await mountView()
    await submit(wrapper)

    await vi.waitFor(() => expect(router.currentRoute.value.name).toBe('profile'))
    expect(String(router.currentRoute.value.name)).not.toContain('admin')
    wrapper.unmount()
  })

  it('带 ?redirect= 时回到原目标，而不是个人中心', async () => {
    // 未登录点进管理台 → 登录 → 回到管理台。这条既有行为不能被这次改动破坏
    const { wrapper, router } = await mountView('?redirect=/admin/events')
    await submit(wrapper)

    await vi.waitFor(() =>
      expect(router.currentRoute.value.name).toBe('admin-events'),
    )
    wrapper.unmount()
  })

  it('原目标带参数与 query 时也照原样回去', async () => {
    /*
      守卫带的是 `to.fullPath`，所以原目标可能带着 query。这里按**字符串**推它（而不是
      当成命名路由），这条用例就是那件事的守门人：拆成 name + params 就会把 query 丢掉。
    */
    const { wrapper, router } = await mountView('?redirect=/2026spring?tab=files')
    await submit(wrapper)

    await vi.waitFor(() => expect(router.currentRoute.value.name).toBe('event'))
    expect(router.currentRoute.value.params.eventId).toBe('2026spring')
    expect(router.currentRoute.value.query.tab).toBe('files')
    wrapper.unmount()
  })
})
