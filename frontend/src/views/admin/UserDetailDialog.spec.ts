/**
 * 账号删除的前端部分（任务 3.3 与 3.5）。
 *
 * 删除不可逆，因此这一组盯的不是"按钮能点"，而是**点之前能不能读到代价**：确认框必须
 * 是 danger、文案必须说清"提交会保留"与"署名变成编号"，而取消必须什么都不做。
 */
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { httpDelete, httpGet, toastFail } = vi.hoisted(() => ({
  httpDelete: vi.fn(),
  httpGet: vi.fn(),
  toastFail: vi.fn(),
}))

vi.mock('@/api/client', async () => {
  const actual = await vi.importActual<typeof import('@/api/client')>('@/api/client')
  return {
    ...actual,
    http: {
      get: (...args: unknown[]) => httpGet(...args),
      delete: (...args: unknown[]) => httpDelete(...args),
      post: vi.fn(),
      patch: vi.fn(),
    },
  }
})

vi.mock('@/composables/useToast', async () => {
  const actual =
    await vi.importActual<typeof import('@/composables/useToast')>('@/composables/useToast')
  return {
    ...actual,
    useToast: () => ({ ...actual.useToast(), fail: toastFail, ok: vi.fn() }),
  }
})

import { useConfirm } from '@/composables/useConfirm'
import { useToast } from '@/composables/useToast'
import UserDetailDialog from './UserDetailDialog.vue'
import UsersView from './UsersView.vue'
import type { UserAdmin } from '@/types/api'

const USER: UserAdmin = {
  id: 7,
  username: 'victim',
  display_name: '受害者',
  role: 'user',
  is_active: true,
  email: 'victim@example.com',
  email_verified: true,
  created_at: '2026-01-01T00:00:00Z',
} as UserAdmin

function page(users: UserAdmin[] = [USER]) {
  httpGet.mockResolvedValue({
    data: { users, total: users.length, page: 1, page_size: 20 },
  })
}

async function mountView() {
  page()
  const wrapper = mount(UsersView, {
    global: { plugins: [createPinia()], stubs: { SplitPane: false } },
    attachTo: document.body,
  })
  await vi.waitFor(() => expect(httpGet).toHaveBeenCalled())
  await wrapper.vm.$nextTick()
  return wrapper
}

/** 打开某一行详情的按钮（双击行） */
async function openDetail(wrapper: ReturnType<typeof mount>) {
  await wrapper.find('.row-link').trigger('click')
  await wrapper.vm.$nextTick()
  return wrapper.find('.detail__danger')
}

beforeEach(() => {
  vi.clearAllMocks()
  setActivePinia(createPinia())
  // 通知与确认框都是模块级状态，会跨用例残留
  useToast().clear()
  useConfirm().clear()
})

describe('详情里的删除区域', () => {
  it('是独立的危险区域，且文案说清代价', async () => {
    const wrapper = mount(UserDetailDialog, {
      props: { user: USER },
      global: { plugins: [createPinia()], stubs: { teleport: true } },
    })

    const danger = wrapper.find('.detail__danger')
    expect(danger.exists()).toBe(true)
    const text = danger.text()
    // 三件必须让人在点之前知道的事
    expect(text).toContain('无法恢复')
    expect(text).toContain('提交过的内容会保留')
    expect(text).toContain('「已删除」')
    wrapper.unmount()
  })

  it('只发事件，不自己去改数据', async () => {
    const wrapper = mount(UserDetailDialog, {
      props: { user: USER },
      global: { plugins: [createPinia()], stubs: { teleport: true } },
    })
    await wrapper.find('.detail__danger button').trigger('click')

    expect(wrapper.emitted('delete')?.[0]).toEqual([USER])
    expect(httpDelete).not.toHaveBeenCalled()
    wrapper.unmount()
  })
})

describe('删除的确认与刷新', () => {
  it('走应用自有的确认框，且是 danger', async () => {
    const wrapper = await mountView()
    await openDetail(wrapper)
    await wrapper.find('.detail__danger button').trigger('click')

    await vi.waitFor(() => expect(useConfirm().request.value).not.toBeNull())
    const request = useConfirm().request.value
    expect(request?.danger).toBe(true)
    expect(request?.title).toContain('删除')
    // 文案里那两个代价
    expect(request?.message).toContain('无法恢复')
    expect(request?.message).toContain('提交过的内容会保留')

    useConfirm().settle(false)
    wrapper.unmount()
  })

  it('取消时什么都不做', async () => {
    const wrapper = await mountView()
    await openDetail(wrapper)
    await wrapper.find('.detail__danger button').trigger('click')

    await vi.waitFor(() => expect(useConfirm().request.value).not.toBeNull())
    useConfirm().settle(false)
    await wrapper.vm.$nextTick()

    expect(httpDelete).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('确认后请求删除，并重拉列表', async () => {
    httpDelete.mockResolvedValue({ status: 204 })
    const wrapper = await mountView()
    const before = httpGet.mock.calls.length

    await openDetail(wrapper)
    await wrapper.find('.detail__danger button').trigger('click')
    await vi.waitFor(() => expect(useConfirm().request.value).not.toBeNull())
    useConfirm().settle(true)

    await vi.waitFor(() => expect(httpDelete).toHaveBeenCalledWith('/admin/users/7'))
    // 任务 3.5：删完必须重拉，否则被删的那一行会作为死数据留在页面上
    await vi.waitFor(() => expect(httpGet.mock.calls.length).toBeGreaterThan(before))
    wrapper.unmount()
  })

  it('失败时也重拉，让列表回到服务端的真实状态', async () => {
    httpDelete.mockRejectedValue(new Error('boom'))
    const wrapper = await mountView()
    const before = httpGet.mock.calls.length

    await openDetail(wrapper)
    await wrapper.find('.detail__danger button').trigger('click')
    await vi.waitFor(() => expect(useConfirm().request.value).not.toBeNull())
    useConfirm().settle(true)

    await vi.waitFor(() => expect(toastFail).toHaveBeenCalled())
    await vi.waitFor(() => expect(httpGet.mock.calls.length).toBeGreaterThan(before))
    wrapper.unmount()
  })
})
