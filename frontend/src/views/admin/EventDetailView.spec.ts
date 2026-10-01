/**
 * 活动详情里的提交列表分页。
 *
 * 这里原本**完全没有分页**：写死取前 20 条，标题写着"共 N 条"，第 21 条之后
 * 根本看不到。所以这一组首先断言"页码真的发给了后端"，其次断言几个容易做错的
 * 联动 —— 翻页不该重取活动详情、删除要刷新配额但不该重置表单。
 */
import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

const getAdminEvent = vi.fn()
const listContent = vi.fn()
const listEventSubmissions = vi.fn()
const reviewSubmission = vi.fn()
const deleteSubmission = vi.fn()

vi.mock('@/api/events', () => ({
  getAdminEvent: (...args: unknown[]) => getAdminEvent(...args),
  listContent: (...args: unknown[]) => listContent(...args),
  updateEvent: vi.fn(),
  deployContent: vi.fn(),
  deleteEvent: vi.fn(),
}))
vi.mock('@/api/submissions', () => ({
  listEventSubmissions: (...args: unknown[]) => listEventSubmissions(...args),
  reviewSubmission: (...args: unknown[]) => reviewSubmission(...args),
  deleteSubmission: (...args: unknown[]) => deleteSubmission(...args),
  attachmentUrl: (id: number, fileId: number) => `/api/v1/submissions/${id}/files/${fileId}`,
}))

import EventDetailView from './EventDetailView.vue'
import { SUBMISSION_STATUS } from '@/domain/submission'

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
  max_submissions: null,
  quota: { limit: 4096, used: 137, remaining: 3959 },
}

function submission(id: number) {
  return {
    id,
    submitter: `a:browser-${id}`,
    from_authenticated_user: false,
    kind: 'signup',
    payload: { name: `n${id}` },
    files: [],
    status: SUBMISSION_STATUS.RECEIVED,
    created_at: '2026-10-01T00:00:00Z',
  }
}

/** 造一页数据：总数固定 137，按请求的页码切片 */
function pageOf(page: number, size: number) {
  const start = (page - 1) * size
  const count = Math.max(0, Math.min(size, 137 - start))
  return {
    submissions: Array.from({ length: count }, (_, index) => submission(start + index + 1)),
    total: 137,
  }
}

async function mountView() {
  getAdminEvent.mockResolvedValue(EVENT)
  listContent.mockResolvedValue({ files: [] })
  listEventSubmissions.mockImplementation((_id: string, filters: { page?: number; page_size?: number }) =>
    Promise.resolve(pageOf(filters.page ?? 1, filters.page_size ?? 20)),
  )

  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: { template: '<div />' } },
      { path: '/admin/events', name: 'admin-events', component: { template: '<div />' } },
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
  await vi.waitFor(() => expect(listEventSubmissions).toHaveBeenCalled())
  await wrapper.vm.$nextTick()
  return { wrapper, router }
}

/** 上一次请求提交列表时用的参数 */
function lastFilters() {
  const calls = listEventSubmissions.mock.calls
  return calls[calls.length - 1]?.[1] as { page?: number; page_size?: number }
}

function pagerButton(wrapper: ReturnType<typeof mount>, label: string) {
  const byLabel = wrapper.findAll('button').find((b) => b.attributes('aria-label') === label)
  if (byLabel) return byLabel
  return wrapper.findAll('button').find((b) => b.text() === label)!
}

beforeEach(() => {
  vi.clearAllMocks()
})
afterEach(() => {
  vi.restoreAllMocks()
})

