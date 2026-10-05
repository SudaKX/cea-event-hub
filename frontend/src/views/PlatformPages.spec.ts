/**
 * 平台自己的页面不应该把所有人指向管理台。
 *
 * 管理台只对管理员开放，而这几个页面**谁都会遇到**：
 *
 * - 登录页是所有人的入口，不是"管理入口"；
 * - 邮箱验证的落地页可能在任何浏览器里被打开（邮件客户端点进来），既不能假设已登录、
 *   更不能假设是管理员；
 * - 404 谁都会撞上 —— 把一个多数人进不去的地方当主出口，等于让 404 变成第二道墙。
 *
 * 这一组断言守的是**文案与出口**，看起来琐碎，但它们是这一期改动里最后残留的
 * "管理台是所有人的默认去处"这个假设。
 */
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

vi.mock('@/api/auth', () => ({
  login: vi.fn(),
  logout: vi.fn(),
  me: vi.fn().mockRejectedValue(new Error('401')),
  verifyEmail: vi.fn().mockResolvedValue(undefined),
}))

import LoginView from './auth/LoginView.vue'
import VerifyEmailView from './auth/VerifyEmailView.vue'
import NotFoundView from './NotFoundView.vue'

function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: { template: '<div />' } },
      { path: '/login', name: 'login', component: { template: '<div />' } },
      { path: '/register', name: 'register', component: { template: '<div />' } },
      { path: '/reset', name: 'reset', component: { template: '<div />' } },
      { path: '/verify-email', name: 'verify-email', component: { template: '<div />' } },
      // 只有它需要管理员权限 —— 也正因如此，把它当默认出口是错的
      { path: '/admin', name: 'admin-events', component: { template: '<div />' } },
    ],
  })
}

async function mountAt(component: unknown, path: string) {
  const router = makeRouter()
  await router.push(path)
  await router.isReady()
  return mount(component as never, {
    global: { plugins: [router, createPinia()] },
    attachTo: document.body,
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  setActivePinia(createPinia())
})

describe('登录页', () => {
  it('不自称是管理入口', async () => {
    /*
      它一度写着"社团活动平台管理入口"，而这一页是**所有人**的入口。现在标题下面
      没有任何说明段 —— 表单自己已经说清了这一页要做什么，再写一句只是重复。
    */
    const wrapper = await mountAt(LoginView, '/login')

    expect(wrapper.text()).not.toContain('管理入口')
    expect(wrapper.find('.auth__lead').exists()).toBe(false)
    wrapper.unmount()
  })
})

describe('邮箱验证落地页', () => {
  it('出口是首页，不是管理台', async () => {
    const wrapper = await mountAt(VerifyEmailView, '/verify-email?token=abc')

    const links = wrapper.findAll('a').map((a) => a.attributes('href'))
    expect(links).toContain('/')
    expect(links).not.toContain('/admin')
    wrapper.unmount()
  })
})

describe('未找到页', () => {
  it('主出口是首页，不是管理台', async () => {
    const wrapper = await mountAt(NotFoundView, '/no-such-event')

    const links = wrapper.findAll('a').map((a) => a.attributes('href'))
    expect(links).toContain('/')
    expect(links).not.toContain('/admin')
    // 登录入口保留：撞上 404 的人里有相当一部分正想登录
    expect(links).toContain('/login')
    wrapper.unmount()
  })
})
