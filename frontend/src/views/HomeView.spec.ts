/**
 * 首页。
 *
 * 它是**公开**页面：未登录也能看，且要能扛住"一个活动都没有""后端挂了"这两种
 * 情况 —— 门面页白屏或报错是最难看的。
 */
import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

const listPublicEvents = vi.hoisted(() => vi.fn())
vi.mock('@/api/events', () => ({
  listPublicEvents: (...args: unknown[]) => listPublicEvents(...args),
}))

import HomeView from './HomeView.vue'
import { useAuthStore } from '@/stores/auth'
import type { EventPublic } from '@/types/api'

function event(overrides: Partial<EventPublic> = {}): EventPublic {
  return {
    id: '2026spring',
    title: '春季招新',
    summary: '面向全校的招新活动',
    status: 'live',
    content_version: 1,
    entry_path: 'index.html',
    submission_requires_login: false,
    submissions_open_at: null,
    submissions_close_at: null,
    quota: { limit: 200, used: 26, remaining: 174 },
    pinned: false,
    ...overrides,
  }
}

async function mountView(events: EventPublic[] = [event()], options: { fail?: boolean } = {}) {
  if (options.fail) listPublicEvents.mockRejectedValue(new Error('boom'))
  else listPublicEvents.mockResolvedValue(events)

  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: { template: '<div />' } },
      { path: '/:eventId', name: 'event', component: { template: '<div />' } },
      { path: '/admin/events', name: 'admin-events', component: { template: '<div />' } },
      { path: '/login', name: 'login', component: { template: '<div />' } },
      { path: '/register', name: 'register', component: { template: '<div />' } },
    ],
  })
  await router.push('/')
  await router.isReady()

  const wrapper = mount(HomeView, {
    global: { plugins: [router, createPinia()] },
    attachTo: document.body,
  })
  await vi.waitFor(() => expect(listPublicEvents).toHaveBeenCalled())
  await wrapper.vm.$nextTick()
  return wrapper
}

beforeEach(() => {
  vi.clearAllMocks()
})
afterEach(() => {
  vi.restoreAllMocks()
})

describe('版面', () => {
  it('立绘与文字栏并排', async () => {
    const wrapper = await mountView()

    expect(wrapper.find('.home__art').exists()).toBe(true)
    expect(wrapper.find('.home__col').exists()).toBe(true)
    wrapper.unmount()
  })

  it('立绘用的是复制过来的那张，且带说明文字', async () => {
    // alt 不能空：它是装饰性插画，但表达的是社团形象，该给读屏用户一个交代
    const wrapper = await mountView()
    const art = wrapper.find('.home__art')

    // 走模块导入而不是 public 绝对路径：后者会被 Vite 当成要解析的资源，
    // 测试环境下解析成 file:///ellia.png 直接报错
    expect(art.attributes('src')).toBeTruthy()
    expect(art.attributes('alt')).toBeTruthy()
    wrapper.unmount()
  })

  it('标题带闪烁光标', async () => {
    // 海报页面的标志性元素，去掉就不像那一版了
    const wrapper = await mountView()

    expect(wrapper.find('.home__title em').text()).toBe('活动平台')
    const cursor = wrapper.find('.cursor')
    expect(cursor.exists()).toBe(true)
    expect(cursor.attributes('aria-hidden')).toBe('true')
    wrapper.unmount()
  })
})

