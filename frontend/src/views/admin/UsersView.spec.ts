/**
 * 用户管理：分页、筛选、名单与批量操作。
 *
 * 分页是之前补的，补之前**真的坏了**：后端 `/admin/users` 默认每页 50 条，而前端
 * 不传分页参数却在页脚显示真实总数 —— 用户超过 50 时列表静默截断，数字和内容对
 * 不上。所以这一组既盯着分页，也盯着"名单跨页累积"这类容易做错的联动。
 */
import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// vi.mock 的工厂会被提升到文件顶部，所以这几个桩必须用 vi.hoisted 声明 ——
// 普通的 const 在工厂执行时还没初始化
const { get, patchFn, post } = vi.hoisted(() => ({
  get: vi.fn(),
  patchFn: vi.fn(),
  post: vi.fn(),
}))

// ApiError 要保留真实实现：组件用 instanceof 判断它
vi.mock('@/api/client', async () => {
  const actual = await vi.importActual<typeof import('@/api/client')>('@/api/client')
  return { ApiError: actual.ApiError, http: { get, patch: patchFn, post } }
})

import { ApiError } from '@/api/client'
import { useToast } from '@/composables/useToast'
import UsersView from './UsersView.vue'
import type { UserAdmin } from '@/types/api'

const TOTAL = 137

function user(id: number, overrides: Partial<UserAdmin> = {}): UserAdmin {
  return {
    id,
    username: `u${id}`,
    display_name: `用户 ${id}`,
    role: 'user',
    email: null,
    email_verified: false,
    is_active: true,
    created_at: '2026-10-01T00:00:00Z',
    ...overrides,
  }
}

/** 按请求的页码切片，总数固定 —— 和真实后端一样 */
function pageOf(page: number, size: number) {
  const start = (page - 1) * size
  const count = Math.max(0, Math.min(size, TOTAL - start))
  return {
    data: {
      users: Array.from({ length: count }, (_, index) => user(start + index + 1)),
      total: TOTAL,
    },
  }
}

async function mountView() {
  get.mockImplementation((_url: string, config?: { params?: Record<string, unknown> }) =>
    Promise.resolve(pageOf(Number(config?.params?.page ?? 1), Number(config?.params?.page_size ?? 20))),
  )

  const wrapper = mount(UsersView, { attachTo: document.body })
  await vi.waitFor(() => expect(get).toHaveBeenCalled())
  await wrapper.vm.$nextTick()
  return wrapper
}

/** 上一次请求 `/admin/users` 时用的参数 */
function lastParams() {
  const calls = get.mock.calls.filter((call) => call[0] === '/admin/users')
  return calls[calls.length - 1]?.[1]?.params as Record<string, unknown>
}

function pagerButton(wrapper: ReturnType<typeof mount>, label: string) {
  const byLabel = wrapper.findAll('button').find((b) => b.attributes('aria-label') === label)
  if (byLabel) return byLabel
  return wrapper.findAll('button').find((b) => b.text() === label)!
}

beforeEach(() => {
  vi.clearAllMocks()
  // 通知是模块级状态，会跨用例残留
  useToast().clear()
  // 默认"全都改成功"
  post.mockImplementation((url: string, body?: { ids?: number[] }) => {
    if (url === '/admin/users:bulk') {
      return Promise.resolve({ data: { updated: body?.ids?.length ?? 0 } })
    }
    return Promise.resolve({ data: {} })
  })
})
afterEach(() => {
  vi.restoreAllMocks()
})

