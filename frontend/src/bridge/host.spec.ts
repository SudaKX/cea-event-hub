/**
 * 任务 13.2 - 13.10：宿主的来源校验、白名单分派、登录短路与清理。
 *
 * 这些是 C 方案的安全命门，因此逐个用测试钉死。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

// 打桩 API 层：这些测试关心的是"宿主有没有发出请求、发给谁"，
// 而不是后端行为（那由后端测试覆盖）
const getPublicEvent = vi.fn()
const submitApi = vi.fn()
const mySubmissions = vi.fn()

vi.mock('@/api/events', () => ({
  getPublicEvent: (...args: unknown[]) => getPublicEvent(...args),
}))
vi.mock('@/api/submissions', () => ({
  submit: (...args: unknown[]) => submitApi(...args),
  mySubmissions: (...args: unknown[]) => mySubmissions(...args),
}))

import { BridgeError, BridgeHost, TOAST_MAX_LENGTH, mapApiError } from './host'
import { BRIDGE_ERROR, BRIDGE_OP, HOST_MESSAGE, IFRAME_MESSAGE, PROTOCOL_VERSION } from './protocol'
import { ApiError } from '@/api/client'

/** 造一个带 contentWindow 的 iframe */
function makeIframe(): HTMLIFrameElement {
  const iframe = document.createElement('iframe')
  document.body.appendChild(iframe)
  return iframe
}

/** 派发一条来自指定窗口的消息。happy-dom 的 init 不收 source，手动定义。 */
function postFrom(source: unknown, data: unknown): void {
  const event = new MessageEvent('message', { data })
  Object.defineProperty(event, 'source', { value: source })
  window.dispatchEvent(event)
}

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

interface Harness {
  host: BridgeHost
  iframe: HTMLIFrameElement
  sent: Array<{ type: string; payload: unknown; id?: string }>
  onBridgeMissing: ReturnType<typeof vi.fn>
  onVersionMismatch: ReturnType<typeof vi.fn>
  onReady: ReturnType<typeof vi.fn>
  onNavigate: ReturnType<typeof vi.fn>
  onToast: ReturnType<typeof vi.fn>
}

function makeHost(options: { loggedIn?: boolean; requiresLogin?: boolean } = {}): Harness {
  const iframe = makeIframe()
  const sent: Harness['sent'] = []

  // 捕获宿主发出的消息
  const contentWindow = iframe.contentWindow as unknown as {
    postMessage: (message: unknown, target: string) => void
  }
  if (contentWindow) {
    contentWindow.postMessage = (message: unknown) => {
      sent.push(message as Harness['sent'][number])
    }
  }

  const onBridgeMissing = vi.fn()
  const onVersionMismatch = vi.fn()
  const onReady = vi.fn()
  const onNavigate = vi.fn()
  const onToast = vi.fn()

  const host = new BridgeHost({
    iframe,
    eventId: () => 'spring-2026',
    apiBase: '/api/v1',
    contentBase: '/content',
    identity: () => ({
      loggedIn: options.loggedIn ?? false,
      userId: options.loggedIn ? 7 : null,
      displayName: options.loggedIn ? 'Alice' : null,
      role: options.loggedIn ? 'user' : null,
      clientId: 'browser-abc',
      submissionRequiresLogin: options.requiresLogin ?? false,
    }),
    theme: () => ({ '--bg': '#0b0b0d' }),
    onBridgeMissing,
    onVersionMismatch,
    onReady,
    onNavigate,
    onToast,
    requestTimeoutMs: 50,
    readyTimeoutMs: 20,
  })

  return {
    host,
    iframe,
    sent,
    onBridgeMissing,
    onVersionMismatch,
    onReady,
    onNavigate,
    onToast,
  }
}

function rpc(host: Harness, op: string, args: Record<string, unknown> = {}, id = 'r1'): void {
  postFrom(host.iframe.contentWindow, {
    v: PROTOCOL_VERSION,
    type: IFRAME_MESSAGE.RPC,
    id,
    payload: { op, args },
  })
}

/** 等待微任务队列排空 */
const flush = () => new Promise((resolve) => setTimeout(resolve, 0))

