/**
 * 桥接协议 v1 —— **两侧唯一真源**。
 *
 * 宿主（SPA）与活动页（iframe 内的 SDK）都从这里取类型与常量。协议的任何改动
 * 都必须先改这里，否则两侧会各自漂移。
 *
 * 三条不可动摇的约束（见 design.md 决策 1-3）：
 *
 * 1. 信封必须带版本号，主版本不一致时宿主明确报错而不是继续
 * 2. 宿主以 `event.source === iframe.contentWindow` 校验来源。
 *    **不能用 `event.origin`**：活动页处于不透明源，其 origin 是字符串 "null"，
 *    拿它做鉴权没有意义
 * 3. 宿主发送时 targetOrigin 用 "*"。这在持有凭据的方案里是漏洞，在这里成立
 *    是因为 iframe **什么都不持有** —— 泄露的只有公开活动信息与主题令牌
 */

/** 协议主版本。不兼容改动时递增，宿主据此拒绝旧版 SDK。 */
export const PROTOCOL_VERSION = 1

/** 宿主下发给活动页的消息类型 */
export const HOST_MESSAGE = {
  /** 握手：下发身份描述符、接口基地址与主题令牌 */
  INIT: 'hub:init',
  /** 登录状态变化时下发更新 */
  SESSION: 'hub:session',
  /** 主题令牌更新 */
  THEME: 'hub:theme',
  /** RPC 结果 */
  RESULT: 'hub:result',
  /** 上传进度 */
  UPLOAD_PROGRESS: 'hub:upload-progress',
  /** 心跳 */
  PING: 'hub:ping',
} as const

/** 活动页发给宿主的消息类型 */
export const IFRAME_MESSAGE = {
  /** 活动页就绪，请求初始化 */
  READY: 'event:ready',
  /** 内容高度变化 */
  RESIZE: 'event:resize',
  /** 请求宿主导航 */
  NAVIGATE: 'event:navigate',
  /** 请求宿主更新标题 */
  TITLE: 'event:title',
  /** 请求宿主弹提示 */
  TOAST: 'event:toast',
  /** RPC 调用 */
  RPC: 'event:rpc',
  /** 取消进行中的 RPC */
  CANCEL: 'event:rpc-cancel',
  /** 活动页内部错误 */
  ERROR: 'event:error',
} as const

/**
 * 宿主允许代活动页执行的操作。
 *
 * **这是整个方案的安全命门。** 如果宿主按活动页给的地址去转发，就等于用宿主的
 * 会话造了一个开放代理，活动页可以要求宿主去调管理接口 —— 安全收益归零。
 * 因此契约是一张固定操作表，不是 URL 转发。
 *
 * 另一个铁律：活动标识一律由宿主从**自身路由**取，绝不采信活动页传值。
 */
export const BRIDGE_OP = {
  /** 读取当前活动公开信息 */
  EVENT_INFO: 'event.info',
  /** 读取当前登录用户信息（零凭证描述符，不是凭据） */
  ME_PROFILE: 'me.profile',
  /** 读取自己的提交历史 */
  ME_SUBMISSIONS: 'me.submissions',
  /** 提交信息（无文件） */
  FORM_SUBMIT: 'form.submit',
  /** 提交信息与文件 */
  FORM_SUBMIT_FILES: 'form.submitFiles',
  /**
   * 草稿存取。
   *
   * 活动页处于不透明源，**没有 localStorage**，因此草稿只能由宿主代存。
   * 这三个操作由宿主在 sessionStorage 里按活动隔离地保存，**不落后端**。
   */
  DRAFT_SAVE: 'draft.save',
  DRAFT_LOAD: 'draft.load',
  DRAFT_CLEAR: 'draft.clear',
} as const

export type BridgeOp = (typeof BRIDGE_OP)[keyof typeof BRIDGE_OP]

export const ALLOWED_OPS: readonly string[] = Object.values(BRIDGE_OP)

/**
 * 活动页可区分的错误码。
 *
 * 活动页**不需要解析 HTTP 状态码**：宿主把后端错误映射成这里的取值。
 * 其中 `quota_exhausted` 与 `event_closed` 是终态，`rate_limited` 是唯一
 * 值得重试的。
 */
export const BRIDGE_ERROR = {
  LOGIN_REQUIRED: 'login_required',
  EVENT_CLOSED: 'event_closed',
  QUOTA_EXHAUSTED: 'quota_exhausted',
  RATE_LIMITED: 'rate_limited',
  VALIDATION_FAILED: 'validation_failed',
  NOT_FOUND: 'not_found',
  PAYLOAD_TOO_LARGE: 'payload_too_large',
  UNSUPPORTED: 'unsupported_op',
  TIMEOUT: 'timeout',
  CANCELLED: 'cancelled',
  BRIDGE_MISSING: 'bridge_missing',
  VERSION_MISMATCH: 'version_mismatch',
  UNKNOWN: 'unknown_error',
} as const

export type BridgeErrorCode = (typeof BRIDGE_ERROR)[keyof typeof BRIDGE_ERROR]

/** 消息信封 */
export interface Envelope<T = unknown> {
  v: number
  type: string
  /** RPC 关联标识；由活动页生成，宿主原样回传 */
  id?: string
  payload?: T
}

/** 宿主下发的身份描述符 —— **零凭证** */
export interface IdentityDescriptor {
  loggedIn: boolean
  userId: number | null
  displayName: string | null
  role: string | null
  /** 匿名标识。不是凭据，仅用于分组 */
  clientId: string
  /** 该活动是否要求登录，供活动页提前渲染正确状态 */
  submissionRequiresLogin: boolean
}

export interface InitPayload {
  protocolVersion: number
  eventId: string
  apiBase: string
  contentBase: string
  identity: IdentityDescriptor
  theme: Record<string, string>
}

export interface RpcResultPayload {
  ok: boolean
  data?: unknown
  error?: { code: BridgeErrorCode; message: string; fields?: Record<string, string> }
}

export interface UploadProgressPayload {
  loaded: number
  total: number | null
}

export interface RpcRequestPayload {
  op: string
  args?: Record<string, unknown>
}

/** 判断一个值是否是形状合法的信封 */
export function isEnvelope(value: unknown): value is Envelope {
  if (typeof value !== 'object' || value === null) return false
  const candidate = value as Record<string, unknown>
  if (typeof candidate.v !== 'number') return false
  if (typeof candidate.type !== 'string' || candidate.type.length === 0) return false
  if (candidate.id !== undefined && typeof candidate.id !== 'string') return false
  return true
}

/** 主版本是否一致 */
export function isCompatible(version: unknown): boolean {
  return typeof version === 'number' && Math.floor(version) === PROTOCOL_VERSION
}
