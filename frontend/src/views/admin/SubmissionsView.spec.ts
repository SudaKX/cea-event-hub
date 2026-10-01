/**
 * 提交列表：点行看详情，以及行内控件的排除。
 *
 * 这一页比活动详情多一个复选框（批量选择），而复选框正好在行内 —— 勾选时弹出详情
 * 会很烦。所以这里主要验证"哪些点击不算看详情"。
 */
import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

const listAdminEvents = vi.fn()
const listEventSubmissions = vi.fn()

vi.mock('@/api/events', () => ({
  listAdminEvents: (...args: unknown[]) => listAdminEvents(...args),
}))
vi.mock('@/api/submissions', () => ({
  listEventSubmissions: (...args: unknown[]) => listEventSubmissions(...args),
  reviewSubmission: vi.fn(),
  deleteSubmission: vi.fn(),
  deleteSubmissions: vi.fn(),
  attachmentUrl: (id: number, fileId: number) => `/api/v1/submissions/${id}/files/${fileId}`,
}))

import SubmissionsView from './SubmissionsView.vue'
import { SUBMISSION_STATUS } from '@/domain/submission'

const EVENT = {
  id: 'spring-2026',
  title: '春季招新',
  summary: null,
  status: 'live',
  content_version: 1,
  entry_path: 'index.html',
  submission_requires_login: false,
  submissions_open_at: null,
  submissions_close_at: null,
  max_submissions: null,
  quota: { limit: 4096, used: 2, remaining: 4094 },
  owner_id: null,
  created_at: '2026-10-01T00:00:00Z',
  updated_at: '2026-10-01T00:00:00Z',
}

function submission(id: number) {
  return {
    id,
    event_id: 'spring-2026',
    kind: 'signup',
    status: SUBMISSION_STATUS.RECEIVED,
    submitter: `a:browser-${id}`,
    from_authenticated_user: false,
    payload: { name: `n${id}` },
    created_at: '2026-10-01T00:00:00Z',
    files: [],
  }
}

async function mountView() {
  listAdminEvents.mockResolvedValue([EVENT])
  listEventSubmissions.mockResolvedValue({
    submissions: [submission(1), submission(2)],
    total: 2,
  })

  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/:pathMatch(.*)*', component: { template: '<div />' } }],
  })
  await router.push('/admin/submissions')
  await router.isReady()

  const wrapper = mount(SubmissionsView, {
    global: { plugins: [router, createPinia()] },
    attachTo: document.body,
  })
  await vi.waitFor(() => expect(listEventSubmissions).toHaveBeenCalled())
  await wrapper.vm.$nextTick()
  return wrapper
}

/**
 * 详情对话框。页面上还有另一个 dialog（帮助弹窗），所以必须指名道姓 ——
 * `find('dialog')` 会取到文档里第一个。
 */
const dialog = (wrapper: ReturnType<typeof mount>) =>
  wrapper.find('.detail-modal').element as HTMLDialogElement

beforeEach(() => {
  vi.clearAllMocks()
})
afterEach(() => {
  vi.restoreAllMocks()
})

describe('点行看详情', () => {
  it('点整行打开详情', async () => {
    const wrapper = await mountView()

    await wrapper.findAll('tbody tr')[1]!.trigger('click')

    expect(dialog(wrapper).open).toBe(true)
    expect(wrapper.find('.detail-modal .modal__title').text()).toContain('#2')
    wrapper.unmount()
  })

  it('勾选复选框不会弹出详情', async () => {
    // 批量选择时每勾一条就弹一个对话框，这页就没法用了
    const wrapper = await mountView()

    await wrapper.find('tbody input[type="checkbox"]').trigger('click')

    expect(dialog(wrapper).open).toBe(false)
    wrapper.unmount()
  })

  it('点操作按钮不会弹出详情', async () => {
    const wrapper = await mountView()

    const remove = wrapper.findAll('button').find((b) => b.text() === '删除')!
    // 这里不点确认，所以删除不会真的发生，只看对话框有没有被顺带打开
    vi.stubGlobal('confirm', vi.fn(() => false))
    await remove.trigger('click')

    expect(dialog(wrapper).open).toBe(false)
    vi.unstubAllGlobals()
    wrapper.unmount()
  })

  it('点编号按钮打开详情', async () => {    const wrapper = await mountView()

    await wrapper.find('.row-link').trigger('click')

    expect(dialog(wrapper).open).toBe(true)
    wrapper.unmount()
  })

  it('摘要用 $display，详情里仍有完整 JSON', async () => {    listAdminEvents.mockResolvedValue([EVENT])
    listEventSubmissions.mockResolvedValue({
      submissions: [
        {
          ...submission(1),
          payload: { $display: '张三 · 2 年级', name: '张三', secret: 'x' },
        },
      ],
      total: 1,
    })

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/:pathMatch(.*)*', component: { template: '<div />' } }],
    })
    await router.push('/admin/submissions')
    await router.isReady()

    const wrapper = mount(SubmissionsView, {
      global: { plugins: [router, createPinia()] },
      attachTo: document.body,
    })
    await vi.waitFor(() => expect(listEventSubmissions).toHaveBeenCalled())
    await wrapper.vm.$nextTick()

    expect(wrapper.find('.payload-cell .cell').text()).toBe('张三 · 2 年级')

    await wrapper.find('tbody tr').trigger('click')
    expect(wrapper.find('.detail__json').text()).toContain('secret')
    wrapper.unmount()
  })
})