describe('提交列表分页', () => {
  it('首次加载就带上页码与每页条数', async () => {
    const { wrapper } = await mountView()

    expect(lastFilters()).toMatchObject({ page: 1, page_size: 20 })
    wrapper.unmount()
  })

  it('标题里的"共 N 条"交给分页器，只显示一处', async () => {
    // 两个地方各显示一份数字，迟早对不上
    const { wrapper } = await mountView()

    expect(wrapper.find('.pager__range').text()).toContain('共 137 条')
    // 分页器所在那个面板的标题里不该再出现一次总数
    const block = wrapper.find('.pager').element.closest('.block') as HTMLElement
    expect(block.querySelector('.block__title')?.textContent?.trim()).toBe('提交')
    wrapper.unmount()
  })

  it('点下一页会按第 2 页重新请求', async () => {
    const { wrapper } = await mountView()

    await pagerButton(wrapper, '下一页').trigger('click')
    await vi.waitFor(() => expect(lastFilters().page).toBe(2))
    await wrapper.vm.$nextTick()

    // 表里显示的确实是第 21 条起
    expect(wrapper.find('tbody tr td').text()).toBe('21')
    wrapper.unmount()
  })

  it('翻页不会重取活动详情', async () => {
    // 重取详情会连带把表单重置掉，而管理员可能正在编辑它
    const { wrapper } = await mountView()
    const before = getAdminEvent.mock.calls.length

    await pagerButton(wrapper, '下一页').trigger('click')
    await vi.waitFor(() => expect(lastFilters().page).toBe(2))

    expect(getAdminEvent.mock.calls.length).toBe(before)
    wrapper.unmount()
  })

  it('改每页条数会回到第 1 页', async () => {
    const { wrapper } = await mountView()

    await pagerButton(wrapper, '下一页').trigger('click')
    await vi.waitFor(() => expect(lastFilters().page).toBe(2))

    await wrapper.find('.pager__size .select__trigger').trigger('click')
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()
    const option = wrapper.findAll('[role="option"]').find((o) => o.text() === '50 / 页')!
    await option.trigger('click')

    await vi.waitFor(() => expect(lastFilters().page_size).toBe(50))
    expect(lastFilters().page).toBe(1)
    wrapper.unmount()
  })

  it('审核只重取当前页，不动活动详情', async () => {
    const { wrapper } = await mountView()
    reviewSubmission.mockResolvedValue(submission(1))
    const before = getAdminEvent.mock.calls.length

    const accept = wrapper.findAll('button').find((b) => b.text() === '采用')!
    await accept.trigger('click')
    await vi.waitFor(() => expect(reviewSubmission).toHaveBeenCalled())

    expect(getAdminEvent.mock.calls.length).toBe(before)
    expect(lastFilters()).toMatchObject({ page: 1, page_size: 20 })
    wrapper.unmount()
  })

  it('删除会刷新配额，但不会重置表单里正在编辑的内容', async () => {
    const { wrapper } = await mountView()
    // happy-dom 没有实现 window.confirm，直接 stub 而不是 spyOn
    vi.stubGlobal('confirm', vi.fn(() => true))
    deleteSubmission.mockResolvedValue(undefined)

    // 模拟管理员改了标题还没保存
    const titleInput = wrapper.find('input[required]')
    await titleInput.setValue('改到一半的标题')

    const remove = wrapper.findAll('button').find((b) => b.text() === '删除')!
    await remove.trigger('click')
    await vi.waitFor(() => expect(deleteSubmission).toHaveBeenCalled())
    await wrapper.vm.$nextTick()

    // 配额变了所以要重取活动
    expect(getAdminEvent.mock.calls.length).toBeGreaterThan(1)
    // 但表单不能被冲掉
    expect((titleInput.element as HTMLInputElement).value).toBe('改到一半的标题')
    vi.unstubAllGlobals()
    wrapper.unmount()
  })

  it('删到当前页空了会自动退一页', async () => {
    // 否则会停在一个已经不存在的页码上，看到一片空白
    const { wrapper } = await mountView()

    // 先翻到最后一页（第 7 页）
    await pagerButton(wrapper, '最后一页').trigger('click')
    await vi.waitFor(() => expect(lastFilters().page).toBe(7))
    await wrapper.vm.$nextTick()

    // 现在总数降到 120（正好 6 页），第 7 页空了。
    // 注意总数必须**始终**报 120，否则回退到第 6 页时总数又变回 137，页数跟着回去。
    listEventSubmissions.mockImplementation(
      (_id: string, filters: { page?: number; page_size?: number }) => {
        const page = filters.page ?? 1
        const start = (page - 1) * 20
        const count = Math.max(0, Math.min(20, 120 - start))
        return Promise.resolve({
          submissions: Array.from({ length: count }, (_, index) => submission(start + index + 1)),
          total: 120,
        })
      },
    )

    await pagerButton(wrapper, '上一页').trigger('click')
    await pagerButton(wrapper, '下一页').trigger('click')

    await vi.waitFor(() => expect(wrapper.find('.pager__position').text()).toBe('6 / 6'))
    expect(lastFilters().page).toBe(6)
    wrapper.unmount()
  })
})

describe('点行看详情', () => {
  const dialog = (wrapper: ReturnType<typeof mount>) =>
    wrapper.find('dialog').element as HTMLDialogElement

  it('点整行打开详情对话框', async () => {
    const { wrapper } = await mountView()

    await wrapper.find('tbody tr').trigger('click')

    expect(dialog(wrapper).open).toBe(true)
    expect(wrapper.find('.detail__title').text()).toContain('#1')
    wrapper.unmount()
  })

  it('对话框给出这一条的完整内容', async () => {
    const { wrapper } = await mountView()
    await wrapper.find('tbody tr').trigger('click')

    // 列表里那一格是截断的摘要，详情里必须是完整 JSON
    expect(wrapper.find('.detail__json').text()).toContain('"name": "n1"')
    wrapper.unmount()
  })

  it('点操作按钮不会顺带弹出详情', async () => {
    // 否则"删除"和"看详情"会同时发生
    const { wrapper } = await mountView()
    reviewSubmission.mockResolvedValue(submission(1))

    const accept = wrapper.findAll('button').find((b) => b.text() === '采用')!
    await accept.trigger('click')
    await vi.waitFor(() => expect(reviewSubmission).toHaveBeenCalled())

    expect(dialog(wrapper).open).toBe(false)
    wrapper.unmount()
  })

  it('点编号按钮也能打开 —— 键盘用户的入口', async () => {
    // 整行可点只对鼠标友好，编号做成按钮才有真正的控件可聚焦
    const { wrapper } = await mountView()

    await wrapper.find('.row-link').trigger('click')

    expect(dialog(wrapper).open).toBe(true)
    wrapper.unmount()
  })

  it('关闭后不再显示详情', async () => {
    const { wrapper } = await mountView()
    await wrapper.find('tbody tr').trigger('click')
    expect(dialog(wrapper).open).toBe(true)

    dialog(wrapper).close()
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()

    expect(dialog(wrapper).open).toBe(false)
    wrapper.unmount()
  })

  it('翻页后详情关掉，不会停在上一页那一条上', async () => {
    const { wrapper } = await mountView()
    await wrapper.find('tbody tr').trigger('click')
    expect(dialog(wrapper).open).toBe(true)

    // 页码变了但 detail 没清的话，对话框会继续显示一条已经不在列表里的记录
    dialog(wrapper).close()
    await wrapper.vm.$nextTick()
    await pagerButton(wrapper, '下一页').trigger('click')
    await vi.waitFor(() => expect(lastFilters().page).toBe(2))
    await wrapper.vm.$nextTick()

    expect(dialog(wrapper).open).toBe(false)
    wrapper.unmount()
  })
})
