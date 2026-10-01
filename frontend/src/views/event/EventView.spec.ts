/**
 * 活动页宿主的加载反馈。
 *
 * 这一组测的是**真实组件**而不是纯函数，因为最糟的失败模式只有挂载起来才看得见：
 * 覆盖层永远不消失，把整个页面挡死。纯函数测试再多也发现不了那个。
 *
 * 接口与内容探测都用手动兑现的 promise，这样"第一阶段"这种瞬时状态才观测得到 ——
 * 用 mockResolvedValue 的话，一次 flush 就把所有阶段冲过去了。
 */
import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

const getPublicEvent = vi.fn()
const contentEntryExists = vi.fn()

vi.mock('@/api/events', () => ({
  getPublicEvent: (...args: unknown[]) => getPublicEvent(...args),
}))
vi.mock('./contentProbe', () => ({
  contentEntryExists: (...args: unknown[]) => contentEntryExists(...args),
}))

import { ApiError } from '@/api/client'
import { PROTOCOL_VERSION } from '@/bridge/protocol'
import { COMPLETE_HOLD_MS } from './loadingProgress'
import EventView from './EventView.vue'

const EVENT = {
  id: 'spring-2026',
  title: '春季招新',
  summary: null,
  status: 'live' as const,
  content_version: 3,
  entry_path: 'index.html',
  submission_requires_login: false,
  submissions_open_at: null,
  submissions_close_at: null,
  quota: { limit: 4096, used: 1, remaining: 4095 },
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

/** 冲一次微任务队列。用 advanceTimersByTimeAsync 而不是 flushPromises —— 后者靠
 *  真实的 setTimeout，在假时钟下会挂住。 */
const flush = () => vi.advanceTimersByTimeAsync(0)

function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: { template: '<div />' } },
      // 缺内容那条诊断里有指向它的 RouterLink；没有这条路由，Vue Router 会解析
      // 失败并让整个面板渲染不出来
      {
        path: '/admin/events/:eventId',
        name: 'admin-event-detail',
        component: { template: '<div />' },
      },
      { path: '/:eventId', name: 'event', component: { template: '<div />' } },
      { path: '/:pathMatch(.*)*', name: 'not-found', component: { template: '<div />' } },
    ],
  })
}

async function mountView() {
  const api = deferred<typeof EVENT>()
  const probe = deferred<boolean>()
  getPublicEvent.mockReturnValue(api.promise)
  contentEntryExists.mockReturnValue(probe.promise)

  const router = makeRouter()
  await router.push('/spring-2026')
  await router.isReady()

  const wrapper = mount(EventView, {
    props: { eventId: 'spring-2026' },
    global: { plugins: [router, createPinia()] },
    attachTo: document.body,
  })
  await wrapper.vm.$nextTick()

  /** 走完整个 load()：先活动信息，再内容探测（后者依赖前者） */
  async function settle(entryExists = true) {
    api.resolve(EVENT)
    await flush()
    probe.resolve(entryExists)
    await flush()
    await wrapper.vm.$nextTick()
  }

  return { wrapper, router, settle }
}

/** 派发一条来自 iframe 的消息。happy-dom 的 init 不收 source，手动定义。 */
function postFromIframe(wrapper: ReturnType<typeof mount>, data: unknown): void {
  const frame = wrapper.find('iframe').element as HTMLIFrameElement
  const event = new MessageEvent('message', { data })
  Object.defineProperty(event, 'source', { value: frame.contentWindow })
  window.dispatchEvent(event)
}

function sendReady(wrapper: ReturnType<typeof mount>): void {
  postFromIframe(wrapper, {
    v: PROTOCOL_VERSION,
    type: 'event:ready',
    payload: { protocolVersion: PROTOCOL_VERSION },
  })
}

/** iframe 渲染出来后才谈得上加载它 */
async function loadIframe(wrapper: ReturnType<typeof mount>) {
  const frame = wrapper.find('iframe')
  expect(frame.exists()).toBe(true)
  await frame.trigger('load')
  await wrapper.vm.$nextTick()
}

/** 从内联 style 里读出进度条宽度 */
function progressWidth(wrapper: ReturnType<typeof mount>): number {
  const style = wrapper.find('.event__progress-fill').attributes('style') ?? ''
  return Number(/([\d.]+)%/.exec(style)?.[1] ?? '0')
}

beforeEach(() => {
  vi.useFakeTimers()
})
afterEach(() => {
  vi.useRealTimers()
  vi.clearAllMocks()
})

