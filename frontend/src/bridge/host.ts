/**
 * 宿主侧的桥接实现。
 *
 * 活动页零凭证：它只能通过 postMessage 请求**预定义操作**，由宿主用自身会话代为
 * 调用后端。这个模块是整个方案的安全命门，三条规则不可动摇：
 *
 * 1. **op 白名单，不是 URL 转发。** 如果按活动页给的地址转发，就等于用宿主会话
 *    造了一个开放代理，活动页可以要求宿主去调管理接口 —— 安全收益归零。
 * 2. **活动标识只从宿主自身路由取**，绝不采信活动页传值。
 * 3. **来源用 `event.source === iframe.contentWindow` 校验**，不能用
 *    `event.origin` —— 活动页处于不透明源，其 origin 是字符串 "null"。
 */

import { getPublicEvent } from '@/api/events'
import { submit as submitApi, mySubmissions } from '@/api/submissions'
import { ApiError } from '@/api/client'
import {
  BRIDGE_ERROR,
  BRIDGE_OP,
  HOST_MESSAGE,
  IFRAME_MESSAGE,
  PROTOCOL_VERSION,
  isCompatible,
  isEnvelope,
  type BridgeErrorCode,
  type Envelope,
  type IdentityDescriptor,
  type InitPayload,
  type RpcRequestPayload,
  type RpcResultPayload,
} from './protocol'
import type { EventPublic } from '@/types/api'

export interface BridgeHostOptions {
  iframe: HTMLIFrameElement
  /** 从**宿主路由**取活动标识；这里用回调而不是值，避免活动切换后失效 */
  eventId: () => string
  apiBase: string
  contentBase: string
  identity: () => IdentityDescriptor
  theme: () => Record<string, string>
  onNavigate?: (to: string) => void
  onToast?: (payload: { level?: string; message: string }) => void
  onTitle?: (title: string) => void
  onResize?: (height: number) => void
  /** 约定时间内没收到就绪消息时触发，用于显示"缺少桥接脚本"诊断 */
  onBridgeMissing?: () => void
  onVersionMismatch?: (declared: number) => void
  /** 请求超时；上传类请求由调用方另行放宽 */
  requestTimeoutMs?: number
  /** 等待活动页就绪的时间 */
  readyTimeoutMs?: number
}

interface PendingRequest {
  resolve: (value: unknown) => void
  reject: (reason: unknown) => void
  timer: ReturnType<typeof setTimeout>
  controller: AbortController
}

/** 活动页拿到的结构化错误。活动页不需要解析 HTTP 状态码。 */
export class BridgeError extends Error {
  readonly code: BridgeErrorCode
  readonly fields?: Record<string, string>

  constructor(code: BridgeErrorCode, message: string, fields?: Record<string, string>) {
    super(message)
    this.name = 'BridgeError'
    this.code = code
    this.fields = fields
  }
}

/** 后端错误码 -> 桥接错误码。两者刻意保持同名，避免多一层心智映射。 */
const ERROR_CODE_MAP: Record<string, BridgeErrorCode> = {
  login_required: BRIDGE_ERROR.LOGIN_REQUIRED,
  event_closed: BRIDGE_ERROR.EVENT_CLOSED,
  quota_exhausted: BRIDGE_ERROR.QUOTA_EXHAUSTED,
  rate_limited: BRIDGE_ERROR.RATE_LIMITED,
  validation_failed: BRIDGE_ERROR.VALIDATION_FAILED,
  not_found: BRIDGE_ERROR.NOT_FOUND,
  payload_too_large: BRIDGE_ERROR.PAYLOAD_TOO_LARGE,
}

export function mapApiError(error: unknown): BridgeError {
  if (error instanceof BridgeError) return error

  if (error instanceof ApiError) {
    const code = ERROR_CODE_MAP[error.code] ?? BRIDGE_ERROR.UNKNOWN
    return new BridgeError(code, error.message, error.fields)
  }

  if (error instanceof Error && error.name === 'AbortError') {
    return new BridgeError(BRIDGE_ERROR.CANCELLED, '请求已取消')
  }

  return new BridgeError(BRIDGE_ERROR.UNKNOWN, '操作失败，请稍后重试')
}

