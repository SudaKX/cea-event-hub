/**
 * 调试台视图（add-develop-harness 任务 4.6 + 拦截改造）。
 *
 * 几件事只有把真实组件挂起来才测得到：
 *
 * 1. **来源被拒时界面里没有 iframe** —— 断言 `resolveDebugSource` 返回 null 是不够的，
 *    真正要防的是"校验函数拒绝了，但模板还是把 iframe 渲染了出来"。
 * 2. **默认什么都不拦** —— 不勾过滤时请求照常转发，行为与加这套东西之前一致。
 * 3. **挂起是真的挂起** —— 请求被拦下、内容看得见、由人来选假响应还是转发。
 * 4. **默认应答开着时不必逐条点** —— 而且两种默认动作的结果不同。
 *
 * ## happy-dom 下的一个退化，值得写清楚
 *
 * 带真实 `src` 的 iframe 在 happy-dom 里 **`contentWindow` 是 null**。于是宿主的来源
 * 校验（`event.source !== iframe.contentWindow`）在这里退化成 `null !== null`，恒为假。
 * 也就是说这一组用例**没有**在验证来源校验本身 —— 那条由 `bridge/host.spec.ts` 覆盖。
 * 这里验的是面板在收到消息之后的判断与渲染，那部分与 contentWindow 无关。
 *
 * 同理，宿主回给活动页的消息在这里是发不出去的（`contentWindow` 为 null），所以不能靠
 * "截获发出去的消息"来断言结算。改用**日志与队列**：面板在结算时会各写一条记录，而挂起
 * 队列本身就是可观察的状态。
 */
import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

const getPublicEvent = vi.fn()
const mySubmissions = vi.fn()
const submit = vi.fn()

vi.mock('@/api/events', () => ({
  getPublicEvent: (...args: unknown[]) => getPublicEvent(...args),
}))
vi.mock('@/api/submissions', () => ({
  mySubmissions: (...args: unknown[]) => mySubmissions(...args),
  submit: (...args: unknown[]) => submit(...args),
}))

import { BRIDGE_ERROR, HOST_MESSAGE, IFRAME_MESSAGE, PROTOCOL_VERSION } from '@/bridge/protocol'
import { useToast } from '@/composables/useToast'
import DevelopView from './DevelopView.vue'

const SRC = '/draft/demo/index.html'

type Wrapper = ReturnType<typeof mount>

/**
 * 挂载过的组件，测试结束一律卸载。
 *
 * **不卸载会让宿主泄漏到后面的用例里**：每个宿主都在 window 上注册了 message 监听，
 * 而这里又是靠 `window.dispatchEvent` 造消息的 —— 于是前面用例留下的宿主也会收到、
 * 也会去调接口。表现是"我只发了一次请求，接口却被调了四次"，而且次数随用例顺序变化，
 * 极难看出是测试自身的问题。
 */
const mounted: Wrapper[] = []

afterEach(() => {
  for (const wrapper of mounted.splice(0)) wrapper.unmount()
})

async function mountAt(query: Record<string, string>) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/develop', name: 'develop', component: { template: '<div />' } },
      { path: '/:pathMatch(.*)*', component: { template: '<div />' } },
    ],
  })
  await router.push({ path: '/develop', query })
  await router.isReady()

  const wrapper = mount(DevelopView, { global: { plugins: [router] } })
  mounted.push(wrapper)
  // 宿主是在 onMounted 的 nextTick 里建的；不等它，消息就没有接收方
  await wrapper.vm.$nextTick()
  await wrapper.vm.$nextTick()
  return wrapper
}

async function openTab(wrapper: Wrapper, label: string) {
  const button = wrapper.findAll('.rail__btn').find((item) => item.text().includes(label))
  if (!button) throw new Error(`工具轨上没有「${label}」`)
  await button.trigger('click')
}

/** 造一条"活动页发来"的请求。 */
function rpcFromPage(id: string, op: string, args: Record<string, unknown> = {}) {
  return new MessageEvent('message', {
    data: {
      v: PROTOCOL_VERSION,
      type: IFRAME_MESSAGE.RPC,
      id,
      payload: { op, args },
    },
  })
}