describe('来源校验（任务 13.4）', () => {
  let harness: Harness

  beforeEach(() => {
    vi.clearAllMocks()
    harness = makeHost()
    harness.host.start()
  })

  it('忽略其他窗口发来的同格式消息', async () => {
    getPublicEvent.mockResolvedValue(EVENT)

    // 来自一个陌生窗口，而不是那个 iframe
    const stranger = makeIframe()
    postFrom(stranger.contentWindow, {
      v: PROTOCOL_VERSION,
      type: IFRAME_MESSAGE.RPC,
      id: 'r1',
      payload: { op: BRIDGE_OP.EVENT_INFO, args: {} },
    })
    await flush()

    // 没有握手、没有请求、没有回包
    expect(harness.host.isHandshaken).toBe(false)
    expect(getPublicEvent).not.toHaveBeenCalled()
    expect(harness.sent).toHaveLength(0)
  })

  it('忽略未知消息类型', async () => {
    postFrom(harness.iframe.contentWindow, {
      v: PROTOCOL_VERSION,
      type: 'event:something-new',
      payload: {},
    })
    await flush()
    expect(harness.sent).toHaveLength(0)
  })

  it('忽略形状不合法的消息', async () => {
    postFrom(harness.iframe.contentWindow, { type: 'event:ready' })
    await flush()
    expect(harness.host.isHandshaken).toBe(false)
  })
})

describe('握手（任务 13.2）', () => {
  let harness: Harness

  beforeEach(() => {
    vi.clearAllMocks()
    harness = makeHost({ loggedIn: true })
    harness.host.start()
  })

  it('收到就绪后下发初始化', () => {
    postFrom(harness.iframe.contentWindow, {
      v: PROTOCOL_VERSION,
      type: IFRAME_MESSAGE.READY,
      payload: { protocolVersion: PROTOCOL_VERSION },
    })

    expect(harness.host.isHandshaken).toBe(true)
    const init = harness.sent.find((message) => message.type === HOST_MESSAGE.INIT)
    expect(init).toBeDefined()
    expect(init?.payload).toMatchObject({ eventId: 'spring-2026' })
  })

  it('收到就绪后通知 onReady —— 宿主据此收起加载覆盖层', () => {
    expect(harness.onReady).not.toHaveBeenCalled()

    postFrom(harness.iframe.contentWindow, {
      v: PROTOCOL_VERSION,
      type: IFRAME_MESSAGE.READY,
      payload: { protocolVersion: PROTOCOL_VERSION },
    })

    expect(harness.onReady).toHaveBeenCalledTimes(1)
  })

  it('版本不匹配时不通知 onReady', () => {
    // 版本不兼容等于没连上，覆盖层不该收起
    postFrom(harness.iframe.contentWindow, {
      v: PROTOCOL_VERSION,
      type: IFRAME_MESSAGE.READY,
      payload: { protocolVersion: PROTOCOL_VERSION + 99 },
    })

    expect(harness.onVersionMismatch).toHaveBeenCalled()
    expect(harness.onReady).not.toHaveBeenCalled()
  })

  it('重复就绪是幂等的', () => {
    const ready = {
      v: PROTOCOL_VERSION,
      type: IFRAME_MESSAGE.READY,
      payload: { protocolVersion: PROTOCOL_VERSION },
    }
    postFrom(harness.iframe.contentWindow, ready)
    postFrom(harness.iframe.contentWindow, ready)

    const inits = harness.sent.filter((message) => message.type === HOST_MESSAGE.INIT)
    // 重复下发不产生副作用，但不会报错
    expect(inits.length).toBe(2)
    expect(harness.host.isHandshaken).toBe(true)
  })

  it('主版本不匹配时明确报错而不是继续', () => {
    postFrom(harness.iframe.contentWindow, {
      v: PROTOCOL_VERSION,
      type: IFRAME_MESSAGE.READY,
      payload: { protocolVersion: PROTOCOL_VERSION + 1 },
    })

    expect(harness.onVersionMismatch).toHaveBeenCalledWith(PROTOCOL_VERSION + 1)
    expect(harness.host.isHandshaken).toBe(false)
    expect(harness.sent).toHaveLength(0)
  })

  it('超时未就绪触发缺失诊断（任务 13.3）', async () => {
    await new Promise((resolve) => setTimeout(resolve, 40))
    expect(harness.onBridgeMissing).toHaveBeenCalled()
  })

  it('就绪后不再触发缺失诊断', async () => {
    postFrom(harness.iframe.contentWindow, {
      v: PROTOCOL_VERSION,
      type: IFRAME_MESSAGE.READY,
      payload: { protocolVersion: PROTOCOL_VERSION },
    })
    await new Promise((resolve) => setTimeout(resolve, 40))
    expect(harness.onBridgeMissing).not.toHaveBeenCalled()
  })
})