export class BridgeHost {
  private readonly options: BridgeHostOptions
  private readonly pending = new Map<string, PendingRequest>()
  private readyTimer: ReturnType<typeof setTimeout> | null = null
  private handshakeDone = false
  private eventCache: EventPublic | null = null
  private eventPromise: Promise<EventPublic> | null = null
  private disposed = false
  private sequence = 0

  constructor(options: BridgeHostOptions) {
    this.options = options
    window.addEventListener('message', this.handleMessage)
  }

  /** 挂到 iframe 上，开始等待活动页就绪 */
  start(): void {
    this.readyTimer = setTimeout(() => {
      if (!this.handshakeDone) this.options.onBridgeMissing?.()
    }, this.options.readyTimeoutMs ?? 5000)
  }

  dispose(): void {
    this.disposed = true
    window.removeEventListener('message', this.handleMessage)
    if (this.readyTimer) clearTimeout(this.readyTimer)
    // iframe 被卸载时，未决请求必须被拒绝，否则活动页会永远挂起
    this.rejectAllPending(new BridgeError(BRIDGE_ERROR.CANCELLED, '活动页已重新加载'))
  }

  /** iframe 重新加载后重新握手（幂等） */
  onIframeLoad(): void {
    this.handshakeDone = false
    this.start()
  }

  /** 登录态变化时主动下发更新 */
  pushSession(): void {
    if (!this.handshakeDone) return
    this.post(HOST_MESSAGE.SESSION, { identity: this.options.identity() })
  }

  // ------------------------------------------------------------------
  // 收消息
  // ------------------------------------------------------------------

  private handleMessage = (event: MessageEvent): void => {
    // 关键：比对**窗口引用**而不是 origin 字符串。
    // 不透明源的 iframe 发来的消息 origin 恒为 "null"，拿它鉴权毫无意义；
    // 而窗口引用不可伪造，其他窗口发来的同格式消息会被这一步挡掉。
    if (this.disposed) return
    if (event.source !== this.options.iframe.contentWindow) return

    if (!isEnvelope(event.data)) return
    const message = event.data as Envelope

    switch (message.type) {
      case IFRAME_MESSAGE.READY:
        this.handleReady(message)
        break
      case IFRAME_MESSAGE.RESIZE:
        this.handleResize(message)
        break
      case IFRAME_MESSAGE.NAVIGATE:
        this.handleNavigate(message)
        break
      case IFRAME_MESSAGE.TITLE:
        this.handleTitle(message)
        break
      case IFRAME_MESSAGE.TOAST:
        this.handleToast(message)
        break
      case IFRAME_MESSAGE.RPC:
        void this.handleRpc(message)
        break
      case IFRAME_MESSAGE.CANCEL:
        this.handleCancel(message)
        break
      default:
        // 未知类型静默忽略：协议可能比宿主新，不该因此中断
        break
    }
  }

  private handleReady(message: Envelope): void {
    const declared = message.payload as { protocolVersion?: number } | undefined

    if (declared?.protocolVersion !== undefined && !isCompatible(declared.protocolVersion)) {
      // 主版本不一致时明确失败，而不是带着不兼容的假设继续
      this.options.onVersionMismatch?.(declared.protocolVersion)
      return
    }

    if (this.readyTimer) {
      clearTimeout(this.readyTimer)
      this.readyTimer = null
    }
    this.handshakeDone = true
    // 幂等：重复就绪或 iframe 重载后重复下发都不会产生副作用
    this.sendInit()
  }

  private handleResize(message: Envelope): void {
    const height = (message.payload as { height?: number } | undefined)?.height
    if (typeof height === 'number' && height > 0) this.options.onResize?.(height)
  }

  private handleNavigate(message: Envelope): void {
    const to = (message.payload as { to?: string } | undefined)?.to
    // 只接受站内路径：活动页不该把宿主导航到外部地址
    if (typeof to === 'string' && to.startsWith('/')) this.options.onNavigate?.(to)
  }

  private handleTitle(message: Envelope): void {
    const title = (message.payload as { title?: string } | undefined)?.title
    if (typeof title === 'string' && title) this.options.onTitle?.(title)
  }

  private handleToast(message: Envelope): void {
    const payload = message.payload as { level?: string; message?: string } | undefined
    if (payload?.message) this.options.onToast?.({ level: payload.level, message: payload.message })
  }

