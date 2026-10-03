/**
 * 提交列表：单击选中、双击看详情、批量操作。
 *
 * 这一页的交互角色是分开的：整行负责选中（高频），详情要点两下或用编号按钮
 * （键盘入口），改状态与删除则统一走底部的批量按钮 —— 所以这里主要验证
 * "哪种点击触发哪种动作"，以及行内控件不会被整行的点击顺带翻转。
 */
import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

// vi.mock 的工厂会被提升，所以桩要用 vi.hoisted 声明
const { listAdminEvents, listEventSubmissions, reviewSubmissions, deleteSubmissions } =
  vi.hoisted(() => ({
    listAdminEvents: vi.fn(),
    listEventSubmissions: vi.fn(),
    reviewSubmissions: vi.fn(),
    deleteSubmissions: vi.fn(),
  }))

vi.mock('@/api/events', () => ({
  listAdminEvents: (...args: unknown[]) => listAdminEvents(...args),
}))
vi.mock('@/api/submissions', () => ({
  listEventSubmissions: (...args: unknown[]) => listEventSubmissions(...args),
  // 批量端点：一次请求改一批，返回实际改动数
  reviewSubmissions: (...args: unknown[]) => reviewSubmissions(...args),
  reviewSubmission: vi.fn(),
  deleteSubmission: vi.fn(),
  deleteSubmissions: (...args: unknown[]) => deleteSubmissions(...args),
  attachmentUrl: (id: number, fileId: number) => `/api/v1/submissions/${id}/files/${fileId}`,
}))

import { useConfirm } from '@/composables/useConfirm'
import { useToast } from '@/composables/useToast'
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

/**
 * 挂载视图。
 *
 * `total` 可以调大，用来造出多页 —— 像"翻到第 2 页再搜索，应当回到第 1 页"这类
 * 断言，只有一页时根本无从验证（下一页按钮是禁用的）。
 */
