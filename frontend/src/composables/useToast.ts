/**
 * 通知（toast）的组合式 API。
 *
 * ## 什么时候该用通知
 *
 * **通知用于"操作的结果"** —— 保存、创建、删除、批量处理这类**改变了状态**、
 * 而结果并不出现在用户当前视线里的动作。三种反馈各有各的落点：
 *
 * | 场景 | 落点 | 理由 |
 * |---|---|---|
 * | 操作结果（成功或失败） | 通知 | 用户的眼睛在别处，而且结果本身很短 |
 * | 页面/列表**加载**失败 | 内联 | 通知会消失，留下一片空白，比一直显示错误更糟 |
 * | 表单字段校验 | 字段旁 | 用户正看着输入框，要指出的是**哪一个**字段 |
 *
 * 所以认证页的登录失败留在表单里，而管理台的各种"改完了"走通知。
 *
 * ## 为什么是模块级状态而不是 Pinia store
 *
 * 通知栈是**应用级的界面状态**，与领域数据无关。做成 store 会让每个组件测试都必须
 * 装 Pinia —— 这是实实在在的摩擦，换不来任何好处。这里用一个模块级的 `ref`，
 * 谁都能 `useToast()`，测试里 `clear()` 一下即可。
 */
import { readonly, ref } from 'vue'

export type ToastTone = 'ok' | 'error' | 'info'

export interface Toast {
  id: number
  tone: ToastTone
  message: string
}

/**
 * 各类通知的默认停留时长（毫秒）。
 *
 * 失败留得久一些：它通常更长、更需要看清，而且往往要求用户做点什么。成功只要
 * 让人知道"成了"就够。
 */
const DEFAULT_DURATION: Record<ToastTone, number> = {
  ok: 3200,
  info: 3200,
  error: 6400,
}

/**
 * 同时最多显示几条。
 *
 * 批量操作逐条失败时可能瞬间推入很多条，不设上限会把屏幕铺满、还把更早的挤到
 * 看不见的地方。丢弃**最旧**的：最新的那条通常最相关。
 */
const MAX_VISIBLE = 4

const toasts = ref<Toast[]>([])
const timers = new Map<number, ReturnType<typeof setTimeout>>()
let nextId = 1

function clearTimer(id: number): void {
  const handle = timers.get(id)
  if (handle !== undefined) {
    clearTimeout(handle)
    timers.delete(id)
  }
}

function dismiss(id: number): void {
  clearTimer(id)
  toasts.value = toasts.value.filter((toast) => toast.id !== id)
}

function push(tone: ToastTone, message: string, duration?: number): number {
  const text = message.trim()
  // 空消息不推：一条没有内容的通知只会让人以为界面坏了
  if (!text) return 0

  const id = nextId++
  toasts.value = [...toasts.value, { id, tone, message: text }]

  // 超上限时丢最旧的，并把它挂着的定时器一并清掉
  while (toasts.value.length > MAX_VISIBLE) {
    const oldest = toasts.value[0]
    if (oldest === undefined) break
    clearTimer(oldest.id)
    toasts.value = toasts.value.slice(1)
  }

  const wait = duration ?? DEFAULT_DURATION[tone]
  if (wait > 0) timers.set(id, setTimeout(() => dismiss(id), wait))
  return id
}

/**
 * 暂停自动消失（指针停在上面时）。
 *
 * 简化之处：移开之后是**重新计时整段时长**，而不是接着剩下的时间走。精确到毫秒
 * 需要记录剩余量并在每次进出时重算，为一个"鼠标扫过"的场景不值得。
 */
function hold(id: number): void {
  clearTimer(id)
}

function resume(id: number): void {
  const toast = toasts.value.find((item) => item.id === id)
  if (!toast) return
  clearTimer(id)
  timers.set(id, setTimeout(() => dismiss(id), DEFAULT_DURATION[toast.tone]))
}

/** 清空全部通知。测试的 beforeEach 与"离开页面"时用得上。 */
function clear(): void {
  for (const id of [...timers.keys()]) clearTimer(id)
  toasts.value = []
}

export function useToast() {
  return {
    /** 只读：改通知只能走下面这几个方法，免得绕开定时器管理 */
    toasts: readonly(toasts),
    push,
    ok: (message: string, duration?: number) => push('ok', message, duration),
    fail: (message: string, duration?: number) => push('error', message, duration),
    info: (message: string, duration?: number) => push('info', message, duration),
    dismiss,
    hold,
    resume,
    clear,
  }
}
