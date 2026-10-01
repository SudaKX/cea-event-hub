/**
 * 活动页加载的**假进度**与阶段文案。
 *
 * 抽成模块而不是塞在视图里，是为了能直接断言它最重要的一条性质：
 * **它永远到不了 100%。**
 *
 * 进度条表示"还要等多久"，而"好了没有"只有活动页真的连上才知道。让它在等待期间
 * 自己走到 100 就是在说谎 —— 用户会以为加载完了，然后盯着一个空页面。
 * 所以上限是 `PROGRESS_CAP`，真正就绪时才由调用方补齐到 100。
 */

/** 各阶段的文案。键的顺序即推进顺序。 */
export const STAGE_LABELS = {
  event: '正在读取活动信息',
  content: '正在检查活动内容',
  page: '正在加载活动页面',
  bridge: '正在与活动页建立连接',
} as const

export type LoadStage = keyof typeof STAGE_LABELS

/** 假进度在等待期间的上限。**必须小于 100**，理由见文件头。 */
export const PROGRESS_CAP = 92

/** 推进一次假进度。越接近上限走得越慢，看起来像在等最后一点。 */
export function nextProgress(current: number): number {
  if (current >= PROGRESS_CAP) return PROGRESS_CAP
  const remaining = PROGRESS_CAP - current
  return Math.min(PROGRESS_CAP, current + Math.max(0.6, remaining * 0.08))
}

/** 假进度的推进间隔。 */
export const PROGRESS_TICK_MS = 160

/** 补齐到 100% 后停留多久再淡出 —— 否则进度条像是被掐断的。 */
export const COMPLETE_HOLD_MS = 220

/** 覆盖层与进度条的淡出时长。与设计令牌的过渡上限一致（≤200ms）。 */
export const FADE_MS = 200