function logLines(wrapper: Wrapper): string[] {
  return wrapper.findAll('.log li').map((li) => li.text())
}

/**
 * 冲干净微任务链。
 *
 * 转发不是一步到位的：`forwardHeld` → `runRequest` → `dispatch`，而 `dispatch` 在真正
 * 发请求之前还要 `await loadEvent()`（提交类操作的"提交前登录短路"判据）。只 `$nextTick`
 * 一次会在那一步之前就断言，于是"接口没被调到"——看起来像功能坏了，其实是测试太快。
 */
async function flush(wrapper: Wrapper): Promise<void> {
  for (let i = 0; i < 4; i += 1) {
    await new Promise((resolve) => setTimeout(resolve, 0))
    await wrapper.vm.$nextTick()
  }
}

/** 过滤清单里某个操作那一行的勾选框。 */
function filterBox(wrapper: Wrapper, op: string) {
  const row = wrapper.findAll('.ops li').find((li) => li.text().includes(op))
  if (!row) throw new Error(`过滤清单里没有 ${op}`)
  return row.find('input[type="checkbox"]')
}

/** 挂起队列里的条目。 */
function heldItems(wrapper: Wrapper) {
  return wrapper.findAll('.held li')
}

/**
 * 挂起队列里的第一条。
 *
 * 空队列时**抛**而不是返回 `undefined`：失败信息会直接说"没有挂起"，而不是一个
 * 解引用 `undefined` 的 TypeError。类型上也不需要 `!` 断言 —— 那个断言会掩盖真正的问题。
 */
function firstHeld(wrapper: Wrapper) {
  const item = heldItems(wrapper)[0]
  if (!item) throw new Error('挂起队列是空的')
  return item
}

/** 挂起条目上的第 n 个结算按钮（0 = 假响应，1 = 转发）。 */
function heldButton(wrapper: Wrapper, index: number) {
  const button = firstHeld(wrapper).findAll('button')[index]
  if (!button) throw new Error(`挂起的条目上没有第 ${index + 1} 个按钮`)
  return button
}

beforeEach(() => {
  vi.clearAllMocks()
  getPublicEvent.mockResolvedValue({ id: 'dev', title: '调试活动', quota: {} })
  useToast().clear()
})

describe('来源限制', () => {
  it('给了合规来源时渲染出 iframe，并绑定沙箱与 src', async () => {
    const wrapper = await mountAt({ src: SRC })

    const iframe = wrapper.find('iframe')
    expect(iframe.exists()).toBe(true)
    expect(iframe.attributes('src')).toBe(SRC)

    const sandbox = iframe.attributes('sandbox') ?? ''
    expect(sandbox).toContain('allow-scripts')
    // 与生产宿主同一条不可动摇的约束
    expect(sandbox).not.toContain('allow-same-origin')
  })

  it('来源是站外地址时**不渲染 iframe**，只显示拒绝原因', async () => {
    const wrapper = await mountAt({ src: 'https://evil.example/draft/x/index.html' })

    expect(wrapper.find('iframe').exists()).toBe(false)
    expect(wrapper.text()).toContain('不接受这个来源')
    expect(wrapper.text()).toContain('https://evil.example/draft/x/index.html')
  })

  it('协议相对地址（//host）同样被拒 —— 它看起来像站内路径', async () => {
    const wrapper = await mountAt({ src: '//evil.example/draft/x/index.html' })

    expect(wrapper.find('iframe').exists()).toBe(false)
  })

  it('没给 src 时说明该怎么给', async () => {
    const wrapper = await mountAt({})

    expect(wrapper.find('iframe').exists()).toBe(false)
    expect(wrapper.text()).toContain('?src=')
  })

  it('活动标识缺省为开发活动，且可由查询参数覆盖', async () => {
    const fallback = await mountAt({ src: SRC })
    expect(fallback.text()).toContain('dev')

    const overridden = await mountAt({ src: SRC, event: '2026autumn' })
    expect(overridden.text()).toContain('2026autumn')
  })
})

