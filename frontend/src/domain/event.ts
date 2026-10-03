/**
 * 活动可见性的码值与展示。
 *
 * 与提交状态一样放在 `domain/` 下：码值是**对外契约**（公开列表端点、首页卡片区、
 * 标识补全都按它走），改动必须配迁移；而"码值 -> 标签"的映射在两个视图里都要用，
 * 各写一份必然漂移。
 */
import type { SelectOption } from '@/components/ui/Select.vue'

/**
 * 在公开面露多少。与后端 `core/enums.py` 的 `EventVisibility` 一一对应。
 *
 * - `INVISIBLE = 0` —— 不进任何公开面，但**仍可按标识访问**：它是"未公开"，
 *   不是"不存在"
 * - `PUBLIC = 1` —— 进公开列表端点与首页的标识补全
 * - `PINNED = 2` —— 在此之上，还进首页的卡片区
 *
 * 用一个有序刻度而不是两个布尔，是因为**置顶蕴含公开**：把一条不公开的活动置顶
 * 没有意义，两个布尔会允许这种无意义组合存在。
 */
export const EVENT_VISIBILITY = {
  INVISIBLE: 0,
  PUBLIC: 1,
  PINNED: 2,
} as const

/** 码值到人读名称。与后端 `EVENT_VISIBILITY_LABELS` 保持一致。 */
export const EVENT_VISIBILITY_LABELS: Record<number, string> = {
  [EVENT_VISIBILITY.INVISIBLE]: '不公开',
  [EVENT_VISIBILITY.PUBLIC]: '公开',
  [EVENT_VISIBILITY.PINNED]: '公开并置顶',
}

/** 标签配色用的后缀。数字不能直接进 class 名，所以映射成词。 */
const TONES: Record<number, string> = {
  [EVENT_VISIBILITY.INVISIBLE]: 'off',
  [EVENT_VISIBILITY.PUBLIC]: 'received',
  [EVENT_VISIBILITY.PINNED]: 'accepted',
}

/** 未知码值原样显示，而不是变成空白 —— 看得见异常比看不见好。 */
export function visibilityLabel(code: number): string {
  return EVENT_VISIBILITY_LABELS[code] ?? `未知可见性 ${code}`
}

export function visibilityTone(code: number): string {
  return TONES[code] ?? 'unknown'
}

/** 管理端下拉的选项。把后果写进标签 —— 光看三档名称不知道意味着什么。 */
export const EVENT_VISIBILITY_OPTIONS: SelectOption[] = [
  {
    value: String(EVENT_VISIBILITY.INVISIBLE),
    label: '不公开（不进列表与补全，但链接仍可访问）',
  },
  { value: String(EVENT_VISIBILITY.PUBLIC), label: '公开（进列表与标识补全）' },
  { value: String(EVENT_VISIBILITY.PINNED), label: '公开并置顶（另进首页卡片区）' },
]

/**
 * 把下拉给的字符串转成码值。
 *
 * 顺带挡住非数字输入 —— 与提交状态那边同理：`Number('abc')` 是 `NaN`，发出去
 * 后端只会回一个难以理解的校验错误。
 */
export function parseVisibility(value: string): number {
  const code = Number(value)
  return Number.isInteger(code) && code in EVENT_VISIBILITY_LABELS
    ? code
    : EVENT_VISIBILITY.PUBLIC
}