  private handleCancel(message: Envelope): void {
    if (!message.id) return
    const pending = this.pending.get(message.id)
    if (pending) {
      pending.controller.abort()
      this.settle(message.id, false, new BridgeError(BRIDGE_ERROR.CANCELLED, '请求已取消'))
    }
  }

  // ------------------------------------------------------------------
  // RPC 分派
  // ------------------------------------------------------------------

  private async handleRpc(message: Envelope): Promise<void> {
    const requestId = message.id
    if (!requestId) return // 没有标识就无法回传结果，忽略

    const payload = message.payload as RpcRequestPayload | undefined
    const op = payload?.op
    const args = payload?.args ?? {}

    if (typeof op !== 'string' || !this.isAllowed(op)) {
      // 白名单外一律拒绝，且**不发出任何网络请求**
      this.postResult(requestId, {
        ok: false,
        error: { code: BRIDGE_ERROR.UNSUPPORTED, message: `不支持的操作：${String(op)}` },
      })
      return
    }

    const controller = new AbortController()
    const timeout = this.options.requestTimeoutMs ?? 30_000

    try {
      const data = await this.withTimeout(
        requestId,
        controller,
        timeout,
        this.dispatch(op, args, requestId, controller),
      )
      this.postResult(requestId, { ok: true, data })
    } catch (error) {
      const mapped = mapApiError(error)
      this.postResult(requestId, {
        ok: false,
        error: { code: mapped.code, message: mapped.message, fields: mapped.fields },
      })
    } finally {
      const pending = this.pending.get(requestId)
      if (pending) clearTimeout(pending.timer)
      this.pending.delete(requestId)
    }
  }

  private isAllowed(op: string): boolean {
    return Object.values(BRIDGE_OP).includes(op as never)
  }

  private async dispatch(
    op: string,
    args: Record<string, unknown>,
    requestId: string,
    controller: AbortController,
  ): Promise<unknown> {
    // 注意：**活动标识来自宿主自身**，args 里即便带了 event_id 也会被忽略
    const eventId = this.options.eventId()

    switch (op) {
      case BRIDGE_OP.EVENT_INFO:
        return this.loadEvent()

      case BRIDGE_OP.ME_PROFILE:
        return this.options.identity()

      case BRIDGE_OP.ME_SUBMISSIONS:
        return mySubmissions(eventId)

      case BRIDGE_OP.DRAFT_SAVE: {
        draftStore.save(eventId, asFormKey(args.formKey), args.value)
        return { ok: true }
      }

      case BRIDGE_OP.DRAFT_LOAD:
        return draftStore.load(eventId, asFormKey(args.formKey))

      case BRIDGE_OP.DRAFT_CLEAR:
        draftStore.clear(eventId, asFormKey(args.formKey))
        return { ok: true }

      case BRIDGE_OP.FORM_SUBMIT:
      case BRIDGE_OP.FORM_SUBMIT_FILES: {
        // 提交前短路：活动要求登录而当前未登录时，**不发出网络请求**
        const event = await this.loadEvent()
        if (event.submission_requires_login && !this.options.identity().loggedIn) {
          throw new BridgeError(BRIDGE_ERROR.LOGIN_REQUIRED, '请先登录后再提交')
        }

        const files = op === BRIDGE_OP.FORM_SUBMIT_FILES ? asFileList(args.files) : []
        const payload = (args.payload as Record<string, unknown>) ?? {}

        const result = await submitApi(eventId, {
          payload,
          files,
          kind: typeof args.kind === 'string' ? args.kind : undefined,
          clientId: this.options.identity().clientId,
          idempotencyKey:
            typeof args.idempotencyKey === 'string' ? args.idempotencyKey : undefined,
          signal: controller.signal,
          onUploadProgress: (loaded, total) => {
            this.post(HOST_MESSAGE.UPLOAD_PROGRESS, { requestId, loaded, total })
          },
        })
        return result
      }

      default:
        throw new BridgeError(BRIDGE_ERROR.UNSUPPORTED, `不支持的操作：${op}`)
    }
  }

