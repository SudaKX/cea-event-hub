/**
 * 活动页 SDK —— 暴露 `window.CEA`。
 *
 * **刻意编译成经典脚本（IIFE）而不是 ES module。** `<script type="module">` 受
 * CORS 限制，而活动页处于不透明源、对同主机也算跨源；经典 `<script src>` 不受
 * 该限制。对写单文件 HTML 的活动作者来说，少一个必须理解的概念。
 *
 * 活动页没有凭据、没有本地存储、不能直接 fetch `/api`（那是浏览器强制的边界）。
 * 它只能通过这里的方法请求宿主代办。
 *
 * ## 本文件不允许有**运行时导出**
 *
 * 产物靠 `window.CEA = api` 挂载，而不是靠打包器的 `lib.name`。一旦存在运行时
 * 导出，Rollup 就会为 IIFE 生成 `var CEA = <exports>`，在全局作用域把这个名字
 * **覆盖**掉 —— 模块内那句 `window.CEA = api` 白写了，活动页拿到的是一个空对象，
 * 表现为 `CEA.ready` 是 undefined、`await CEA.ready` 得到 undefined。
 *
 * 因此 `CeaRequestError` 等一律不加 `export`；`interface` / `type` 会被类型擦除，
 * 不产生运行时导出，可以放心导出。
 */

import {
  BRIDGE_ERROR,
  HOST_MESSAGE,
  IFRAME_MESSAGE,
  PROTOCOL_VERSION,
  isEnvelope,
  type BridgeErrorCode,
  type Envelope,
  type IdentityDescriptor,
} from '../protocol'

export interface CeaError {
  code: BridgeErrorCode
  message: string
  fields?: Record<string, string>
}

/** 有意不加 `export`：见文件头关于"运行时导出"的说明。 */
class CeaRequestError extends Error {
  readonly code: BridgeErrorCode
  readonly fields?: Record<string, string>

  constructor(error: CeaError) {
    super(error.message)
    this.name = 'CeaRequestError'
    this.code = error.code
    this.fields = error.fields
  }
}

export interface SubmitOptions {
  /** 提交内容。必须是 JSON 对象 —— 后端只做这一条结构约束 */
  payload?: Record<string, unknown>
  /** 要上传的文件。**带文件时 SDK 自动改走文件端点**，作者无需关心 */
  files?: File[]
  /** 可选分类标签，仅用于管理端分组 */
  kind?: string
  /** 幂等键：网络抖动导致重试时，服务端会返回原提交而不是新建一条 */
  idempotencyKey?: string
  /** 上传进度回调 */
  onProgress?: (loaded: number, total: number | null) => void
}

export interface CeaApi {
  /** 协议就绪后兑现；拿到身份描述符 */
  ready: Promise<IdentityDescriptor>
  identity(): IdentityDescriptor | null
  event(): Promise<Record<string, unknown>>
  me(): Promise<IdentityDescriptor>
  mySubmissions(): Promise<unknown>
  submit(options?: SubmitOptions): Promise<unknown>
  toast(message: string, level?: 'info' | 'error'): void
  navigate(to: string): void
  setTitle(title: string): void
  /** 内容高度变化时调用，让宿主调整 iframe 高度 */
  resize(): void
  draft: {
    save(value: unknown, formKey?: string): Promise<void>
    load<T = unknown>(formKey?: string): Promise<T | null>
    clear(formKey?: string): Promise<void>
  }
}

interface Pending {
  resolve: (value: unknown) => void
  reject: (error: CeaRequestError) => void
}

const READY_TIMEOUT_MS = 10_000
const DEFAULT_FORM_KEY = 'default'

class Bridge {
  private readonly pending = new Map<string, Pending>()
  private identityDescriptor: IdentityDescriptor | null = null
  private sequence = 0
  private readyResolve!: (identity: IdentityDescriptor) => void
  private readyReject!: (error: Error) => void
  private readySettled = false
  private readonly readyPromise: Promise<IdentityDescriptor>
  /** 宿主来源。不透明源下拿不到，因此只在能拿到时才校验。 */
  private readonly hostOrigin: string | null

  constructor() {
    this.readyPromise = new Promise((resolve, reject) => {
      this.readyResolve = resolve
      this.readyReject = reject
    })

    this.hostOrigin = deriveHostOrigin()

    window.addEventListener('message', this.handleMessage)
    window.addEventListener('load', () => this.announce())
    // 若脚本在 load 之后才被注入，上面的监听不会再触发
    if (document.readyState === 'complete') this.announce()

    setTimeout(() => {
      if (!this.readySettled) {
        this.readyReject(new Error('未能与宿主建立连接（是否在 iframe 中打开？）'))
      }
    }, READY_TIMEOUT_MS)
  }

  get ready(): Promise<IdentityDescriptor> {
    return this.readyPromise
  }

  get identity(): IdentityDescriptor | null {
    return this.identityDescriptor
  }

  private announce(): void {
    this.post(IFRAME_MESSAGE.READY, { protocolVersion: PROTOCOL_VERSION })
  }

