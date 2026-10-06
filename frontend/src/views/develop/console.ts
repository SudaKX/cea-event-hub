/**
 * 调试台的日志模型与动作清单推导。
 *
 * 同样抽成独立模块：清单推导是纯函数，值得单独钉住（见同目录的 `console.spec.ts`），
 * 而 `<script setup>` 不允许具名导出。
 */

import {
  BRIDGE_ERROR,
  BRIDGE_OP,
  HOST_MESSAGE,
  type BridgeErrorCode,
} from '@/bridge/protocol'

/**
 * 一条日志的来源。
 *
 * 这个区分不是装饰，而是**这套面板唯一可能骗人的地方**：
 *
 * - `in`：活动页 → 宿主。在宿主的来源校验与信封校验之后观测到，是真实的线上消息。
 * - `host`：**宿主自身**发出的消息（初始化、会话推送、应答、通知、确认框……）。
 *   在宿主真正 `postMessage` 的那一处观测到，同样真实 —— 因此"活动页请求"与
 *   "宿主应答"能成对看到。
 * - `out`：面板直接发出的宿主 → 活动页消息。真实的发送（它与 `host` 分开，只是为了
 *   让人一眼看出"这条是我手动点的"）。
 * - `forged`：面板伪造的那条结算。**没有发往任何后端**，内容是假的。
 * - `held`：被拦下来、还没决定的请求。不是消息，是队列状态。
 */
export type Direction = 'in' | 'out' | 'forged' | 'held' | 'host'

export interface DebugEntry {
  seq: number
  at: number
  direction: Direction
  type: string
  summary: string
}

/** 与后端 `Settings.DEV_EVENT_ID` 的默认值一致。见 docs/dev-harness.md。 */
export const DEFAULT_DEV_EVENT_ID = 'dev'

/**
 * 日志里的时刻，精确到秒。
 *
 * 毫秒对读日志没有帮助，只会占宽度；而**秒是需要的** —— "应答是在超时之后才到的吗"
 * 这类问题靠消息顺序看不出来。
 */