describe('提交者那一列的截断', () => {
  it('标识走截断组件，而不是直接插值', async () => {
    // 匿名标识是 `a:<uuid>`，38 个字符，远超列宽。直接插值只会被硬裁，没有省略号
    const wrapper = await mountView()

    const cell = wrapper.find('td.submitter')
    expect(cell.find('.cell').text()).toBe('a:browser-1')
    wrapper.unmount()
  })

  it('标签在截断元素之外，不会被一起裁掉', async () => {
    // "匿名"才是这一列真正要看的信息，跟着标识一起被截就本末倒置了
    const wrapper = await mountView()

    const cell = wrapper.find('td.submitter')
    expect(cell.find('.cell .tag').exists()).toBe(false)
    expect(cell.find('.tag').text()).toBe('匿名')
    wrapper.unmount()
  })

  it('超长标识也不会撑破单元格', async () => {
    // 断言的是结构：文本进了截断组件，剩下的交给 CSS
    listAdminEvents.mockResolvedValue([EVENT])
    listEventSubmissions.mockResolvedValue({
      submissions: [
        {
          ...submission(1),
          submitter: 'a:550e8400-e29b-41d4-a716-446655440000',
        },
      ],
      total: 1,
    })

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/:pathMatch(.*)*', component: { template: '<div />' } }],
    })
    await router.push('/admin/submissions')
    await router.isReady()

    const wrapper = mount(SubmissionsView, {
      global: { plugins: [router, createPinia()] },
      attachTo: document.body,
    })
    await vi.waitFor(() => expect(listEventSubmissions).toHaveBeenCalled())
    await wrapper.vm.$nextTick()

    expect(wrapper.find('td.submitter .cell').text()).toBe(
      'a:550e8400-e29b-41d4-a716-446655440000',
    )
    wrapper.unmount()
  })

  it('分类列同样截断', async () => {
    // 分类允许 64 个字符，和标识是同一类问题
    const wrapper = await mountView()

    expect(wrapper.find('td.kind-cell .cell').text()).toBe('signup')
    wrapper.unmount()
  })
})

describe('审核说明收在 ? 按钮里', () => {
  const helpDialog = (wrapper: ReturnType<typeof mount>) =>
    wrapper.find('.help-modal').element as HTMLDialogElement

  it('页面上没有常驻的说明卡片', async () => {
    // 读一次就够的内容常驻，只会把真正要看的东西往下挤
    const wrapper = await mountView()

    expect(wrapper.find('.legend').exists()).toBe(false)
    wrapper.unmount()
  })

  it('标题右侧有 ? 按钮', async () => {
    const wrapper = await mountView()

    const help = wrapper.find('.help')
    expect(help.exists()).toBe(true)
    expect(help.text()).toBe('?')
    expect(help.attributes('aria-label')).toBe('审核动作说明')
    wrapper.unmount()
  })

  it('点 ? 弹出说明', async () => {
    const wrapper = await mountView()
    expect(helpDialog(wrapper).open).toBe(false)

    await wrapper.find('.help').trigger('click')
    await wrapper.vm.$nextTick()

    expect(helpDialog(wrapper).open).toBe(true)
    expect(wrapper.find('.help-modal').text()).toContain('不释放名额')
    wrapper.unmount()
  })

  it('说明里讲清"不采用"与"删除"的区别', async () => {
    // 最容易误判的一条：标成不采用并不腾出名额
    const wrapper = await mountView()
    await wrapper.find('.help').trigger('click')
    await wrapper.vm.$nextTick()

    const text = wrapper.find('.help-modal').text()
    expect(text).toContain('采用 / 不采用')
    expect(text).toContain('删除')
    expect(text).toContain('释放一个名额')
    wrapper.unmount()
  })

  it('关掉之后不再显示', async () => {
    const wrapper = await mountView()
    await wrapper.find('.help').trigger('click')
    await wrapper.vm.$nextTick()

    helpDialog(wrapper).close()
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()

    expect(helpDialog(wrapper).open).toBe(false)
    wrapper.unmount()
  })

  it('打开说明不会影响提交详情那一个', async () => {
    // 两个 dialog 各管各的，别串了
    const wrapper = await mountView()

    await wrapper.find('.help').trigger('click')
    await wrapper.vm.$nextTick()

    expect(dialog(wrapper).open).toBe(false)
    wrapper.unmount()
  })
})