describe('身份描述符（任务 13.5）', () => {
  it('初始化载荷不含任何凭据', () => {
    vi.clearAllMocks()
    const harness = makeHost({ loggedIn: true })
    harness.host.start()
    postFrom(harness.iframe.contentWindow, {
      v: PROTOCOL_VERSION,
      type: IFRAME_MESSAGE.READY,
      payload: { protocolVersion: PROTOCOL_VERSION },
    })

    const init = harness.sent.find((message) => message.type === HOST_MESSAGE.INIT)
    const serialized = JSON.stringify(init?.payload)

    // 不该出现任何令牌字样或会话凭据字段
    expect(serialized).not.toMatch(/token|session|credential|password/i)
    expect(init?.payload).toMatchObject({
      identity: { loggedIn: true, userId: 7, clientId: 'browser-abc' },
    })
  })

  it('未登录时描述符仍带匿名标识与活动策略', () => {
    vi.clearAllMocks()
    const harness = makeHost({ loggedIn: false, requiresLogin: true })
    harness.host.start()
    postFrom(harness.iframe.contentWindow, {
      v: PROTOCOL_VERSION,
      type: IFRAME_MESSAGE.READY,
      payload: { protocolVersion: PROTOCOL_VERSION },
    })

    const init = harness.sent.find((message) => message.type === HOST_MESSAGE.INIT)
    expect(init?.payload).toMatchObject({
      identity: {
        loggedIn: false,
        userId: null,
        clientId: 'browser-abc',
        submissionRequiresLogin: true,
      },
    })
  })
})

describe('操作白名单（任务 13.6）', () => {
  let harness: Harness

  beforeEach(() => {
    vi.clearAllMocks()
    getPublicEvent.mockResolvedValue(EVENT)
    harness = makeHost({ loggedIn: true })
    harness.host.start()
    postFrom(harness.iframe.contentWindow, {
      v: PROTOCOL_VERSION,
      type: IFRAME_MESSAGE.READY,
      payload: { protocolVersion: PROTOCOL_VERSION },
    })
    harness.sent.length = 0
  })

  it('白名单外的操作被拒绝且不发出任何网络请求', async () => {
    rpc(harness, 'admin.users.list')
    await flush()

    expect(getPublicEvent).not.toHaveBeenCalled()
    expect(submitApi).not.toHaveBeenCalled()

    const result = harness.sent.find((message) => message.type === HOST_MESSAGE.RESULT)
    expect(result?.payload).toMatchObject({
      ok: false,
      error: { code: BRIDGE_ERROR.UNSUPPORTED },
    })
  })

  it('拒绝任意 URL 转发', async () => {
    rpc(harness, '/admin/users')
    await flush()
    expect(getPublicEvent).not.toHaveBeenCalled()
  })

  it('活动标识取自宿主路由，不采信活动页传值', async () => {
    rpc(harness, BRIDGE_OP.EVENT_INFO, { event_id: 'someone-elses-event' })
    await flush()

    // 宿主用自己的 eventId 发起请求
    expect(getPublicEvent).toHaveBeenCalledWith('spring-2026')
    expect(getPublicEvent).not.toHaveBeenCalledWith('someone-elses-event')
  })

  it('提交时同样忽略活动页传的活动标识', async () => {
    submitApi.mockResolvedValue({ submission: { id: 1 }, deduplicated: false })
    rpc(harness, BRIDGE_OP.FORM_SUBMIT, {
      eventId: 'other',
      event_id: 'other',
      payload: { n: 1 },
    })
    await flush()

    expect(submitApi).toHaveBeenCalledWith('spring-2026', expect.anything())
  })

  it('请求标识原样回传', async () => {
    rpc(harness, BRIDGE_OP.EVENT_INFO, {}, 'my-request-id')
    await flush()

    const result = harness.sent.find((message) => message.type === HOST_MESSAGE.RESULT)
    expect(result?.id).toBe('my-request-id')
  })

  it('无标识的 RPC 被忽略', async () => {
    postFrom(harness.iframe.contentWindow, {
      v: PROTOCOL_VERSION,
      type: IFRAME_MESSAGE.RPC,
      payload: { op: BRIDGE_OP.EVENT_INFO, args: {} },
    })
    await flush()
    expect(getPublicEvent).not.toHaveBeenCalled()
  })
})