export function formatLogTime(at: number): string {
  const date = new Date(at)
  const pad = (value: number) => String(value).padStart(2, '0')
  return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

/** 把一条信封压成一行可读的摘要。认不出来的形状也要能显示，不能抛。 */
export function summarize(data: unknown): string {
  if (typeof data !== 'object' || data === null) return String(data)

  const envelope = data as { type?: unknown; id?: unknown; payload?: unknown }
  const payload = (envelope.payload ?? {}) as Record<string, unknown>
  const parts: string[] = []

  if (typeof envelope.id === 'string') parts.push(`id=${envelope.id}`)

  if (typeof payload.op === 'string') {
    parts.push(`op=${payload.op}`)
    const args = payload.args as Record<string, unknown> | undefined
    if (args && typeof args === 'object') {
      const keys = Object.keys(args)
      if (keys.length > 0) parts.push(`args={${keys.join(',')}}`)
    }
  }

  if (typeof payload.ok === 'boolean') {
    if (payload.ok) {
      parts.push('ok')
    } else {
      const error = payload.error as { code?: unknown; message?: unknown } | undefined
      parts.push(`error=${String(error?.code ?? 'unknown')}`)
      if (typeof error?.message === 'string') parts.push(error.message)
    }
  }

  if (typeof payload.protocolVersion === 'number') {
    parts.push(`protocolVersion=${payload.protocolVersion}`)
  }

  if (typeof payload.loaded === 'number') {
    const total = payload.total
    parts.push(total === null || total === undefined
      ? `loaded=${payload.loaded}`
      : `loaded=${payload.loaded}/${String(total)}`)
  }

  if (typeof payload.level === 'string' || typeof payload.message === 'string') {
    parts.push(`${String(payload.level ?? 'info')}: ${String(payload.message ?? '')}`)
  }

  if (typeof payload.to === 'string') parts.push(`to=${payload.to}`)
  if (typeof payload.title === 'string') parts.push(`title=${payload.title}`)

  return parts.join('  ')
}

// ---------------------------------------------------------------------------
// 宿主 → 活动页 的动作清单
// ---------------------------------------------------------------------------

/**
 * 动作的发送方式。
 *
 * - `handshake` / `session`：走宿主的**真实方法**。重握手要连带重置宿主的内部状态，
 *   自己发一条 `hub:init` 做不到这件事。
 * - `raw`：直接发。这类消息宿主要么从不主动发（主题），要么需要面板补上下文
 *   （结果与上传进度都要一个请求 id）。
 */
export type HostActionKind = 'handshake' | 'session' | 'raw'

export interface HostAction {
  type: string
  kind: HostActionKind
  hint: string
}

/**
 * 已知类型的补充说明。
 *
 * **这份表只提供说明，不提供清单** —— 清单来自 `HOST_MESSAGE` 本身。因此协议里新增
 * 一个类型时，它自动出现在面板上（按 `raw` 处理），不需要有人记得来这里登记。
 * 反过来，这里多写一个协议里没有的类型不会有任何效果，也不会造成"面板上有、协议里
 * 没有"的假象。
 */
const KNOWN_HINTS: Record<string, { kind: HostActionKind; hint: string }> = {
  [HOST_MESSAGE.INIT]: {
    kind: 'handshake',
    hint: '重握手：走宿主真实路径，会一并重置宿主的握手状态',
  },
  [HOST_MESSAGE.SESSION]: {
    kind: 'session',
    hint: '把面板里设定的身份推给活动页；活动页的 CEA.identity() 会随之改变',
  },
  [HOST_MESSAGE.THEME]: {
    kind: 'raw',
    hint: '宿主从不主动发这条 —— 正好用来试活动页对主题更新的反应',
  },
  [HOST_MESSAGE.RESULT]: {
    kind: 'raw',
    hint: '需要请求 id：用最近一次活动页请求的 id。这就是"拦截伪造"的手动版',
  },
  [HOST_MESSAGE.UPLOAD_PROGRESS]: {
    kind: 'raw',
    hint: '需要请求 id；total 为 null 表示服务端未给总长',
  },
  [HOST_MESSAGE.PING]: {
    kind: 'raw',
    hint: '协议里声明了，但宿主当前没有发送路径 —— 用来试活动页会不会被它弄乱',
  },
}

/**
 * 由协议常量推导出面板上的动作清单。
 *
 * 参数留了默认值是为了可测：传一组"多了一个类型"的清单进去，就能断言新类型确实
 * 会出现 —— 这正是"清单从真源推导"要保证的性质，也是手写清单做不到的事。
 */
export function hostActions(
  types: readonly string[] = Object.values(HOST_MESSAGE),
): HostAction[] {
  return types.map((type) => {
    const known = KNOWN_HINTS[type]
    return {
      type,
      kind: known?.kind ?? 'raw',
      hint: known?.hint ?? '协议里的新类型：面板按直接发送处理',
    }
  })
}

// ---------------------------------------------------------------------------
// 拦截：多选过滤 → 挂起 → 结算
// ---------------------------------------------------------------------------

/**
 * 一条被拦截的请求最终怎么结算。
 *
 * - `ask`：**挂起**，等人在面板上选。请求不会发给后端。
 * - `fail`：直接用一条伪造的错误结算，同样不发网络请求。
 * - `forward`：转发给真实后端，把**后端的真实返回值**回给活动页。
 */
export type InterceptAction = 'ask' | 'fail' | 'forward'

/** 默认应答动作。`ask` 不是"默认"，它意味着没有默认。 */
export type DefaultAction = Exclude<InterceptAction, 'ask'>

export interface InterceptState {
  /** 多选过滤：勾中的操作才会被拦。未勾中的一律照常转发。 */
  filters: Record<string, boolean>
  /**
   * 是否启用默认应答。
   *
   * **关掉时每一条都挂起等人选**（`ask`）。开着时按 `defaultAction` 直接结算，
   * 不必逐条点 —— 这就是"不用手动选择"的那一档。
   */
  autoRespond: boolean
  defaultAction: DefaultAction
  /** 默认伪造哪个错误码。 */
  defaultCode: BridgeErrorCode
}

export function emptyInterceptState(): InterceptState {
  return {
    filters: {},
    // 默认关闭：开着会让"拦截"从一个观察动作变成静默改写结果的动作
    autoRespond: false,
    defaultAction: 'fail',
    defaultCode: BRIDGE_ERROR.TIMEOUT,
  }
}

/**
 * 判定一条请求该怎么处理。**这是整套拦截逻辑的唯一判据**，抽出来是为了能单独钉住它。
 *
 * @returns `null` 表示不拦截（照常转发）；否则返回该怎么结算。
 */
export function decideInterception(
  state: InterceptState,
  op: unknown,
): InterceptAction | null {
  if (typeof op !== 'string') return null
  // 多选过滤：只有勾中的才拦
  if (state.filters[op] !== true) return null
  // 没开默认应答就挂起等人选 —— 这是"观察请求内容"那一档的前提
  if (!state.autoRespond) return 'ask'
  return state.defaultAction
}

/** 勾中的操作。给角标与摘要用。 */
export function filteredOps(filters: Record<string, boolean>): string[] {
  return Object.keys(filters).filter((op) => filters[op] === true)
}

/**
 * 可被过滤的操作清单。**来自协议常量，不手写** —— 协议里加一个操作，这里自动多一行。
 * 参数留默认值是为了可测：传一组"多了一个操作"的清单进去就能断言它会出现。
 */
export function filterableOps(
  ops: readonly string[] = Object.values(BRIDGE_OP),
): string[] {
  return [...ops]
}

/** 请求内容的可读形式。挂起时要让人看清发的是什么。 */
export function formatArgs(args: unknown, maxLength = 600): string {
  let text: string
  try {
    text = JSON.stringify(args, null, 2) ?? String(args)
  } catch {
    // 循环引用之类：不值得让整个面板挂掉
    text = String(args)
  }
  return text.length > maxLength ? `${text.slice(0, maxLength)}\n…（已截断）` : text
}

/** 值得伪造的错误码。挑的是真后端造不出来或很难造的那几个。 */
export const FORGEABLE_CODES: readonly BridgeErrorCode[] = [
  BRIDGE_ERROR.TIMEOUT,
  BRIDGE_ERROR.UNSUPPORTED,
  BRIDGE_ERROR.VALIDATION_FAILED,
  BRIDGE_ERROR.RATE_LIMITED,
  BRIDGE_ERROR.PAYLOAD_TOO_LARGE,
  BRIDGE_ERROR.CANCELLED,
]

/**
 * 造一条伪造的错误。形状与宿主真实回的那条一致，活动页无法区分（这正是它的用途）。
 */
export function forgedError(code: BridgeErrorCode): {
  code: BridgeErrorCode
  message: string
} {
  return { code, message: forgedMessage(code) }
}

/** 伪造结果时给活动页看的话。写得像真的一样，否则活动页的错误分支测不出真实观感。 */
export function forgedMessage(code: BridgeErrorCode): string {
  switch (code) {
    case BRIDGE_ERROR.TIMEOUT:
      return '操作超时，请稍后重试'
    case BRIDGE_ERROR.UNSUPPORTED:
      return '宿主未提供该能力'
    case BRIDGE_ERROR.VALIDATION_FAILED:
      return '提交的内容没通过校验'
    case BRIDGE_ERROR.RATE_LIMITED:
      return '操作过于频繁，请稍后再试'
    case BRIDGE_ERROR.PAYLOAD_TOO_LARGE:
      return '内容或文件超出体积上限'
    case BRIDGE_ERROR.CANCELLED:
      return '请求已取消'
    default:
      return '操作失败'
  }
}
