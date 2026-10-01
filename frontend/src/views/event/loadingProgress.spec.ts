/**
 * 加载反馈的假进度与阶段文案。
 *
 * 最重要的一条性质是**假进度永远到不了 100%** —— 它表示"还要等多久"，而"好了
 * 没有"只有活动页真的连上才知道。让它在等待期间走到头就是在说谎：用户会以为
 * 加载完了，然后盯着一个空页面。所以这里花了好几条断言把这件事钉死。
 */
import { describe, expect, it } from 'vitest'

import {
  COMPLETE_HOLD_MS,
  FADE_MS,
  PROGRESS_CAP,
  PROGRESS_TICK_MS,
  STAGE_LABELS,
  nextProgress,
} from './loadingProgress'

describe('假进度', () => {
  it('上限小于 100', () => {
    expect(PROGRESS_CAP).toBeLessThan(100)
  })

  it('单调不减', () => {
    let value = 0
    for (let step = 0; step < 200; step += 1) {
      const next = nextProgress(value)
      expect(next).toBeGreaterThanOrEqual(value)
      value = next
    }
  })

  it('推进足够多次也到不了 100', () => {
    // 这一条是整件事的核心：等再久也只能停在 PROGRESS_CAP
    let value = 0
    for (let step = 0; step < 10_000; step += 1) value = nextProgress(value)

    expect(value).toBe(PROGRESS_CAP)
    expect(value).toBeLessThan(100)
  })

  it('每次至少推进一点，不会卡住', () => {
    // 只按比例推进的话，越接近上限增量越小，最后会小到看不出在动
    let value = 0
    for (let step = 0; step < 100; step += 1) {
      const next = nextProgress(value)
      if (value < PROGRESS_CAP) expect(next).toBeGreaterThan(value)
      value = next
    }
  })

  it('越接近上限走得越慢', () => {
    const early = nextProgress(0) - 0
    const late = nextProgress(80) - 80
    expect(late).toBeLessThan(early)
  })

  it('从 0 开始就能推进', () => {
    expect(nextProgress(0)).toBeGreaterThan(0)
  })

  it('传入超限值时收敛到上限而不是溢出', () => {
    expect(nextProgress(100)).toBe(PROGRESS_CAP)
    expect(nextProgress(999)).toBe(PROGRESS_CAP)
  })
})

describe('阶段文案', () => {
  it('覆盖全部阶段，顺序即推进顺序', () => {
    expect(Object.keys(STAGE_LABELS)).toEqual(['event', 'content', 'page', 'bridge'])
  })

  it('每一条都是给人看的中文，不是标识符', () => {
    for (const label of Object.values(STAGE_LABELS)) {
      expect(label).toMatch(/[\u4e00-\u9fa5]/)
      expect(label.length).toBeGreaterThan(4)
    }
  })
})

describe('时序常量', () => {
  it('淡出时长不超过设计令牌的过渡上限', () => {
    // 设计语言硬约束：过渡不超过 200ms
    expect(FADE_MS).toBeLessThanOrEqual(200)
  })

  it('补齐到 100% 后会停留一下再淡出', () => {
    // 不留这一下的话，进度条看起来像是被掐断的
    expect(COMPLETE_HOLD_MS).toBeGreaterThan(0)
  })

  it('推进间隔不至于让进度条看起来是跳的', () => {
    expect(PROGRESS_TICK_MS).toBeLessThanOrEqual(250)
  })
})