describe('提交前登录短路（任务 13.8）', () => {
  it('要求登录且未登录时不下发请求', async () => {
    vi.clearAllMocks()
    getPublicEvent.mockResolvedValue({ ...EVENT, submission_requires_login: true })
    const harness = makeHost({ loggedIn: false, requiresLogin: true })
    harness.host.start()
    postFrom(harness.iframe.contentWindow, {
      v: PROTOCOL_VERSION,
      type: IFRAME_MESSAGE.READY,
      payload: { protocolVersion: PROTOCOL_VERSION },
    })
    harness.sent.length = 0

    rpc(harness, BRIDGE_OP.FORM_SUBMIT, { payload: { n: 1 } })
    await flush()

    expect(submitApi).not.toHaveBeenCalled()
    const result = harness.sent.find((message) => message.type === HOST_MESSAGE.RESULT)
    expect(result?.payload).toMatchObject({
      ok: false,
      error: { code: BRIDGE_ERROR.LOGIN_REQUIRED },
    })
  })

  it('已登录时正常提交', async () => {
    vi.clearAllMocks()
    getPublicEvent.mockResolvedValue({ ...EVENT, submission_requires_login: true })
    submitApi.mockResolvedValue({ submission: { id: 9 }, deduplicated: false })

    const harness = makeHost({ loggedIn: true, requiresLogin: true })
    harness.host.start()
    postFrom(harness.iframe.contentWindow, {
      v: PROTOCOL_VERSION,
      type: IFRAME_MESSAGE.READY,
      payload: { protocolVersion: PROTOCOL_VERSION },
    })
    harness.sent.length = 0

    rpc(harness, BRIDGE_OP.FORM_SUBMIT, { payload: { n: 1 } })
    await flush()

    expect(submitApi).toHaveBeenCalled()
    const result = harness.sent.find((message) => message.type === HOST_MESSAGE.RESULT)
    expect(result?.payload).toMatchObject({ ok: true })
  })
})

describe('超时与清理（任务 13.7）', () => {
  it('超时返回结构化错误而不是永久挂起', async () => {
    vi.clearAllMocks()
    getPublicEvent.mockImplementation(() => new Promise(() => {})) // 永不兑现

    const harness = makeHost()
    harness.host.start()
    postFrom(harness.iframe.contentWindow, {
      v: PROTOCOL_VERSION,
      type: IFRAME_MESSAGE.READY,
      payload: { protocolVersion: PROTOCOL_VERSION },
    })
    harness.sent.length = 0

    rpc(harness, BRIDGE_OP.EVENT_INFO)
    await new Promise((resolve) => setTimeout(resolve, 120))

    const result = harness.sent.find((message) => message.type === HOST_MESSAGE.RESULT)
    expect(result?.payload).toMatchObject({ ok: false, error: { code: BRIDGE_ERROR.TIMEOUT } })
  })

  it('iframe 重新加载后未决请求被清理', async () => {
    vi.clearAllMocks()
    getPublicEvent.mockImplementation(() => new Promise(() => {}))

    const harness = makeHost()
    harness.host.start()
    postFrom(harness.iframe.contentWindow, {
      v: PROTOCOL_VERSION,
      type: IFRAME_MESSAGE.READY,
      payload: { protocolVersion: PROTOCOL_VERSION },
    })

    rpc(harness, BRIDGE_OP.EVENT_INFO)
    await flush()
    expect(harness.host.pendingCount).toBe(1)

    harness.host.dispose()
    expect(harness.host.pendingCount).toBe(0)
  })

  it('活动页可主动取消请求', async () => {
    vi.clearAllMocks()
    getPublicEvent.mockImplementation(() => new Promise(() => {}))

    const harness = makeHost()
    harness.host.start()
    postFrom(harness.iframe.contentWindow, {
      v: PROTOCOL_VERSION,
      type: IFRAME_MESSAGE.READY,
      payload: { protocolVersion: PROTOCOL_VERSION },
    })
    harness.sent.length = 0

    rpc(harness, BRIDGE_OP.EVENT_INFO, {}, 'r-cancel')
    await flush()
    expect(harness.host.pendingCount).toBe(1)

    postFrom(harness.iframe.contentWindow, {
      v: PROTOCOL_VERSION,
      type: IFRAME_MESSAGE.CANCEL,
      id: 'r-cancel',
    })
    await flush()

    expect(harness.host.pendingCount).toBe(0)
    const result = harness.sent.find((message) => message.type === HOST_MESSAGE.RESULT)
    expect(result?.payload).toMatchObject({
      ok: false,
      error: { code: BRIDGE_ERROR.CANCELLED },
    })
  })
})

