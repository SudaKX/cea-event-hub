/**
 * 提交的领域取值与展示辅助。
 *
 * 单独成模块而不是散在两个视图里：状态码到标签的映射、以及 `$display` 的取值规则
 * 都是**跨视图共用的契约**，各写一份必然漂移。
 */
import type { SelectOption } from '@/components/ui/Select.vue'

/**
 * 审核状态码。与后端 `core/enums.py` 的 `SubmissionStatus` 一一对应。
 *
 * - `IGNORED = 0` —— 不采用。取 0 是因为它在布尔与数值判断里天然表示"否"
 * - `RECEIVED = 1` —— 刚收到，尚未处理。新提交的默认值
 * - `ACCEPTED = 2` —— 采用
 *
 * 刻意没有"审核中"这一档：三档够用，而中间态会让"按状态筛选"多出一个语义模糊的
 * 桶 —— 管理员要么还没看，要么已经决定。
 */
export const SUBMISSION_STATUS = {
  IGNORED: 0,
  RECEIVED: 1,
  ACCEPTED: 2,
} as const

/** 码值到人读名称。与后端 `SUBMISSION_STATUS_LABELS` 保持一致。 */
export const SUBMISSION_STATUS_LABELS: Record<number, string> = {
  [SUBMISSION_STATUS.IGNORED]: '不采用',
  [SUBMISSION_STATUS.RECEIVED]: '待处理',
  [SUBMISSION_STATUS.ACCEPTED]: '已采用',
}

/** 码值到标签配色用的后缀。数字不能直接进 class 名，所以映射成词。 */
const STATUS_TONES: Record<number, string> = {
  [SUBMISSION_STATUS.IGNORED]: 'ignored',
  [SUBMISSION_STATUS.RECEIVED]: 'received',
  [SUBMISSION_STATUS.ACCEPTED]: 'accepted',
}

/** 状态标签。未知码值原样显示，而不是变成空白 —— 看得见异常比看不见好。 */
export function statusLabel(status: number): string {
  return SUBMISSION_STATUS_LABELS[status] ?? `未知状态 ${status}`
}

export function statusTone(status: number): string {
  return STATUS_TONES[status] ?? 'unknown'
}

/** 状态筛选下拉的选项。"全部"用空串，与"不过滤"对齐。 */
export const SUBMISSION_STATUS_OPTIONS: SelectOption[] = [
  { value: '', label: '全部' },
  ...Object.entries(SUBMISSION_STATUS_LABELS).map(([code, label]) => ({
    value: code,
    label: `${label}（${code}）`,
  })),
]

/**
 * 把筛选下拉的字符串转成码值。
 *
 * 下拉组件的值一律是字符串（它要能表示"全部"这个空值），而接口要的是整数。
 * 这里顺带挡住非数字输入 —— `Number('abc')` 是 `NaN`，发出去后端只会回一个
 * 难以理解的校验错误。
 */
export function parseStatusFilter(value: string): number | undefined {
  if (value === '') return undefined
  const code = Number(value)
  return Number.isInteger(code) && code in SUBMISSION_STATUS_LABELS ? code : undefined
}

/**
 * 活动页可以放进 payload 的展示替换字段。
 *
 * 用 `$` 前缀是刻意的：活动自己的字段很难叫这个名字，不会撞。
 */
export const DISPLAY_KEY = '$display'

/**
 * 列表那一格显示什么。
 *
 * 活动页给了 `payload.$display` 就显示它 —— 一串给人读的摘要，比整段 JSON 好认
 * 得多。没有（或不是非空字符串）时退回 JSON。
 *
 * **调用方必须把它当纯文本渲染，绝不能当 HTML。** payload 完全由提交者控制，而
 * 提交可以是匿名的 —— 拿它去 `v-html` 等于把 XSS 交给任何一个访客。
 */
export function payloadSummary(payload: Record<string, unknown>): string {
  const value = payload[DISPLAY_KEY]
  if (typeof value === 'string' && value.trim() !== '') return value
  return JSON.stringify(payload)
}

/**
 * 展开面板里的完整内容。
 *
 * **始终包含完整 JSON**：`$display` 是摘要，它不该把原始数据挡在后面 —— 管理员
 * 展开的目的往往正是看摘要没覆盖到的字段。
 */
export function payloadDetail(payload: Record<string, unknown>): string {
  const json = JSON.stringify(payload, null, 2)
  const display = payload[DISPLAY_KEY]
  if (typeof display !== 'string' || display.trim() === '') return json
  return `${display}\n\n原始数据\n${json}`
}
