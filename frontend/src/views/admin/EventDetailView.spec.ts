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
  await vi.waitFor(() => expect(wrapper.find('.card__title').exists()).toBe(true))
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

    const notes = wrapper.findAll('.card__note').map((node) => node.text()).join(' ')
    expect(notes).toContain('26 / 200')
    wrapper.unmount()
  })

  it('各个卡片都还在', async () => {
    const { wrapper } = await mountView()

    const titles = wrapper.findAll('.card__title').map((node) => node.text())
    expect(titles).toEqual(['设置', '网页内容', '提交', '删除活动'])
    wrapper.unmount()
  })

  it('设置卡带加宽类，其余卡片不带', async () => {
    /*
      只断言 CSS 里存在 `.card--wide` 是不够的：把类名从模板里删掉，样式测试照样
      通过，而布局已经塌回等宽了。**规则存在 ≠ 规则被用上** —— 这条与上面那条
      合起来才闭环。
    */
    const { wrapper } = await mountView()

    const cards = wrapper.findAll('.card')
    expect(cards).toHaveLength(4)
    expect(cards[0]!.classes()).toContain('card--wide')
    expect(cards[0]!.text()).toContain('设置')
    // 其余三张都走常规宽度
    for (const card of cards.slice(1)) {
      expect(card.classes()).not.toContain('card--wide')
    }
    wrapper.unmount()
  })

  it('设置卡自己一条流，其余三张共用拉齐高度的那条', async () => {
    /*
      同样地：`.cards--even` 存在不等于被用上。而设置卡**不该**在那条流里 ——
      它内容多，被拉齐只会连累旁边几张空一大截。
    */
    const { wrapper } = await mountView()

    const flows = wrapper.findAll('.cards')
    expect(flows).toHaveLength(2)

    // 第一条只有设置卡，且不拉齐
    expect(flows[0]!.findAll('.card')).toHaveLength(1)
    expect(flows[0]!.classes()).not.toContain('cards--even')

    // 第二条是另外三张，且拉齐
    expect(flows[1]!.classes()).toContain('cards--even')
    expect(flows[1]!.findAll('.card')).toHaveLength(3)
    wrapper.unmount()
  })

  it('设置类字段住在同一个表单里，一次保存', async () => {
    /*
      拆成多张卡片是**视觉分组**，不是拆成多个表单：分成多个表单之后，"我只改了
      标题，怎么提交配额也被发出去了"这种不确定性会回来。
    */
    const { wrapper } = await mountView()

    const form = wrapper.find('form.cards')
    expect(form.exists()).toBe(true)
    for (const label of ['标题', '简介', '状态', '可见性', '条数上限', '每人最多']) {
      expect(form.text()).toContain(label)
    }
    // 保存按钮也在这个表单里，且是唯一的提交按钮
    expect(form.findAll('button[type="submit"]')).toHaveLength(1)
    wrapper.unmount()
  })

  it('删除活动在卡片里，不在页头', async () => {
    /*
      页头那个位置离标题很远、又没有任何说明，是个容易被误点的位置。挪进卡片之后，
      "点错了会发生什么"就写在按钮上方。
    */
    const { wrapper } = await mountView()

    expect(wrapper.find('.head').text()).not.toContain('删除活动')

    const danger = wrapper.find('.card--danger')
    expect(danger.exists()).toBe(true)
    expect(danger.text()).toContain('不可撤销')
    expect(danger.findAll('button').map((b) => b.text())).toContain('删除活动')
    wrapper.unmount()
  })

  it('即时生效的操作不在设置表单里', async () => {
    // 内容投放与"查看提交"各自即时生效，混进设置表单会让人以为要一起提交
    const { wrapper } = await mountView()

    const form = wrapper.find('form.cards')
    expect(form.text()).not.toContain('网页内容')
    expect(form.text()).not.toContain('查看该活动的提交')
    wrapper.unmount()
  })
})