describe('分页', () => {
  it('首次加载就带上页码与每页条数', async () => {
    const wrapper = await mountView()

    expect(lastParams()).toMatchObject({ page: 1, page_size: 20 })
    expect(wrapper.findAll('tbody tr')).toHaveLength(20)
    wrapper.unmount()
  })

  it('总数只在分页器里显示一处', async () => {
    // 两处各显示一份数字，迟早会出现对不上的时候 —— 这次就是
    const wrapper = await mountView()

    expect(wrapper.find('.pager__range').text()).toContain('共 137 人')
    expect(wrapper.find('.head').text()).not.toContain('137')
    wrapper.unmount()
  })

  it('分页器用"人"作单位', async () => {
    const wrapper = await mountView()

    expect(wrapper.find('.pager__range').text()).toContain('第 1–20 人')
    wrapper.unmount()
  })

  it('点下一页按第 2 页重新请求', async () => {
    const wrapper = await mountView()

    await pagerButton(wrapper, '下一页').trigger('click')
    await vi.waitFor(() => expect(lastParams().page).toBe(2))
    await wrapper.vm.$nextTick()

    // 表里确实是第 21 个人起。第一格现在是复选框，所以按编号按钮取
    expect(wrapper.find('.row-link').text()).toBe('21')
    wrapper.unmount()
  })

  it('改每页条数回到第 1 页', async () => {
    const wrapper = await mountView()

    await pagerButton(wrapper, '下一页').trigger('click')
    await vi.waitFor(() => expect(lastParams().page).toBe(2))

    await wrapper.find('.pager__size .select__trigger').trigger('click')
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()
    const option = wrapper.findAll('[role="option"]').find((o) => o.text() === '50 / 页')!
    await option.trigger('click')

    await vi.waitFor(() => expect(lastParams().page_size).toBe(50))
    expect(lastParams().page).toBe(1)
    wrapper.unmount()
  })

  it('换筛选条件回到第 1 页', async () => {
    // 否则会停在一个新结果集里不存在的页码上
    const wrapper = await mountView()

    await pagerButton(wrapper, '下一页').trigger('click')
    await vi.waitFor(() => expect(lastParams().page).toBe(2))

    await wrapper.find('.filters input').setValue('zhang')
    await wrapper.find('.filters input').trigger('keyup.enter')

    await vi.waitFor(() => expect(lastParams().username).toBe('zhang'))
    expect(lastParams().page).toBe(1)
    wrapper.unmount()
  })

  it('点查询也回到第 1 页', async () => {
    // 原来的「刷新」与「查询」是同一件事（都按当前条件重新拉第一页），合并成一个
    const wrapper = await mountView()

    await pagerButton(wrapper, '下一页').trigger('click')
    await vi.waitFor(() => expect(lastParams().page).toBe(2))

    await wrapper.findAll('button').find((b) => b.text() === '查询')!.trigger('click')

    await vi.waitFor(() => expect(lastParams().page).toBe(1))
    wrapper.unmount()
  })

  it('当前页越界时自动退一页', async () => {
    // 停用某个人之后，按"启用"筛选的结果变少，当前页可能已经不存在
    const wrapper = await mountView()

    await pagerButton(wrapper, '最后一页').trigger('click')
    await vi.waitFor(() => expect(lastParams().page).toBe(7))

    // 现在总数降到 20，只剩一页
    get.mockImplementation((_url: string, config?: { params?: Record<string, unknown> }) => {
      const page = Number(config?.params?.page ?? 1)
      return Promise.resolve({
        data: { users: page === 1 ? [user(1)] : [], total: 1 },
      })
    })

    await pagerButton(wrapper, '上一页').trigger('click')
    await pagerButton(wrapper, '下一页').trigger('click')

    await vi.waitFor(() => expect(wrapper.find('.pager__position').text()).toBe('1 / 1'))
    expect(lastParams().page).toBe(1)
    wrapper.unmount()
  })

  it('没有用户时不显示分页器', async () => {
    get.mockResolvedValue({ data: { users: [], total: 0 } })

    const wrapper = mount(UsersView, { attachTo: document.body })
    await vi.waitFor(() => expect(get).toHaveBeenCalled())
    await wrapper.vm.$nextTick()

    expect(wrapper.find('.pager').exists()).toBe(false)
    expect(wrapper.find('.empty').text()).toBe('没有符合条件的用户。')
    wrapper.unmount()
  })
})

describe('筛选参数', () => {
  it('空筛选值不发出去，与后端"缺省不过滤"对齐', async () => {
    const wrapper = await mountView()

    expect(lastParams().role).toBeUndefined()
    expect(lastParams().is_active).toBeUndefined()
    expect(lastParams().username).toBeUndefined()
    wrapper.unmount()
  })
})

describe('账号状态标签', () => {
  it('停用用 tag--off', async () => {
    // 曾经用的是 tag--rejected，那个类在状态改造中被改名了，标签因此静默失去配色
    get.mockResolvedValue({
      data: { users: [user(1), user(2, { is_active: false })], total: 2 },
    })

    const wrapper = mount(UsersView, { attachTo: document.body })
    await vi.waitFor(() => expect(get).toHaveBeenCalled())
    await wrapper.vm.$nextTick()

    // 列序：勾选 / 编号 / 用户名 / 显示名 / 角色 / 状态 / 邮箱
    const cells = wrapper.findAll('tbody tr')[1]!.findAll('td')
    expect(cells[5]!.find('.tag').classes()).toContain('tag--off')
    expect(cells[5]!.text()).toBe('停用')
    wrapper.unmount()
  })
})