  private handleMessage = (event: MessageEvent): void => {
    // 宿主是唯一的合法发送方。用**窗口引用**判定：不透明源下 origin 是 "null"，
    // 而引用不可伪造。
    if (event.source !== window.parent) return

    // 能拿到真实来源时再校验一次（同源场景下这是有效的额外约束）
    if (this.hostOrigin && event.origin && event.origin !== 'null') {
      if (event.origin !== this.hostOrigin) return
    }

    if (!isEnvelope(event.data)) return
    const message = event.data as Envelope

    switch (message.type) {
      case HOST_MESSAGE.INIT: {
        const payload = message.payload as { identity?: IdentityDescriptor } | undefined
        if (payload?.identity) {
          this.identityDescriptor = payload.identity
          if (!this.readySettled) {
            this.readySettled = true
            this.readyResolve(payload.identity)
          }
        }
        break
      }
      case HOST_MESSAGE.SESSION: {
        const payload = message.payload as { identity?: IdentityDescriptor } | undefined
        if (payload?.identity) this.identityDescriptor = payload.identity
        break
      }
      case HOST_MESSAGE.RESULT: {
        this.settle(message)
        break
      }
      default:
        break
    }
  }

  private settle(message: Envelope): void {
    if (!message.id) return
    const pending = this.pending.get(message.id)
    if (!pending) return
    this.pending.delete(message.id)

    const payload = message.payload as
      | { ok: boolean; data?: unknown; error?: CeaError }
      | undefined

    if (payload?.ok) {
      pending.resolve(payload.data)
    } else {
      pending.reject(
        new CeaRequestError(
          payload?.error ?? { code: BRIDGE_ERROR.UNKNOWN, message: '操作失败' },
        ),
      )
    }
  }

  async call(op: string, args: Record<string, unknown> = {}): Promise<unknown> {
    await this.readyPromise
    const id = `r${++this.sequence}`

    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject })
      this.post(IFRAME_MESSAGE.RPC, { op, args }, id)
    })
  }

  private post(type: string, payload: unknown, id?: string): void {
    const envelope: Envelope = { v: PROTOCOL_VERSION, type, payload }
    if (id) envelope.id = id
    // 与宿主对称：目标源无法用具体值匹配（不透明源），而这里也不发送任何凭据
    window.parent.postMessage(envelope, '*')
  }
}

/**
 * 推导宿主来源，用于能拿到时的额外校验。
 *
 * 不透明源的沙箱 iframe 拿不到 referrer，此时返回 null，只靠窗口引用判定 ——
 * 这已经是充分的：`window.parent` 不可伪造。
 */
function deriveHostOrigin(): string | null {
  try {
    if (document.referrer) return new URL(document.referrer).origin
    if (window.location.ancestorOrigins?.length) {
      return window.location.ancestorOrigins[0] ?? null
    }
  } catch {
    /* 拿不到就算了 */
  }
  return null
}

const bridge = new Bridge()

const api: CeaApi = {
  ready: bridge.ready,

  identity: () => bridge.identity,

  event: () => bridge.call('event.info') as Promise<Record<string, unknown>>,

  me: () => bridge.call('me.profile') as Promise<IdentityDescriptor>,

  mySubmissions: () => bridge.call('me.submissions'),

  async submit(options: SubmitOptions = {}) {
    const args: Record<string, unknown> = { payload: options.payload ?? {} }
    if (options.kind) args.kind = options.kind
    if (options.idempotencyKey) args.idempotencyKey = options.idempotencyKey
    if (options.files && options.files.length > 0) args.files = options.files

    // 带文件走文件端点（它是超集），不带文件走 JSON 端点。
    // 这个分叉是内部细节：作者视角永远是一次 submit 调用。
    const op = options.files && options.files.length > 0 ? 'form.submitFiles' : 'form.submit'

    const result = await bridge.call(op, args)
    options.onProgress?.(1, 1)
    return result
  },

  toast(message: string, level: 'info' | 'error' = 'info') {
    void bridge.call('event.info').catch(() => undefined) // 确保已就绪再发
    window.parent.postMessage(
      { v: PROTOCOL_VERSION, type: IFRAME_MESSAGE.TOAST, payload: { message, level } },
      '*',
    )
  },

  navigate(to: string) {
    window.parent.postMessage(
      { v: PROTOCOL_VERSION, type: IFRAME_MESSAGE.NAVIGATE, payload: { to } },
      '*',
    )
  },

  setTitle(title: string) {
    window.parent.postMessage(
      { v: PROTOCOL_VERSION, type: IFRAME_MESSAGE.TITLE, payload: { title } },
      '*',
    )
  },

  resize() {
    const height = Math.max(
      document.documentElement.scrollHeight,
      document.body?.scrollHeight ?? 0,
    )
    window.parent.postMessage(
      { v: PROTOCOL_VERSION, type: IFRAME_MESSAGE.RESIZE, payload: { height } },
      '*',
    )
  },

  draft: {
    async save(value: unknown, formKey = DEFAULT_FORM_KEY) {
      await bridge.call('draft.save', { formKey, value })
    },
    async load<T = unknown>(formKey = DEFAULT_FORM_KEY) {
      return (await bridge.call('draft.load', { formKey })) as T | null
    },
    async clear(formKey = DEFAULT_FORM_KEY) {
      await bridge.call('draft.clear', { formKey })
    },
  },
}

// 暴露全局对象。活动页只需 `<script src="/sdk/v1/cea.js"></script>`
// 然后 `CEA.submit(...)`。
//
// 刻意**不做 default export**：这是一个经典脚本，产物挂在 window 上，
// 导出对活动页没有任何意义，而且混用具名导出与默认导出会让打包器警告
// "consumers will have to use CEA.default"。
declare global {
  interface Window {
    CEA: CeaApi
  }
}

window.CEA = api