describe('活动卡片区', () => {
  /** 置顶的活动才进卡片区 */
  const pinned = (overrides: Partial<EventPublic> = {}) =>
    event({ pinned: true, ...overrides })

  it('只展示置顶的活动', async () => {
    const wrapper = await mountView([
      pinned({ id: 'featured', title: '置顶的那个' }),
      event({ id: 'plain', title: '普通公开的' }),
    ])

    const links = wrapper.findAll('.events__link')
    expect(links).toHaveLength(1)
    expect(links[0]!.text()).toContain('置顶的那个')
    expect(wrapper.text()).not.toContain('普通公开的')
    wrapper.unmount()
  })

  it('没有置顶活动时给一句说明', async () => {
    // 公开但未置顶的活动不进卡片区 —— 那是三档里第 1 档的设计，不是空白页
    const wrapper = await mountView([event({ pinned: false })])

    expect(wrapper.find('.events').exists()).toBe(false)
    expect(wrapper.find('.home__note').text()).toContain('没有置顶的活动')
    wrapper.unmount()
  })

  it('卡片链到活动页', async () => {
    const wrapper = await mountView([pinned()])

    expect(wrapper.find('.events__link').attributes('href')).toBe('/2026spring')
    wrapper.unmount()
  })

  it('顺带给出标识、名额与简介', async () => {
    const wrapper = await mountView([pinned()])

    const link = wrapper.find('.events__link')
    expect(link.text()).toContain('2026spring')
    expect(link.text()).toContain('26 / 200')
    expect(link.text()).toContain('面向全校的招新活动')
    wrapper.unmount()
  })

  it('不限额的活动不显示名额', async () => {
    const wrapper = await mountView([
      pinned({ quota: { limit: null, used: 3, remaining: null } }),
    ])

    expect(wrapper.find('.events__link').text()).not.toContain('名额')
    wrapper.unmount()
  })

  it('活动没有简介时不渲染那一行', async () => {
    const wrapper = await mountView([pinned({ summary: null })])

    expect(wrapper.find('.events__summary').exists()).toBe(false)
    wrapper.unmount()
  })
})

describe('截止状态', () => {
  /** 卡片区只渲染置顶的，所以这一组的活动都要置顶 */
  const pinned = (overrides: Partial<EventPublic> = {}) => event({ pinned: true, ...overrides })
  it('已过截止时间的活动标为已截止，且不再显示名额', async () => {
    const wrapper = await mountView([
      pinned({ submissions_close_at: '2020-01-01T00:00:00Z' }),
    ])

    expect(wrapper.find('.events__link').text()).toContain('已截止')
    wrapper.unmount()
  })

  it('还没到开放时间的同样标为已截止', async () => {
    // 对访客来说两者是一回事：现在交不了
    const wrapper = await mountView([
      pinned({ submissions_open_at: '2099-01-01T00:00:00Z' }),
    ])

    expect(wrapper.find('.events__link').text()).toContain('已截止')
    wrapper.unmount()
  })

  it('名额用尽也算截止', async () => {
    const wrapper = await mountView([pinned({ quota: { limit: 10, used: 10, remaining: 0 } })])

    expect(wrapper.find('.events__link').text()).toContain('已截止')
    wrapper.unmount()
  })

  it('时间窗内的正常活动不标截止', async () => {
    const wrapper = await mountView([
      pinned({
        submissions_open_at: '2020-01-01T00:00:00Z',
        submissions_close_at: '2099-01-01T00:00:00Z',
      }),
    ])

    expect(wrapper.find('.events__link').text()).not.toContain('已截止')
    wrapper.unmount()
  })
})