describe('默认什么都不拦', () => {
  it('没勾任何过滤时，请求照常放行', async () => {
    const wrapper = await mountAt({ src: SRC })

    window.dispatchEvent(rpcFromPage('r1', 'form.submit', { payload: { a: 1 } }))
    await wrapper.vm.$nextTick()

    await openTab(wrapper, '拦截')
    expect(heldItems(wrapper)).toHaveLength(0)
  })

  it('过滤清单里的勾选框默认全不勾选', async () => {
    const wrapper = await mountAt({ src: SRC })
    await openTab(wrapper, '拦截')

    const boxes = wrapper.findAll('.ops input[type="checkbox"]')
    expect(boxes.length).toBeGreaterThan(0)
    for (const box of boxes) {
      expect((box.element as HTMLInputElement).checked).toBe(false)
    }
  })

  it('默认应答默认是关的', async () => {
    const wrapper = await mountAt({ src: SRC })
    await openTab(wrapper, '拦截')

    const boxes = wrapper.findAll('.block input[type="checkbox"]')
    const autoRespond = boxes.find((box) => box.element.closest('label')?.textContent?.includes('不必逐条'))
    expect((autoRespond?.element as HTMLInputElement | undefined)?.checked).toBe(false)
  })
})

describe('挂起与观察', () => {
  it('勾中且没开默认应答时，请求被挂起而不是发出去', async () => {
    const wrapper = await mountAt({ src: SRC })
    await openTab(wrapper, '拦截')
    await filterBox(wrapper, 'form.submit').setValue(true)

    window.dispatchEvent(rpcFromPage('r2', 'form.submit', { payload: { name: '张三' } }))
    await flush(wrapper)

    const items = heldItems(wrapper)
    expect(items).toHaveLength(1)
    // 没有转发，所以后端一次都没被调到。
    // **必须先 flush 再断言"没被调用"**：转发是异步的（要先 await loadEvent），
    // 只等一个 tick 的话，一个"偷偷转发"的实现也能让这条断言通过。
    expect(submit).not.toHaveBeenCalled()
    expect(getPublicEvent).not.toHaveBeenCalled()
  })

  it('挂起的请求能看到它的内容', async () => {
    const wrapper = await mountAt({ src: SRC })
    await openTab(wrapper, '拦截')
    await filterBox(wrapper, 'form.submit').setValue(true)

    window.dispatchEvent(
      rpcFromPage('r3', 'form.submit', { payload: { name: '李四', grade: '大二' } }),
    )
    await wrapper.vm.$nextTick()

    const item = firstHeld(wrapper)
    expect(item.text()).toContain('form.submit')
    expect(item.text()).toContain('r3')
    expect(item.text()).toContain('李四')
    expect(item.text()).toContain('大二')
  })

  it('未勾中的操作即使在挂起状态下也照常放行', async () => {
    const wrapper = await mountAt({ src: SRC })
    await openTab(wrapper, '拦截')
    await filterBox(wrapper, 'form.submit').setValue(true)

    window.dispatchEvent(rpcFromPage('r4', 'storage.save', { key: 'k', value: 1 }))
    await wrapper.vm.$nextTick()

    expect(heldItems(wrapper)).toHaveLength(0)
  })
})

describe('结算挂起的请求', () => {
  it('假响应：不发往后端，日志标明是伪造的', async () => {
    const wrapper = await mountAt({ src: SRC })
    await openTab(wrapper, '拦截')
    await filterBox(wrapper, 'form.submit').setValue(true)

    window.dispatchEvent(rpcFromPage('r5', 'form.submit', {}))
    await wrapper.vm.$nextTick()

    await heldButton(wrapper, 0).trigger('click')
    // 同样是"先 flush 再断言没被调用"——否则偷偷转发的实现照样能过
    await flush(wrapper)

    expect(heldItems(wrapper)).toHaveLength(0)
    expect(submit).not.toHaveBeenCalled()

    await openTab(wrapper, '日志')
    const forged = logLines(wrapper).filter((line) => line.includes('假响应'))
    expect(forged).toHaveLength(1)
    expect(forged[0]).toContain(BRIDGE_ERROR.TIMEOUT)
    expect(forged[0]).toContain('未发往后端')
  })

  it('转发：请求真的发往后端', async () => {
    submit.mockResolvedValue({ submission: { id: 1 }, deduplicated: false })

    const wrapper = await mountAt({ src: SRC })
    await openTab(wrapper, '拦截')
    await filterBox(wrapper, 'form.submit').setValue(true)

    window.dispatchEvent(rpcFromPage('r6', 'form.submit', { payload: { a: 1 } }))
    await wrapper.vm.$nextTick()

    // 第二个按钮是「转发」
    await heldButton(wrapper, 1).trigger('click')
    await flush(wrapper)

    expect(heldItems(wrapper)).toHaveLength(0)
    expect(submit).toHaveBeenCalledTimes(1)

    await openTab(wrapper, '日志')
    expect(logLines(wrapper).some((line) => line.includes('转发'))).toBe(true)
  })
})