describe('加载覆盖层', () => {
  it('挂载即出现，并显示第一阶段', async () => {
    const { wrapper } = await mountView()

    const overlay = wrapper.find('.event__overlay')
    expect(overlay.exists()).toBe(true)
    // 活动信息还没回来，所以停在这里
    expect(overlay.text()).toContain('正在读取活动信息')
    wrapper.unmount()
  })

  it('随阶段推进更新文案', async () => {
    const { wrapper, settle } = await mountView()
    await settle()
    await loadIframe(wrapper)

    expect(wrapper.find('.event__overlay').text()).toContain('正在与活动页建立连接')
    wrapper.unmount()
  })

  it('进度条推进，但等待期间到不了 100%', async () => {
    const { wrapper, settle } = await mountView()
    await settle()
    await loadIframe(wrapper)

    await vi.advanceTimersByTimeAsync(4000)

    const width = progressWidth(wrapper)
    expect(width).toBeGreaterThan(0)
    // 核心：等待期间不能声称加载完了
    expect(width).toBeLessThan(100)
    wrapper.unmount()
  })

  it('活动页就绪后补齐到 100%，停留一下，再移除', async () => {
    const { wrapper, settle } = await mountView()
    await settle()
    await loadIframe(wrapper)

    sendReady(wrapper)
    await wrapper.vm.$nextTick()

    // 先补齐到 100 让人看见
    expect(progressWidth(wrapper)).toBe(100)

    // 停留期间还在
    await vi.advanceTimersByTimeAsync(COMPLETE_HOLD_MS - 10)
    expect(wrapper.find('.event__overlay').exists()).toBe(true)

    // 停留结束后卸载（<Transition> 走完淡出再移除）
    await vi.advanceTimersByTimeAsync(COMPLETE_HOLD_MS + 400)
    expect(wrapper.find('.event__overlay').exists()).toBe(false)
    expect(wrapper.find('.event__progress').exists()).toBe(false)
    wrapper.unmount()
  })

  it('重复就绪不会把已经收起的覆盖层弄回来', async () => {
    // iframe 内部导航会重新握手，覆盖层不该再冒出来盖住页面
    const { wrapper, settle } = await mountView()
    await settle()
    await loadIframe(wrapper)

    sendReady(wrapper)
    await vi.advanceTimersByTimeAsync(COMPLETE_HOLD_MS + 400)
    expect(wrapper.find('.event__overlay').exists()).toBe(false)

    sendReady(wrapper)
    await vi.advanceTimersByTimeAsync(COMPLETE_HOLD_MS + 400)
    expect(wrapper.find('.event__overlay').exists()).toBe(false)
    wrapper.unmount()
  })

  it('缺少 SDK 时收起覆盖层，露出可操作的诊断面板', async () => {
    // 覆盖层盖着诊断面板的话，用户看到问题却没法处理
    const { wrapper, settle } = await mountView()
    await settle()
    await loadIframe(wrapper)

    await vi.advanceTimersByTimeAsync(20_000) // 等就绪超时
    await wrapper.vm.$nextTick()
    await vi.advanceTimersByTimeAsync(COMPLETE_HOLD_MS + 400)

    expect(wrapper.find('.event__overlay').exists()).toBe(false)
    expect(wrapper.text()).toContain('没有接入桥接脚本')
    wrapper.unmount()
  })

  it('内容未投放时收起覆盖层，露出投放入口', async () => {
    const { wrapper, settle } = await mountView()
    await settle(false)
    await vi.advanceTimersByTimeAsync(COMPLETE_HOLD_MS + 400)

    expect(wrapper.find('.event__overlay').exists()).toBe(false)
    expect(wrapper.text()).toContain('活动还没有投放内容')
    // 没有内容就没有 iframe
    expect(wrapper.find('iframe').exists()).toBe(false)
    wrapper.unmount()
  })
})

describe('加载失败', () => {
  async function mountFailed(error: ApiError) {
    getPublicEvent.mockRejectedValue(error)
    contentEntryExists.mockResolvedValue(true)

    const router = makeRouter()
    await router.push('/spring-2026')
    await router.isReady()

    const wrapper = mount(EventView, {
      props: { eventId: 'spring-2026' },
      global: { plugins: [router, createPinia()] },
      attachTo: document.body,
    })
    await flush()
    await wrapper.vm.$nextTick()
    return { wrapper, router }
  }

  it('接口失败时覆盖层显示错误信息并留在原地', async () => {
    // ApiError 的签名是 (code, message, status, fields)
    const { wrapper } = await mountFailed(new ApiError('internal_error', '服务器开小差了', 500))

    const overlay = wrapper.find('.event__overlay')
    expect(overlay.exists()).toBe(true)
    expect(overlay.text()).toContain('服务器开小差了')
    // 错误态用 alert 而不是 status，读屏会立刻打断播报
    expect(overlay.attributes('role')).toBe('alert')

    // 进度条在错误态没有意义，收掉
    expect(wrapper.find('.event__progress').exists()).toBe(false)

    // 不会自己消失
    await vi.advanceTimersByTimeAsync(5000)
    expect(wrapper.find('.event__overlay').exists()).toBe(true)
    wrapper.unmount()
  })

  it('404 交给 404 页面，不在这里显示错误', async () => {
    const { wrapper, router } = await mountFailed(new ApiError('not_found', '活动不存在', 404))

    expect(router.currentRoute.value.name).toBe('not-found')
    wrapper.unmount()
  })
})