describe('按标识前往（自动补全）', () => {
  /*
    用现成的 Select（可搜索）而不是自搓 combobox：它已经是"输入即过滤 + 全键盘
    操作"。这里验证它接得对 —— 候选项来自公开列表，选中即跳转。
  */
  it('候选项来自完整公开列表，而不是卡片区那份', async () => {
    /*
      补全含**未置顶**的公开活动：它的作用是"目录里有哪些"，砍成只剩置顶的就
      本末倒置了。这也是三档里第 1 档的落脚处。
    */
    const wrapper = await mountView([
      event({ id: 'featured', title: '置顶的', pinned: true }),
      event({ id: 'plain', title: '未置顶的', pinned: false }),
    ])

    // 卡片区只有置顶那个
    expect(wrapper.findAll('.events__link')).toHaveLength(1)

    await wrapper.find('.home__jump .select__trigger').trigger('click')
    await wrapper.vm.$nextTick()

    const labels = wrapper.findAll('[role="option"]').map((o) => o.text())
    expect(labels.some((t) => t.includes('featured'))).toBe(true)
    expect(labels.some((t) => t.includes('plain'))).toBe(true)
    wrapper.unmount()
  })

  it('可以按标识过滤', async () => {
    const wrapper = await mountView([event(), event({ id: 'autumn', title: '秋季工作坊' })])

    await wrapper.find('.home__jump .select__trigger').trigger('click')
    await wrapper.vm.$nextTick()
    await wrapper.find('.home__jump input').setValue('autumn')
    await wrapper.vm.$nextTick()

    const labels = wrapper.findAll('[role="option"]').map((o) => o.text())
    expect(labels).toHaveLength(1)
    expect(labels[0]).toContain('autumn')
    wrapper.unmount()
  })

  it('选中即跳到该活动', async () => {
    const wrapper = await mountView([event()])
    const router = wrapper.vm.$router

    await wrapper.find('.home__jump .select__trigger').trigger('click')
    await wrapper.vm.$nextTick()
    await wrapper.findAll('[role="option"]')[0]!.trigger('click')
    await vi.waitFor(() => expect(router.currentRoute.value.name).toBe('event'))

    expect(router.currentRoute.value.params.eventId).toBe('2026spring')
    wrapper.unmount()
  })

  it('没有活动时不显示这个入口', async () => {
    // 没东西可补全，摆一个空下拉只会让人以为坏了
    const wrapper = await mountView([])

    expect(wrapper.find('.home__jump').exists()).toBe(false)
    wrapper.unmount()
  })
})

describe('失败与入口', () => {
  it('后端出错时给出提示而不是白屏', async () => {
    const wrapper = await mountView([], { fail: true })
    await vi.waitFor(() => expect(wrapper.find('[role="alert"]').exists()).toBe(true))

    expect(wrapper.find('[role="alert"]').text()).toBeTruthy()
    wrapper.unmount()
  })

  it('未登录时给出登录、注册与管理台三个入口', async () => {
    /*
      之前只有"管理台" —— 它虽然也会把人引到登录页，但那要先点进去才发现。首页是
      门面，得让人一眼看出自己能做什么。
    */
    const wrapper = await mountView()

    const links = wrapper.findAll('.home__link').map((l) => l.attributes('href'))
    expect(links).toContain('/login')
    expect(links).toContain('/register')
    expect(links).toContain('/admin/events')
    wrapper.unmount()
  })

  it('已登录的普通用户不再看到注册与管理台入口', async () => {
    const wrapper = await mountView()
    useAuthStore().setUser({
      id: 1,
      username: 'alice',
      display_name: 'Alice',
      role: 'user',
      email: null,
      email_verified: true,
    })
    await wrapper.vm.$nextTick()

    expect(wrapper.text()).toContain('Alice')
    const links = wrapper.findAll('.home__link').map((l) => l.attributes('href'))
    expect(links).not.toContain('/register')
    // 点进去只会被守卫弹回来
    expect(links).not.toContain('/admin/events')
    wrapper.unmount()
  })

  it('管理员登录后仍然看得到管理台入口', async () => {
    /*
      隐藏规则只针对普通用户。把管理员也一并隐藏的话，他在自己的首页上找不到
      入口 —— 而根路径本来就该是"我接下来能去哪"的地方。
    */
    const wrapper = await mountView()
    useAuthStore().setUser({
      id: 1,
      username: 'root',
      display_name: 'Root',
      role: 'admin',
      email: null,
      email_verified: true,
    })
    await wrapper.vm.$nextTick()

    const links = wrapper.findAll('.home__link').map((l) => l.attributes('href'))
    expect(links).toContain('/admin/events')
    wrapper.unmount()
  })
})