async function mountView(options: { total?: number } = {}) {
  const total = options.total ?? 2
  listAdminEvents.mockResolvedValue([EVENT])
  listEventSubmissions.mockResolvedValue({
    submissions: [submission(1), submission(2)],
    total,
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
  // 通知与确认框都是模块级状态，会跨用例残留
  useToast().clear()
  useConfirm().clear()
  // 默认"全都改成功"
  reviewSubmissions.mockImplementation((ids: number[]) => Promise.resolve(ids.length))
  deleteSubmissions.mockResolvedValue(0)
})
afterEach(() => {
  vi.restoreAllMocks()
})

describe('队列', () => {
  /*
    模型：列表的勾选是**临时的**（本页有效），队列是**跨页累积**的挑选结果。
    「放入 / 移出队列」作用于勾选，采用／不采用／删除作用于**整个队列** ——
    这正是队列存在的理由：一次只显示一页，而"把挑出来的十几条一起处理"光靠勾选
    做不到，翻页就丢了。
  */
  async function select(wrapper: ReturnType<typeof mount>, index: number) {
    await wrapper.findAll('tbody tr')[index]!.trigger('click')
    await wrapper.vm.$nextTick()
  }

  const button = (wrapper: ReturnType<typeof mount>, label: string) =>
    wrapper.findAll('button').find((b) => b.text() === label)!

  /** 选中前 index 行后入队 */
  async function enqueueRows(wrapper: ReturnType<typeof mount>, indexes: number[]) {
    for (const index of indexes) await select(wrapper, index)
    await button(wrapper, '放入队列').trigger('click')
    await wrapper.vm.$nextTick()
  }

  const queuedIds = (wrapper: ReturnType<typeof mount>) =>
    wrapper.findAll('.side__id').map((node) => node.text())

  it('队列初始为空', async () => {
    const wrapper = await mountView()

    expect(wrapper.find('.side__title').text()).toContain('0')
    expect(wrapper.find('.side__empty').exists()).toBe(true)
    wrapper.unmount()
  })

  it('放入队列把选中的行收进队列', async () => {
    const wrapper = await mountView()
    await enqueueRows(wrapper, [0, 1])

    expect(queuedIds(wrapper)).toEqual(['#1', '#2'])
    expect(wrapper.find('.side__title').text()).toContain('2')
    wrapper.unmount()
  })

  it('入队后清掉勾选 —— 勾选的意义已经完成', async () => {
    const wrapper = await mountView()
    await enqueueRows(wrapper, [0])

    expect(wrapper.find('.pick__count').text()).toBe('已选 0 行')
    expect(wrapper.findAll('tbody tr')[0]!.classes()).not.toContain('row--selected')
    wrapper.unmount()
  })

  it('已在队列里的不重复添加', async () => {
    // 跨页挑选时很容易重复点到同一条，重复入队会让"对队列执行"处理两次
    const wrapper = await mountView()
    await enqueueRows(wrapper, [0])
    await enqueueRows(wrapper, [0])

    expect(queuedIds(wrapper)).toEqual(['#1'])
    wrapper.unmount()
  })

  it('移出队列把选中的行从队列里去掉', async () => {
    const wrapper = await mountView()
    await enqueueRows(wrapper, [0, 1])

    await select(wrapper, 0)
    await button(wrapper, '移出队列').trigger('click')
    await wrapper.vm.$nextTick()

    expect(queuedIds(wrapper)).toEqual(['#2'])
    wrapper.unmount()
  })

  it('逐条 × 可以移出', async () => {
    const wrapper = await mountView()
    await enqueueRows(wrapper, [0, 1])

    await wrapper.findAll('.side__drop')[0]!.trigger('click')
    await wrapper.vm.$nextTick()

    expect(queuedIds(wrapper)).toEqual(['#2'])
    wrapper.unmount()
  })

  it('清空按钮只在有内容时出现', async () => {
    const wrapper = await mountView()
    expect(wrapper.findAll('button').some((b) => b.text() === '清空')).toBe(false)

    await enqueueRows(wrapper, [0])
    await button(wrapper, '清空').trigger('click')
    await wrapper.vm.$nextTick()

    expect(wrapper.find('.side__empty').exists()).toBe(true)
    wrapper.unmount()
  })

  it('渲染队列条目的状态标签', async () => {
    const wrapper = await mountView()
    await enqueueRows(wrapper, [0])

    expect(wrapper.find('.side__item .tag').text()).toBe('待处理')
    wrapper.unmount()
  })

  it('没有勾选时，放入／移出队列都不可点', async () => {
    const wrapper = await mountView()

    expect(button(wrapper, '放入队列').attributes('disabled')).toBeDefined()
    expect(button(wrapper, '移出队列').attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it('队列为空时，采用／不采用／删除都不可点', async () => {
    const wrapper = await mountView()

    for (const label of ['采用', '不采用', '删除']) {
      expect(button(wrapper, label).attributes('disabled')).toBeDefined()
    }
    wrapper.unmount()
  })

  it('采用作用于**队列**，而不是当前勾选', async () => {
    const wrapper = await mountView()
    await enqueueRows(wrapper, [0, 1])
    // 再勾一条但**不入队**，它不该被处理
    await select(wrapper, 0)

    await button(wrapper, '采用').trigger('click')
    await vi.waitFor(() => expect(reviewSubmissions).toHaveBeenCalled())

    // 一次请求带上整条队列，而不是逐条 PATCH
    expect(reviewSubmissions).toHaveBeenCalledTimes(1)
    expect(reviewSubmissions.mock.calls[0]![0]).toEqual([1, 2])
    expect(reviewSubmissions.mock.calls[0]![1]).toBe(SUBMISSION_STATUS.ACCEPTED)
    wrapper.unmount()
  })

  it('不采用发的是 ignored 码值', async () => {
    const wrapper = await mountView()
    await enqueueRows(wrapper, [0])

    await button(wrapper, '不采用').trigger('click')
    await vi.waitFor(() => expect(reviewSubmissions).toHaveBeenCalled())

    expect(reviewSubmissions.mock.calls[0]![1]).toBe(SUBMISSION_STATUS.IGNORED)
    wrapper.unmount()
  })

  it('实际改动数少于点名数时给出提示', async () => {
    /*
      勾选期间可能有人删掉了其中几条。批量端点返回的是**实际改动数** ——
      对不上时说一声，否则管理员会以为全改了。

      提示走通知（见 useToast 的分工表），所以断言通知栈而不是页面上的段落。
    */
    const wrapper = await mountView()
    await enqueueRows(wrapper, [0, 1])
    reviewSubmissions.mockResolvedValue(1)

    await button(wrapper, '采用').trigger('click')
    await vi.waitFor(() => expect(useToast().toasts.value).toHaveLength(1))

    const [toast] = useToast().toasts.value
    expect(toast!.tone).toBe('ok')
    expect(toast!.message).toContain('已处理 1 条')
    expect(toast!.message).toContain('1 条已不存在')
    wrapper.unmount()
  })

  it('全部成功时也给一条通知', async () => {
    /*
      全成功也要说一声：批量操作的结果不在用户视线里（列表会重新加载，但"改了几
      条"并不直接可见），没有回馈会让人不确定到底生效没有。
    */
    const wrapper = await mountView()
    await enqueueRows(wrapper, [0, 1])

    await button(wrapper, '采用').trigger('click')
    await vi.waitFor(() => expect(useToast().toasts.value).toHaveLength(1))

    expect(useToast().toasts.value[0]!.message).toBe('已处理 2 条')
    wrapper.unmount()
  })

  it('执行之后队列清空 —— 那些条目的目的已经达到', async () => {
    const wrapper = await mountView()
    await enqueueRows(wrapper, [0])

    await button(wrapper, '采用').trigger('click')
    await vi.waitFor(() => expect(reviewSubmissions).toHaveBeenCalled())

    expect(wrapper.find('.side__empty').exists()).toBe(true)
    wrapper.unmount()
  })

  it('删除把队列里的 id 一次交给批量接口', async () => {
    const wrapper = await mountView()
    await enqueueRows(wrapper, [0, 1])

    await button(wrapper, '删除').trigger('click')
    // 确认框走的是我们自己的组件，不再是 window.confirm —— 直接结算它
    await vi.waitFor(() => expect(useConfirm().request.value).not.toBeNull())
    useConfirm().settle(true)
    await vi.waitFor(() => expect(deleteSubmissions).toHaveBeenCalled())

    expect(deleteSubmissions.mock.calls[0]![0]).toEqual([1, 2])
    wrapper.unmount()
  })

  it('删除前要确认，取消就什么都不做', async () => {
    const wrapper = await mountView()
    await enqueueRows(wrapper, [0])

    await button(wrapper, '删除').trigger('click')
    await vi.waitFor(() => expect(useConfirm().request.value).not.toBeNull())

    // 确认框的文案要能说清后果
    expect(useConfirm().request.value!.message).toContain('不可撤销')

    useConfirm().settle(false)
    await wrapper.vm.$nextTick()

    expect(deleteSubmissions).not.toHaveBeenCalled()
    // 取消之后框要收掉
    expect(useConfirm().request.value).toBeNull()
    wrapper.unmount()
  })

  it('面板与列表并排，中间是可拖的分隔线', async () => {
    const wrapper = await mountView()

    expect(wrapper.find('.split .side').exists()).toBe(true)
    expect(wrapper.find('.split__handle').attributes('role')).toBe('separator')
    wrapper.unmount()
  })

  it('两组按钮各归其位：入队跟着列表，执行跟着队列', async () => {
    /*
      按钮摆在哪儿等于声明它管的是哪一片范围：
      「放入 / 移出队列」作用于**勾选**（本页临时），所以留在列表底部的操作行；
      「采用 / 不采用 / 删除」作用于**跨页累积的队列**，所以留在右侧面板。

      断言的是**按钮标签**而不是整块文字：侧栏的空状态提示里也写着「放入队列」，
      用 text() 会匹配到说明而不是按钮。
    */
    const wrapper = await mountView()
    const labelsIn = (selector: string) =>
      wrapper.find(selector).findAll('button').map((b) => b.text())

    expect(labelsIn('.pick')).toEqual(expect.arrayContaining(['放入队列', '移出队列']))

    expect(labelsIn('.side')).toEqual(expect.arrayContaining(['采用', '不采用', '删除']))
    expect(labelsIn('.side')).not.toContain('放入队列')

    expect(wrapper.find('.head').text()).not.toContain('删除选中')
    wrapper.unmount()
  })

  it('列表底部分两行：操作一行、翻页一行', async () => {
    // 挤成一行时三个控件组连成一片，"作用于勾选"和"跳到第几页"混在一起读不通
    const wrapper = await mountView()

    // 分页器回归纯分页，不再承载操作
    expect(wrapper.find('.pager').findAll('button').map((b) => b.text())).not.toContain(
      '放入队列',
    )

    const pick = wrapper.find('.pick')
    const pager = wrapper.find('.pager')
    expect(pick.exists()).toBe(true)
    // 操作行在分页行之前
    expect(pick.element.compareDocumentPosition(pager.element)).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    )
    wrapper.unmount()
  })

  it('行内不再有单条操作按钮', async () => {
    // 改状态与删除统一走队列，列表里每行三个按钮既挤又占地方
    const wrapper = await mountView()

    expect(wrapper.find('tbody .actions').exists()).toBe(false)
    expect(wrapper.find('th.col-actions').exists()).toBe(false)
    wrapper.unmount()
  })
})

describe('内容搜索', () => {
  /*
    输入与"已生效的搜索词"是**两个变量**：查询只在回车或点搜索时才发出去。

    绑成同一个的话，中文输入法还没上屏就会触发查询 —— 拼音字母会当成搜索词发到
    后端；即便用英文，每敲一个字也查一次，"张三"会先按"张"白查一遍。
  */
  /** 搜索框本身带 `.filters__search`（它不再是某个容器的子元素） */
  const searchBox = (wrapper: ReturnType<typeof mount>) => wrapper.find('.filters__search')

  /** mock 打的是包装函数 `listEventSubmissions(eventId, filters)`，所以 filters 是第二个参数 */
  const getCalls = () =>
    listEventSubmissions.mock.calls as [string, Record<string, unknown>][]
  const lastFilters = () => getCalls()[getCalls().length - 1]?.[1] ?? {}
  const lastQuery = () => lastFilters().q

  it('默认不带搜索词', async () => {
    const wrapper = await mountView()

    expect(lastQuery()).toBeUndefined()
    wrapper.unmount()
  })

  it('打字时不发查询', async () => {
    const wrapper = await mountView()
    const before = listEventSubmissions.mock.calls.length

    await searchBox(wrapper).setValue('张三')
    await wrapper.vm.$nextTick()

    expect(listEventSubmissions.mock.calls.length).toBe(before)
    wrapper.unmount()
  })

  it('按回车才查', async () => {
    const wrapper = await mountView()

    await searchBox(wrapper).setValue('张三')
    await searchBox(wrapper).trigger('keyup.enter')

    await vi.waitFor(() => expect(lastQuery()).toBe('张三'))
    wrapper.unmount()
  })

  it('点搜索按钮也查', async () => {
    const wrapper = await mountView()

    await searchBox(wrapper).setValue('嵌入式')
    await wrapper.findAll('button').find((b) => b.text() === '搜索')!.trigger('click')

    await vi.waitFor(() => expect(lastQuery()).toBe('嵌入式'))
    wrapper.unmount()
  })

  it('搜索回到第一页', async () => {
    // 否则会停在一个新结果集里不存在的页码上
    const wrapper = await mountView({ total: 40 })

    await wrapper.findAll('button').find((b) => b.text() === '下一页')!.trigger('click')
    await vi.waitFor(() => expect(lastFilters().page).toBe(2))

    await searchBox(wrapper).setValue('张三')
    await searchBox(wrapper).trigger('keyup.enter')

    await vi.waitFor(() => expect(lastQuery()).toBe('张三'))
    expect(lastFilters().page).toBe(1)
    wrapper.unmount()
  })

  it('首尾空白会被去掉', async () => {
    const wrapper = await mountView()

    await searchBox(wrapper).setValue('  张三  ')
    await searchBox(wrapper).trigger('keyup.enter')

    await vi.waitFor(() => expect(lastQuery()).toBe('张三'))
    wrapper.unmount()
  })

  it('清除按钮只在搜索生效后出现，点了就恢复全部', async () => {
    const wrapper = await mountView()
    expect(wrapper.findAll('button').some((b) => b.text() === '清除')).toBe(false)

    await searchBox(wrapper).setValue('张三')
    await searchBox(wrapper).trigger('keyup.enter')
    await vi.waitFor(() => expect(lastQuery()).toBe('张三'))

    await wrapper.findAll('button').find((b) => b.text() === '清除')!.trigger('click')
    await vi.waitFor(() => expect(lastQuery()).toBeUndefined())

    // 输入框也一并清空，否则框里还留着词而结果已经是全部
    expect((searchBox(wrapper).element as HTMLInputElement).value).toBe('')
    wrapper.unmount()
  })

  it('搜索单独占一整行', async () => {
    // 它搜的是整份内容，与上面三个"按属性筛选"不是一回事
    const wrapper = await mountView()

    const rows = wrapper.findAll('.filters__row')
    expect(rows).toHaveLength(2)

    // 三个筛选在第一行，搜索在第二行
    expect(rows[0]!.findAll('.select')).toHaveLength(3)
    expect(rows[1]!.find('.filters__search').exists()).toBe(true)
    wrapper.unmount()
  })

  it('搜索框带 input 类，否则会回落到浏览器默认外观', async () => {
    /*
      输入框的外观规则是 `.field input, …, .input`。这一行的搜索框刻意不在 `.field`
      里（它是横向 flex 的一格），所以必须自己带上 `.input` —— 漏了的话控件会变成
      系统默认的白底，在暗色界面上格外刺眼。
    */
    const wrapper = await mountView()

    expect(searchBox(wrapper).classes()).toContain('input')
    wrapper.unmount()
  })

  it('搜索与其它筛选条件并存', async () => {
    const wrapper = await mountView()

    await searchBox(wrapper).setValue('张三')
    await searchBox(wrapper).trigger('keyup.enter')
    await vi.waitFor(() => expect(lastQuery()).toBe('张三'))

    // 搜索生效之后，后续的加载仍然带着它
    expect(lastFilters().q).toBe('张三')
    expect(lastFilters().event_id ?? getCalls()[0]![0]).toBe('spring-2026')
    wrapper.unmount()
  })
})

describe('点行看详情', () => {
  it('双击整行打开详情', async () => {
    const wrapper = await mountView()

    await wrapper.findAll('tbody tr')[1]!.trigger('dblclick')

    expect(dialog(wrapper).open).toBe(true)
    expect(wrapper.find('.detail-modal .modal__title').text()).toContain('#2')
    wrapper.unmount()
  })

  it('单击整行只选中，不打开详情', async () => {
    // 选中才是高频动作，详情要点两下
    const wrapper = await mountView()

    await wrapper.findAll('tbody tr')[1]!.trigger('click')
    await wrapper.vm.$nextTick()

    expect(dialog(wrapper).open).toBe(false)
    expect(wrapper.findAll('tbody tr')[1]!.classes()).toContain('row--selected')
    wrapper.unmount()
  })

  it('再单击一次取消选中', async () => {
    const wrapper = await mountView()
    const row = wrapper.findAll('tbody tr')[1]!

    await row.trigger('click')
    await wrapper.vm.$nextTick()
    await row.trigger('click')
    await wrapper.vm.$nextTick()

    expect(row.classes()).not.toContain('row--selected')
    wrapper.unmount()
  })

  it('勾选复选框不会弹出详情，也不会翻转整行的选中', async () => {
    // 多选时每勾一条就弹一个对话框，这页就没法用了；
    // 而且复选框自己已经改过状态，行点击不能再来一次
    const wrapper = await mountView()

    await wrapper.find('tbody .checkbox__box').trigger('pointerdown')
    await wrapper.vm.$nextTick()

    expect(dialog(wrapper).open).toBe(false)
    // 只选中了一条，说明行点击没有跟着翻转
    expect(wrapper.find('.pick__count').text()).toBe('已选 1 行')
    wrapper.unmount()
  })

  it('点批量操作按钮不会弹出详情', async () => {
    const wrapper = await mountView()

    const remove = wrapper.findAll('button').find((b) => b.text() === '删除')!
    // 这里不结算确认框，所以删除不会真的发生，只看详情对话框有没有被顺带打开
    await remove.trigger('click')
    await wrapper.vm.$nextTick()

    expect(dialog(wrapper).open).toBe(false)
    useConfirm().settle(false)
    wrapper.unmount()
  })

  it('点编号按钮打开详情', async () => {
    const wrapper = await mountView()

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

    await wrapper.find('tbody tr').trigger('dblclick')
    expect(wrapper.find('.detail__json').text()).toContain('secret')
    wrapper.unmount()
  })
})

describe('提交者那一列的截断', () => {
  it('标识走截断组件，而不是直接插值', async () => {
    // 匿名标识是 `a:<uuid>`，38 个字符，远超列宽。直接插值只会被硬裁，没有省略号
    const wrapper = await mountView()

    const cell = wrapper.find('.submitter')
    expect(cell.find('.cell').text()).toBe('a:browser-1')
    wrapper.unmount()
  })

  it('标签在截断元素之外，不会被一起裁掉', async () => {
    // "匿名"才是这一列真正要看的信息，跟着标识一起被截就本末倒置了
    const wrapper = await mountView()

    const cell = wrapper.find('.submitter')
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

    expect(wrapper.find('.submitter .cell').text()).toBe(
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

  it('flex 容器是 td 内层的 div，不是 td 本身', async () => {
    /*
      给 `<td>` 直接加 `display: flex` 会让它不再是 table-cell —— 行分隔线与列对齐
      跟着断掉，也就是"底行有断裂"那个现象。**这不会报任何错**，只能靠结构断言守住。
    */
    const wrapper = await mountView()

    const cell = wrapper.find('.submitter')
    expect(cell.element.tagName).toBe('DIV')
    expect(cell.element.parentElement?.tagName).toBe('TD')
    // 类名不能落在 td 上
    expect(wrapper.find('td.submitter').exists()).toBe(false)
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

describe('按住滑动多选', () => {
  /*
    状态机：按下 → armed（先不进入拖动）；**按住并离开起点** → dragging；滑过其它行
    → 刷成起点的值；松开 → 复位。

    区分"按下"与"滑起来"是关键：单击（按下与松开都在同一个复选框里）必须是普通的
    选中／取消，不能把列表带进拖动模式。
  */
  const boxes = (wrapper: ReturnType<typeof mount>) =>
    wrapper.findAll('tbody input[type="checkbox"]').map((node) => (node.element as HTMLInputElement).checked)

  const boxAt = (wrapper: ReturnType<typeof mount>, index: number) =>
    wrapper.findAll('tbody .checkbox')[index]!

  /** 在某个复选框上按下。打的是**可见的方块**，也就是用户真正点到的地方 */
  async function press(wrapper: ReturnType<typeof mount>, index: number) {
    await boxAt(wrapper, index).find('.checkbox__box').trigger('pointerdown')
    await wrapper.vm.$nextTick()
  }

  /** 按住状态下离开起点 —— 这一步之后才算真的开始滑 */
  async function leave(wrapper: ReturnType<typeof mount>, index: number) {
    await boxAt(wrapper, index).trigger('pointerleave')
    await wrapper.vm.$nextTick()
  }

  /** 滑过某一行的复选框 */
  async function slideOver(wrapper: ReturnType<typeof mount>, index: number) {
    await boxAt(wrapper, index).trigger('pointerenter')
    await wrapper.vm.$nextTick()
  }

  /** 按下 → 离开 → 滑过，完整的拖动动作 */
  async function dragFrom(wrapper: ReturnType<typeof mount>, from: number, to: number) {
    await press(wrapper, from)
    await leave(wrapper, from)
    await slideOver(wrapper, to)
  }

  async function release(wrapper: ReturnType<typeof mount>) {
    window.dispatchEvent(new Event('pointerup'))
    await wrapper.vm.$nextTick()
  }

  it('按下、离开起点、再滑过 —— 滑过的项跟着起点一起勾上', async () => {
    // 这是这个交互的全部意义：几十条时不必逐条点
    const wrapper = await mountView()

    await dragFrom(wrapper, 0, 1)

    expect(boxes(wrapper)).toEqual([true, true])
    wrapper.unmount()
  })

  it('按下但没离开起点，就只是普通的一次选中', async () => {
    const wrapper = await mountView()

    await press(wrapper, 0)
    await press(wrapper, 1)

    // 各自翻转一次，没有互相影响
    expect(boxes(wrapper)).toEqual([true, true])
    wrapper.unmount()
  })

  it('按下但没离开起点时，滑过别处不改动选择', async () => {
    /*
      这条正是"单击不能误触发拖动"。按下之后没有离开起点就滑到别处，在真实操作里
      意味着指针直接跳过去了 —— 不该把沿途的项刷成同一个值。
    */
    const wrapper = await mountView()

    await press(wrapper, 0)
    await slideOver(wrapper, 1)

    expect(boxes(wrapper)).toEqual([true, false])
    wrapper.unmount()
  })

  it('没按下时滑过不改动选择', async () => {
    // 否则鼠标扫过表格就会乱改选择
    const wrapper = await mountView()

    await slideOver(wrapper, 0)
    await slideOver(wrapper, 1)

    expect(boxes(wrapper)).toEqual([false, false])
    wrapper.unmount()
  })

  it('松开之后再滑过不再改动', async () => {
    const wrapper = await mountView()

    await press(wrapper, 0)
    await leave(wrapper, 0)
    await release(wrapper)
    await slideOver(wrapper, 1)

    expect(boxes(wrapper)).toEqual([true, false])
    wrapper.unmount()
  })

  it('从已勾选的一项开始拖，滑过的是取消', async () => {
    // 刷的是"按下之后那个值"，所以反向拖动要能取消
    const wrapper = await mountView()

    await dragFrom(wrapper, 0, 1) // 两条都勾上
    await dragFrom(wrapper, 0, 1) // 再从已勾选的第一项开始拖，这次是取消

    expect(boxes(wrapper)).toEqual([false, false])
    wrapper.unmount()
  })

  it('松开发生在表格之外也能结束拖动', async () => {
    // 指针经常是在别处松开的，只监听表格会漏掉
    const wrapper = await mountView()

    await press(wrapper, 0)
    await leave(wrapper, 0)
    window.dispatchEvent(new Event('pointerup'))
    await wrapper.vm.$nextTick()
    await slideOver(wrapper, 1)

    expect(boxes(wrapper)).toEqual([true, false])
    wrapper.unmount()
  })

  it('拖动期间一直按住，可以连续滑过多行', async () => {
    const wrapper = await mountView()

    await press(wrapper, 0)
    await leave(wrapper, 0)
    await slideOver(wrapper, 1)
    await slideOver(wrapper, 0)
    await slideOver(wrapper, 1)

    expect(boxes(wrapper)).toEqual([true, true])
    wrapper.unmount()
  })
})