describe('名单与行交互', () => {
  const button = (wrapper: ReturnType<typeof mount>, label: string) =>
    wrapper.findAll('button').find((b) => b.text() === label)!

  const rosterNames = (wrapper: ReturnType<typeof mount>) =>
    wrapper.findAll('.side__who').map((node) => node.text())

  async function selectRow(wrapper: ReturnType<typeof mount>, index: number) {
    await wrapper.findAll('tbody tr')[index]!.trigger('click')
    await wrapper.vm.$nextTick()
  }

  it('页面结构与提交页一致：筛选在左栏、名单在右栏', async () => {
    const wrapper = await mountView()

    const main = wrapper.find('.split__main')
    expect(main.find('.filters').exists()).toBe(true)
    expect(main.find('table').exists()).toBe(true)
    expect(wrapper.find('.split__side .side').exists()).toBe(true)
    wrapper.unmount()
  })

  it('单击整行只选中，双击才打开详情', async () => {
    const wrapper = await mountView()
    const dialogEl = () => wrapper.find('.user-modal').element as HTMLDialogElement

    await selectRow(wrapper, 0)
    expect(dialogEl().open).toBe(false)
    expect(wrapper.findAll('tbody tr')[0]!.classes()).toContain('row--selected')

    await wrapper.findAll('tbody tr')[0]!.trigger('dblclick')
    await wrapper.vm.$nextTick()
    expect(dialogEl().open).toBe(true)
    wrapper.unmount()
  })

  it('加入名单后跨页保留', async () => {
    const wrapper = await mountView()

    await selectRow(wrapper, 0)
    await button(wrapper, '加入名单').trigger('click')
    await wrapper.vm.$nextTick()

    expect(rosterNames(wrapper)).toEqual(['u1'])
    // 入单后清掉勾选，避免误以为还在"待入单"
    expect(wrapper.find('.pick__count').text()).toBe('已选 0 位')

    // 翻页之后再入一个，名单应当累积
    await pagerButton(wrapper, '下一页').trigger('click')
    await vi.waitFor(() => expect(lastParams().page).toBe(2))
    await wrapper.vm.$nextTick()
    await selectRow(wrapper, 0)
    await button(wrapper, '加入名单').trigger('click')
    await wrapper.vm.$nextTick()

    expect(rosterNames(wrapper)).toHaveLength(2)
    wrapper.unmount()
  })

  it('已在名单里的不重复添加', async () => {
    const wrapper = await mountView()

    for (let i = 0; i < 2; i++) {
      await selectRow(wrapper, 0)
      await button(wrapper, '加入名单').trigger('click')
      await wrapper.vm.$nextTick()
    }

    expect(rosterNames(wrapper)).toEqual(['u1'])
    wrapper.unmount()
  })

  it('移出名单与逐条 × 都能用', async () => {
    const wrapper = await mountView()

    await selectRow(wrapper, 0)
    await button(wrapper, '加入名单').trigger('click')
    await wrapper.vm.$nextTick()
    expect(rosterNames(wrapper)).toEqual(['u1'])

    await wrapper.find('.side__drop').trigger('click')
    await wrapper.vm.$nextTick()
    expect(rosterNames(wrapper)).toEqual([])
    wrapper.unmount()
  })

  it('数量对不上时给出提示', async () => {
    // 期间有人被删掉：批量端点返回的是实际改动数
    const wrapper = await mountView()
    post.mockResolvedValue({ data: { updated: 1 } })

    await selectRow(wrapper, 0)
    await button(wrapper, '加入名单').trigger('click')
    await wrapper.vm.$nextTick()
    await selectRow(wrapper, 1)
    await button(wrapper, '加入名单').trigger('click')
    await wrapper.vm.$nextTick()

    await button(wrapper, '提权').trigger('click')
    await vi.waitFor(() => expect(useToast().toasts.value).toHaveLength(1))

    const [toast] = useToast().toasts.value
    expect(toast!.tone).toBe('ok')
    expect(toast!.message).toContain('已处理 1 位')
    wrapper.unmount()
  })

  it('批量失败走通知而不是内联', async () => {
    /*
      操作结果走通知、加载失败才内联 —— 见 useToast 里那张分工表。
      批量改权限失败是操作结果，不该留在页面上不走。
    */
    const wrapper = await mountView()
    post.mockRejectedValue(new ApiError('only_admin', '不能移除最后一个管理员', 409))

    await selectRow(wrapper, 0)
    await button(wrapper, '加入名单').trigger('click')
    await wrapper.vm.$nextTick()

    await button(wrapper, '降权').trigger('click')
    await vi.waitFor(() => expect(useToast().toasts.value).toHaveLength(1))

    const [toast] = useToast().toasts.value
    expect(toast!.tone).toBe('error')
    expect(toast!.message).toBe('不能移除最后一个管理员')
    // 页面上的内联错误区只留给加载失败
    expect(wrapper.find('.alert').exists()).toBe(false)
    wrapper.unmount()
  })

  it('名单为空时批量按钮都不可点', async () => {
    const wrapper = await mountView()

    for (const label of ['提权', '降权', '停用', '启用']) {
      expect(button(wrapper, label).attributes('disabled')).toBeDefined()
    }
    wrapper.unmount()
  })

  it('批量操作一次请求带上整份名单', async () => {
    const wrapper = await mountView()
    post.mockResolvedValue({ data: { updated: 2 } })

    await selectRow(wrapper, 0)
    await button(wrapper, '加入名单').trigger('click')
    await wrapper.vm.$nextTick()
    await selectRow(wrapper, 1)
    await button(wrapper, '加入名单').trigger('click')
    await wrapper.vm.$nextTick()

    await button(wrapper, '停用').trigger('click')
    await vi.waitFor(() => expect(post).toHaveBeenCalled())

    expect(post).toHaveBeenCalledTimes(1)
    expect(post.mock.calls[0]![0]).toBe('/admin/users:bulk')
    expect(post.mock.calls[0]![1]).toEqual({ ids: [1, 2], is_active: false })
    wrapper.unmount()
  })
})