describe('本地储存', () => {
  let harness: Harness

  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
    harness = makeHost()
    harness.host.start()
  })

  const result = (id = 'r1') =>
    harness.sent.find((m) => m.type === HOST_MESSAGE.RESULT && m.id === id)

  it('写入的 localStorage key 由宿主拼成，活动无法指定完整键', async () => {
    rpc(harness, BRIDGE_OP.STORAGE_SAVE, { key: 'form', value: { a: 1 } })
    await flush()

    // 真正落盘的是宿主自己的命名空间，前缀里带活动标识
    const keys = Object.keys(window.localStorage)
    expect(keys).toEqual(['cea.storage:spring-2026:form'])
    // 活动给的那个 key 单独不是有效键，取不到东西
    expect(window.localStorage.getItem('form')).toBeNull()
  })

  it('活动拿不到别的活动的数据', async () => {
    window.localStorage.setItem('cea.storage:other-event:form', '{"secret":1}')

    rpc(harness, BRIDGE_OP.STORAGE_LOAD, { key: 'form' })
    await flush()

    // 本活动没有写过 form，应当是 null，而不是读到 other-event 的
    expect(result()?.payload).toMatchObject({ ok: true, data: null })
  })

  it('活动碰不到宿主自己的键', async () => {
    window.localStorage.setItem('cea.token', 'host-only')

    // 用尽各种想越界的写法，都应当落回自己的命名空间
    for (const evil of ['../cea.token', 'cea.token', ':spring-2026:x']) {
      rpc(harness, BRIDGE_OP.STORAGE_SAVE, { key: evil, value: 1 }, 'rx')
      await flush()
      expect(result('rx')?.payload).toMatchObject({
        ok: false,
        error: { code: BRIDGE_ERROR.STORAGE_KEY_INVALID },
      })
    }
    expect(window.localStorage.getItem('cea.token')).toBe('host-only')
  })

  it('key 不合规时报错而不是回落到默认槽位', async () => {
    // 回落会让 'Form' 和 'form' 之类撞在一起互相覆盖，是静默的数据损坏
    for (const bad of ['', 'Form', 'has space', 'a'.repeat(65), '-lead', 42, null]) {
      rpc(harness, BRIDGE_OP.STORAGE_SAVE, { key: bad, value: 1 }, 'rb')
      await flush()
      expect(result('rb')?.payload).toMatchObject({
        ok: false,
        error: { code: BRIDGE_ERROR.STORAGE_KEY_INVALID },
      })
    }
    expect(Object.keys(window.localStorage)).toEqual([])
  })

  it('存进去能原样读回来', async () => {
    const value = { name: '张三', nested: { list: [1, 2, 3] } }

    rpc(harness, BRIDGE_OP.STORAGE_SAVE, { key: 'form', value })
    await flush()
    rpc(harness, BRIDGE_OP.STORAGE_LOAD, { key: 'form' }, 'r2')
    await flush()

    expect(result('r2')?.payload).toMatchObject({ ok: true, data: value })
  })

  it('remove 只删掉那一条', async () => {
    rpc(harness, BRIDGE_OP.STORAGE_SAVE, { key: 'a', value: 1 })
    await flush()
    rpc(harness, BRIDGE_OP.STORAGE_SAVE, { key: 'b', value: 2 }, 'r2')
    await flush()
    rpc(harness, BRIDGE_OP.STORAGE_REMOVE, { key: 'a' }, 'r3')
    await flush()

    expect(Object.keys(window.localStorage)).toEqual(['cea.storage:spring-2026:b'])
  })

  it('clear 只清本活动的数据', async () => {
    rpc(harness, BRIDGE_OP.STORAGE_SAVE, { key: 'a', value: 1 })
    await flush()
    window.localStorage.setItem('cea.storage:other-event:a', '1')

    rpc(harness, BRIDGE_OP.STORAGE_CLEAR, {}, 'r2')
    await flush()

    expect(Object.keys(window.localStorage)).toEqual(['cea.storage:other-event:a'])
  })

  it('总量超上限时拒绝写入，而不是截断', async () => {
    // 4096 是**整个活动**的预算，不是单条的
    rpc(harness, BRIDGE_OP.STORAGE_SAVE, { key: 'a', value: 'x'.repeat(3000) })
    await flush()
    expect(result()?.payload).toMatchObject({ ok: true })

    // 再加 2000 会让总量超过 4096
    rpc(harness, BRIDGE_OP.STORAGE_SAVE, { key: 'b', value: 'y'.repeat(2000) }, 'r2')
    await flush()
    expect(result('r2')?.payload).toMatchObject({
      ok: false,
      error: { code: BRIDGE_ERROR.STORAGE_TOO_LARGE },
    })

    // 被拒的那条不该留下半个值
    expect(window.localStorage.getItem('cea.storage:spring-2026:b')).toBeNull()
  })

  it('覆盖同一个 key 不会把自己的旧值算进预算', async () => {
    rpc(harness, BRIDGE_OP.STORAGE_SAVE, { key: 'a', value: 'x'.repeat(4000) })
    await flush()

    // 同一条重写，预算应当按"替换"算，而不是"叠加"
    rpc(harness, BRIDGE_OP.STORAGE_SAVE, { key: 'a', value: 'x'.repeat(4000) }, 'r2')
    await flush()
    expect(result('r2')?.payload).toMatchObject({ ok: true })
  })

  it('无法序列化的值报 validation_failed', async () => {
    const circular: Record<string, unknown> = {}
    circular.self = circular

    rpc(harness, BRIDGE_OP.STORAGE_SAVE, { key: 'a', value: circular })
    await flush()

    expect(result()?.payload).toMatchObject({
      ok: false,
      error: { code: BRIDGE_ERROR.VALIDATION_FAILED },
    })
  })

  it('存的是 JSON 而不是字符串本身', async () => {
    rpc(harness, BRIDGE_OP.STORAGE_SAVE, { key: 'a', value: { n: 1 } })
    await flush()

    expect(window.localStorage.getItem('cea.storage:spring-2026:a')).toBe('{"n":1}')
  })
})