  private loadEvent(): Promise<EventPublic> {
    if (this.eventCache) return Promise.resolve(this.eventCache)
    if (!this.eventPromise) {
      this.eventPromise = getPublicEvent(this.options.eventId())
        .then((event) => {
          this.eventCache = event
          return event
        })
        .finally(() => {
          this.eventPromise = null
        })
    }
    return this.eventPromise
  }

  private withTimeout<T>(
    requestId: string,
    controller: AbortController,
    ms: number,
    promise: Promise<T>,
  ): Promise<T> {
    return new Promise<T>((resolve, reject) => {
      const timer = setTimeout(() => {
        controller.abort()
        reject(new BridgeError(BRIDGE_ERROR.TIMEOUT, '操作超时，请稍后重试'))
      }, ms)

      this.pending.set(requestId, {
        resolve: resolve as (value: unknown) => void,
        reject,
        timer,
        controller,
      })

      promise.then(
        (value) => {
          clearTimeout(timer)
          resolve(value)
        },
        (error) => {
          clearTimeout(timer)
          reject(error)
        },
      )
    })
  }

  private settle(requestId: string, ok: boolean, error?: BridgeError): void {
    const pending = this.pending.get(requestId)
    if (!pending) return
    clearTimeout(pending.timer)
    this.pending.delete(requestId)
    if (ok) pending.resolve(undefined)
    else pending.reject(error)
  }

  private rejectAllPending(error: BridgeError): void {
    for (const [requestId, pending] of this.pending) {
      clearTimeout(pending.timer)
      pending.controller.abort()
      pending.reject(error)
      this.pending.delete(requestId)
    }
  }

  // ------------------------------------------------------------------
  // 发消息
  // ------------------------------------------------------------------

  private sendInit(): void {
    const payload: InitPayload = {
      protocolVersion: PROTOCOL_VERSION,
      eventId: this.options.eventId(),
      apiBase: this.options.apiBase,
      contentBase: this.options.contentBase,
      // 身份描述符**不含任何凭据**：只有登录状态、展示信息与匿名标识
      identity: this.options.identity(),
      theme: this.options.theme(),
    }
    this.post(HOST_MESSAGE.INIT, payload)
  }

  private postResult(requestId: string, result: RpcResultPayload): void {
    this.post(HOST_MESSAGE.RESULT, result, requestId)
  }

  private post(type: string, payload: unknown, id?: string): void {
    const target = this.options.iframe.contentWindow
    if (!target) return
    const envelope: Envelope = { v: PROTOCOL_VERSION, type, payload }
    if (id) envelope.id = id
    // targetOrigin 用 "*"：活动页处于不透明源，无法用具体源匹配。
    // 这在持有凭据的方案里是漏洞，在这里成立是因为 iframe 什么都不持有 ——
    // 泄露的只有公开活动信息与主题令牌。
    target.postMessage(envelope, '*')
  }

  /** 供测试观察内部状态 */
  get pendingCount(): number {
    return this.pending.size
  }

  get isHandshaken(): boolean {
    return this.handshakeDone
  }
}

/** 把活动页传来的值收敛成 File 列表；非 File 一律丢弃。 */
function asFileList(value: unknown): File[] {
  if (!Array.isArray(value)) return []
  return value.filter((item): item is File => item instanceof File)
}

/** 草稿的键由活动页给出，但只作为**同一活动内**的命名空间，不影响路径。 */
function asFormKey(value: unknown): string {
  return typeof value === 'string' && value.length > 0 && value.length <= 64
    ? value
    : 'default'
}

/** 草稿存储：活动页没有本地存储，由宿主按活动代存（不落后端）。 */
export const draftStore = {
  key(eventId: string, formKey: string): string {
    return `cea.draft:${eventId}:${formKey}`
  },

  save(eventId: string, formKey: string, value: unknown): void {
    try {
      window.sessionStorage.setItem(this.key(eventId, formKey), JSON.stringify(value))
    } catch {
      /* 存储不可用时静默降级：草稿只是便利功能 */
    }
  },

  load(eventId: string, formKey: string): unknown | null {
    try {
      const raw = window.sessionStorage.getItem(this.key(eventId, formKey))
      return raw ? JSON.parse(raw) : null
    } catch {
      return null
    }
  },

  clear(eventId: string, formKey: string): void {
    try {
      window.sessionStorage.removeItem(this.key(eventId, formKey))
    } catch {
      /* 同上 */
    }
  },
}
