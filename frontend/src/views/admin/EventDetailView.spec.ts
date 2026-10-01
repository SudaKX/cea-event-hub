/**
 * 活动详情页：只编辑策略与投放内容。
 *
 * 提交列表**不在这里** —— 它只有一份，在「提交」页。同一份列表放两处，筛选、分页、
 * 权限各自一套，迟早漂移出不一致。所以这一组同时验证"没有表格"和"有入口"。
 */
import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

const getAdminEvent = vi.fn()
const listContent = vi.fn()

vi.mock('@/api/events', () => ({
  getAdminEvent: (...args: unknown[]) => getAdminEvent(...args),
  listContent: (...args: unknown[]) => listContent(...args),
  updateEvent: vi.fn(),
  deployContent: vi.fn(),
  deleteEvent: vi.fn(),
}))
// 这一页不该再碰提交接口。真去调了就会落到这个会抛错的桩上，测试立刻失败
vi.mock('@/api/submissions', () => ({
  listEventSubmissions: () => {
    throw new Error('活动详情页不该请求提交列表')
  },
}))

import EventDetailView from './EventDetailView.vue'

const EVENT = {
  id: 'spring-2026',
  title: '春季招新',
  summary: null,
  status: 'live',
  entry_path: 'index.html',
  content_version: 3,
  submission_requires_login: false,
  submissions_open_at: null,
  submissions_close_at: null,
  max_submissions: 200,
  quota: { limit: 200, used: 26, remaining: 174 },
  owner_id: null,
  created_at: '2026-10-01T00:00:00Z',
  updated_at: '2026-10-01T00:00:00Z',
}

async function mountView() {
  getAdminEvent.mockResolvedValue(EVENT)
  listContent.mockResolvedValue({ files: [] })

  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: { template: '<div />' } },
      { path: '/admin/events', name: 'admin-events', component: { template: '<div />' } },
      { path: '/admin/submissions', name: 'admin-submissions', component: { template: '<div />' } },
      { path: '/:pathMatch(.*)*', component: { template: '<div />' } },
    ],
  })
  await router.push('/admin/events/spring-2026')
  await router.isReady()

  const wrapper = mount(EventDetailView, {
    props: { eventId: 'spring-2026' },
    global: { plugins: [router, createPinia()] },
    attachTo: document.body,
  })
  // 等**内容渲染出来**，而不是等接口被调用 —— 后者会在 load() 还没跑完时就满足，
  // 那时页面还停在 v-if="loading" 的加载态上
  await vi.waitFor(() => expect(wrapper.find('.block__title').exists()).toBe(true))
  return { wrapper, router }
}

beforeEach(() => {
  vi.clearAllMocks()
})
afterEach(() => {
  vi.restoreAllMocks()
})

describe('不再放提交列表', () => {
  it('页面上没有提交表格', async () => {
    const { wrapper } = await mountView()

    expect(wrapper.find('table').exists()).toBe(false)
    wrapper.unmount()
  })

  it('没有分页器，也没有详情对话框', async () => {
    const { wrapper } = await mountView()

    expect(wrapper.find('.pager').exists()).toBe(false)
    expect(wrapper.find('dialog').exists()).toBe(false)
    wrapper.unmount()
  })

  it('不请求提交列表', async () => {
    // 桩会抛错，所以真去调了这里就挂了
    const { wrapper } = await mountView()

    expect(wrapper.find('.alert').exists()).toBe(false)
    wrapper.unmount()
  })
})

describe('留下入口', () => {
  it('给出查看该活动提交的链接', async () => {
    const { wrapper } = await mountView()

    const link = wrapper.findAll('a').find((a) => a.text().includes('查看该活动的提交'))
    expect(link).toBeDefined()
    wrapper.unmount()
  })

  it('链接带上活动标识，跳到提交页时能落到这个活动上', async () => {
    // 不带的话提交页会默认选第一个活动，而用户刚看的往往不是第一个
    const { wrapper } = await mountView()

    const link = wrapper.findAll('a').find((a) => a.text().includes('查看该活动的提交'))!
    expect(link.attributes('href')).toContain('event=spring-2026')
    wrapper.unmount()
  })

  it('顺带显示当前配额，省得为了看数字再跳一次', async () => {
    const { wrapper } = await mountView()

    const lead = wrapper.findAll('.block__lead').map((node) => node.text()).join(' ')
    expect(lead).toContain('26 / 200')
    wrapper.unmount()
  })

  it('编辑策略与投放内容都还在', async () => {
    const { wrapper } = await mountView()

    const titles = wrapper.findAll('.block__title').map((node) => node.text())
    expect(titles).toContain('提交策略')
    expect(titles).toContain('网页内容')
    expect(titles).toContain('提交')
    wrapper.unmount()
  })
})