describe('活动页请求的其它消息', () => {
  it('忽略已废弃的 event:resize，而不是崩掉', () => {
    // 协议可能比宿主新，也可能有旧活动页还在发这条消息。
    // 全屏版面下它没有意义，但收到它必须无害。
    vi.clearAllMocks()
    const harness = makeHost()
    harness.host.start()

    expect(() =>
      postFrom(harness.iframe.contentWindow, {
        v: PROTOCOL_VERSION,
        type: 'event:resize',
        payload: { height: 1234 },
      }),
    ).not.toThrow()
  })

  it('只接受站内导航目标', () => {
    vi.clearAllMocks()
    const harness = makeHost()
    harness.host.start()

    postFrom(harness.iframe.contentWindow, {
      v: PROTOCOL_VERSION,
      type: IFRAME_MESSAGE.NAVIGATE,
      payload: { to: '/login' },
    })
    expect(harness.onNavigate).toHaveBeenCalledWith('/login')

    // 外部地址会被忽略：活动页不该把宿主导航出去
    postFrom(harness.iframe.contentWindow, {
      v: PROTOCOL_VERSION,
      type: IFRAME_MESSAGE.NAVIGATE,
      payload: { to: 'https://evil.example.com' },
    })
    expect(harness.onNavigate).toHaveBeenCalledTimes(1)
  })
})