describe('默认应答', () => {
  async function enableAutoRespond(wrapper: Wrapper, action: 'fail' | 'forward') {
    await openTab(wrapper, '拦截')
    const block = wrapper.findAll('.block').find((b) => b.text().includes('默认应答'))
    if (!block) throw new Error('没有「默认应答」区块')
    await block.find('input[type="checkbox"]').setValue(true)
    await block.find('select').setValue(action)
  }

  it('开启后假响应不必逐条点，且不进队列', async () => {
    const wrapper = await mountAt({ src: SRC })
    await enableAutoRespond(wrapper, 'fail')
    await filterBox(wrapper, 'form.submit').setValue(true)

    window.dispatchEvent(rpcFromPage('r7', 'form.submit', {}))
    await flush(wrapper)

    expect(heldItems(wrapper)).toHaveLength(0)
    expect(submit).not.toHaveBeenCalled()

    await openTab(wrapper, '日志')
    expect(logLines(wrapper).some((line) => line.includes('默认应答：假响应'))).toBe(true)
  })

  it('开启后转发同样不必逐条点', async () => {
    submit.mockResolvedValue({ submission: { id: 2 }, deduplicated: false })

    const wrapper = await mountAt({ src: SRC })
    await enableAutoRespond(wrapper, 'forward')
    await filterBox(wrapper, 'form.submit').setValue(true)

    window.dispatchEvent(rpcFromPage('r8', 'form.submit', { payload: {} }))
    await flush(wrapper)

    expect(heldItems(wrapper)).toHaveLength(0)
    expect(submit).toHaveBeenCalledTimes(1)

    await openTab(wrapper, '日志')
    expect(logLines(wrapper).some((line) => line.includes('默认应答：转发'))).toBe(true)
  })
})

describe('工具轨分页', () => {
  it('五页都在轨上，且默认停在状态页', async () => {
    const wrapper = await mountAt({ src: SRC })

    const labels = wrapper.findAll('.rail__btn').map((b) => b.text())
    for (const label of ['状态', '身份', '动作', '拦截', '日志']) {
      expect(labels.some((text) => text.includes(label)), `轨上没有「${label}」`).toBe(true)
    }
    expect(wrapper.find('.kv').exists()).toBe(true)
  })

  it('切换分页只显示该页内容', async () => {
    const wrapper = await mountAt({ src: SRC })

    await openTab(wrapper, '动作')
    expect(wrapper.find('.actions').exists()).toBe(true)
    expect(wrapper.find('.kv').exists()).toBe(false)

    await openTab(wrapper, '日志')
    expect(wrapper.find('.log').exists()).toBe(true)
    expect(wrapper.find('.actions').exists()).toBe(false)
  })

  it('挂起数在工具轨上显示角标（漏掉会让活动页一直等着）', async () => {
    const wrapper = await mountAt({ src: SRC })
    await openTab(wrapper, '拦截')
    await filterBox(wrapper, 'form.submit').setValue(true)

    window.dispatchEvent(rpcFromPage('r9', 'form.submit', {}))
    await wrapper.vm.$nextTick()

    expect(wrapper.find('.rail__badge').text()).toBe('1')
  })

  it('动作页列出协议里的全部宿主消息类型', async () => {
    const wrapper = await mountAt({ src: SRC })
    await openTab(wrapper, '动作')

    const text = wrapper.text()
    for (const type of Object.values(HOST_MESSAGE)) {
      expect(text, `${type} 没出现在面板上`).toContain(type)
    }
  })
})
