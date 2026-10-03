/**
 * 确认对话框的组合式 API。
 *
 * ## 为什么不用 `window.confirm`
 *
 * 原生确认框有三处不合用：样式由浏览器决定（暗色界面上突兀）、**阻塞整个事件
 * 循环**（连自己的加载态都动不了）、文案里不能有换行与格式。而且活动页那边
 * 走的是同一套理由 —— 协议文档本来就把它标成"更好的做法"。
 *
 * ## 为什么是 promise 而不是 v-model
 *
 * ```
 * if (!(await confirm.ask({ title: '删除活动', message: '…' }))) return
 * ```
 *
 * 调用点读起来就是它字面的意思，不会在模板里多出一份"待确认状态"要维护。更要紧
 * 的是：**活动页也要问同一个问题**（见 `CEA.confirm`），而它没法持有宿主的组件
 * 状态，只能等一个 promise。
 *
 * ## 同一时刻只允许一个
 *
 * 第二个 `ask` 到来时，**前一个按"取消"结算**再换成新的。排队会让用户对着一个
 * 已经没人关心的对话框点"确定"；直接丢弃则会让前一个调用方的 promise 永远悬着。
 */
import { readonly, ref } from 'vue'

export interface ConfirmOptions {
  title: string
  message: string
  /** 确认按钮的文案。默认"确定" */
  confirmText?: string
  /** 取消按钮的文案。默认"取消" */
  cancelText?: string
  /**
   * 危险动作：确认按钮用警示色。
   *
   * **不改变焦点的落点** —— 焦点始终在取消那一侧，见组件里的说明。
   */
  danger?: boolean
  /** 由活动页发起。会在对话框里标出来，见 `ConfirmHost` 的说明 */
  fromEvent?: boolean
  /**
   * 无人应答时的自动结算时间。
   *
   * 到期按"取消"结算，让对话框自己收掉。桥接那边必须给一个**小于 RPC 超时**的
   * 值，否则调用方先拿到超时错误，而对话框还杵在屏幕上。
   */
  timeoutMs?: number
}

export interface ConfirmRequest {
  id: number
  title: string
  message: string
  confirmText: string
  cancelText: string
  danger: boolean
  fromEvent: boolean
}

const current = ref<ConfirmRequest | null>(null)
const timers = new Map<number, ReturnType<typeof setTimeout>>()
let settlePending: ((ok: boolean) => void) | null = null
let nextId = 1

function clearTimer(id: number): void {
  const handle = timers.get(id)
  if (handle !== undefined) {
    clearTimeout(handle)
    timers.delete(id)
  }
}

/** 用给定答案结算当前对话框。没有对话框时是空操作。 */
function settle(ok: boolean): void {
  const request = current.value
  if (!request) return

  clearTimer(request.id)
  current.value = null

  const resolve = settlePending
  settlePending = null
  resolve?.(ok)
}

function ask(options: ConfirmOptions): Promise<boolean> {
  // 前一个按"取消"结算：它的调用方还在等一个答案，不能就这么悬着
  settle(false)

  const id = nextId++
  current.value = {
    id,
    title: options.title,
    message: options.message,
    confirmText: options.confirmText ?? '确定',
    cancelText: options.cancelText ?? '取消',
    danger: options.danger ?? false,
    fromEvent: options.fromEvent ?? false,
  }

  if (options.timeoutMs !== undefined && options.timeoutMs > 0) {
    timers.set(
      id,
      setTimeout(() => settle(false), options.timeoutMs),
    )
  }

  return new Promise<boolean>((resolve) => {
    settlePending = resolve
  })
}

/** 清空（测试用）。等待中的调用方按"取消"结算。 */
function clear(): void {
  settle(false)
  for (const id of [...timers.keys()]) clearTimer(id)
}

export function useConfirm() {
  return { request: readonly(current), ask, settle, clear }
}