describe('活动页的提示（CEA.toast）', () => {
  /*
    通知渲染在 iframe **之外**、宿主的界面里 —— 这是不受信内容唯一能写到宿主界面
    上的口子。下面几条把这个口子的边界钉住。
  */
  function fireToast(harness: Harness, payload: unknown): void {
    postFrom(harness.iframe.contentWindow, {
      v: PROTOCOL_VERSION,
      type: IFRAME_MESSAGE.TOAST,
      payload,
    })
  }

  function start(): Harness {
    vi.clearAllMocks()
    const harness = makeHost()
    harness.host.start()
    return harness
  }

  it('把活动页的提示转给宿主', () => {
    const harness = start()
    fireToast(harness, { level: 'ok', message: '提交成功' })

    expect(harness.onToast).toHaveBeenCalledWith({ level: 'ok', message: '提交成功' })
  })

  it('认不出来的语气当 info，而不是丢掉整条', () => {
    // 提示本身无害；为多写一个字就把整条丢掉，只会让作者摸不着头脑
    const harness = start()
    fireToast(harness, { level: 'success', message: '提交成功' })
    fireToast(harness, { message: '没有语气' })

    expect(harness.onToast).toHaveBeenNthCalledWith(1, { level: 'info', message: '提交成功' })
    expect(harness.onToast).toHaveBeenNthCalledWith(2, { level: 'info', message: '没有语气' })
  })

  it('超长正文被截断', () => {
    // 通知是浮层，几千字的提示会把整屏占满
    const harness = start()
    fireToast(harness, { level: 'info', message: 'あ'.repeat(5000) })

    const [payload] = harness.onToast.mock.calls[0]!
    expect((payload as { message: string }).message).toHaveLength(TOAST_MAX_LENGTH)
  })

  it('空正文与空白正文都不弹', () => {
    const harness = start()
    fireToast(harness, { level: 'info', message: '   ' })
    fireToast(harness, { level: 'info' })
    fireToast(harness, {})
    fireToast(harness, null)

    expect(harness.onToast).not.toHaveBeenCalled()
  })

  it('十秒内超过 5 条会被丢弃', () => {
    /*
      真正的影响不是"吵"，而是**宿主自己的提示被挤掉** —— 通知栈有显示上限，
      活动页刷屏会把"已保存"这类宿主提示顶出去。
    */
    const harness = start()
    for (let index = 1; index <= 8; index++) {
      fireToast(harness, { level: 'info', message: `第 ${index} 条` })
    }

    expect(harness.onToast).toHaveBeenCalledTimes(5)
    expect(harness.onToast).toHaveBeenLastCalledWith({ level: 'info', message: '第 5 条' })
  })

  it('过了窗口之后重新放行', () => {
    vi.useFakeTimers()
    try {
      const harness = start()
      for (let index = 1; index <= 5; index++) fireToast(harness, { message: `a${index}` })
      expect(harness.onToast).toHaveBeenCalledTimes(5)

      fireToast(harness, { message: '被挡下' })
      expect(harness.onToast).toHaveBeenCalledTimes(5)

      vi.advanceTimersByTime(10_001)
      fireToast(harness, { message: '又行了' })
      expect(harness.onToast).toHaveBeenCalledTimes(6)
    } finally {
      vi.useRealTimers()
    }
  })

  it('正文不是字符串时忽略', () => {
    // 活动页可以发任何东西过来，类型断言在这里不成立
    const harness = start()
    fireToast(harness, { level: 'info', message: { toString: () => 'x' } })
    fireToast(harness, { level: 'info', message: 42 })

    expect(harness.onToast).not.toHaveBeenCalled()
  })
})

describe('错误码映射（任务 13.9）', () => {
  it('把后端错误映射成活动页可区分的取值', () => {
    const cases: Array<[string, string]> = [
      ['login_required', BRIDGE_ERROR.LOGIN_REQUIRED],
      ['event_closed', BRIDGE_ERROR.EVENT_CLOSED],
      ['quota_exhausted', BRIDGE_ERROR.QUOTA_EXHAUSTED],
      // 个人满额与活动满额分成两个码：可采取的行动完全不同
      ['submitter_quota_exhausted', BRIDGE_ERROR.SUBMITTER_QUOTA_EXHAUSTED],
      ['rate_limited', BRIDGE_ERROR.RATE_LIMITED],
      ['validation_failed', BRIDGE_ERROR.VALIDATION_FAILED],
    ]

    for (const [backendCode, expected] of cases) {
      const mapped = mapApiError(new ApiError(backendCode, 'x', 400))
      expect(mapped.code).toBe(expected)
    }
  })

  it('保留字段级错误', () => {
    const mapped = mapApiError(
      new ApiError('validation_failed', '内容有误', 422, { contact: '格式不正确' }),
    )
    expect(mapped.fields).toEqual({ contact: '格式不正确' })
  })

  it('未知错误退化为通用码', () => {
    expect(mapApiError(new Error('boom')).code).toBe(BRIDGE_ERROR.UNKNOWN)
    expect(mapApiError(new ApiError('something_new', 'x', 500)).code).toBe(
      BRIDGE_ERROR.UNKNOWN,
    )
  })

  it('已经是 BridgeError 时原样返回', () => {
    const original = new BridgeError(BRIDGE_ERROR.TIMEOUT, '超时')
    expect(mapApiError(original)).toBe(original)
  })
})
