/**
 * 通知的组合式 API。
 *
 * 这里盯的是几件容易做错的事：自动消失的定时器要清干净、超量时丢最旧的、
 * 空消息不推、以及"失败留得比成功久"。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useToast } from './useToast'

beforeEach(() => {
  vi.useFakeTimers()
  useToast().clear()
})

afterEach(() => {
  useToast().clear()
  vi.useRealTimers()
})

describe('推入与消失', () => {
  it('推入后出现在栈里', () => {
    useToast().ok('已保存')
    expect(useToast().toasts.value).toHaveLength(1)
    expect(useToast().toasts.value[0]).toMatchObject({ tone: 'ok', message: '已保存' })
  })

  it('到点自动消失', () => {
    useToast().ok('已保存')
    vi.advanceTimersByTime(3200)
    expect(useToast().toasts.value).toHaveLength(0)
  })

  it('失败留得比成功久', () => {
    // 失败通常更长、更需要看清，而且往往要求用户做点什么
    useToast().ok('成了')
    useToast().fail('没成')

    vi.advanceTimersByTime(3200)
    // 成功已经走了，失败还在
    expect(useToast().toasts.value.map((t) => t.tone)).toEqual(['error'])

    vi.advanceTimersByTime(3200)
    expect(useToast().toasts.value).toHaveLength(0)
  })

  it('可以手动关掉', () => {
    const id = useToast().ok('已保存')
    useToast().dismiss(id)
    expect(useToast().toasts.value).toHaveLength(0)
  })

  it('手动关掉之后定时器不会再触发一次', () => {
    // 泄漏的定时器会在清空之后又跑一次 dismiss，若实现里用 push 而不是过滤就会复活
    const id = useToast().ok('已保存')
    useToast().dismiss(id)
    vi.advanceTimersByTime(10_000)
    expect(useToast().toasts.value).toHaveLength(0)
  })

  it('时长可以显式给 0 表示不自动消失', () => {
    useToast().info('需要你处理', 0)
    vi.advanceTimersByTime(60_000)
    expect(useToast().toasts.value).toHaveLength(1)
  })
})

describe('栈的管理', () => {
  it('空消息不推', () => {
    // 一条没有内容的通知只会让人以为界面坏了
    useToast().ok('   ')
    expect(useToast().toasts.value).toHaveLength(0)
  })

  it('消息两端的空白会被去掉', () => {
    useToast().ok('  已保存  ')
    expect(useToast().toasts.value[0]!.message).toBe('已保存')
  })

  it('超过上限时丢最旧的', () => {
    // 批量操作逐条失败时可能瞬间推入很多条，不设上限会把屏幕铺满
    for (let index = 1; index <= 6; index++) useToast().ok(`第 ${index} 条`)

    const messages = useToast().toasts.value.map((t) => t.message)
    expect(messages).toEqual(['第 3 条', '第 4 条', '第 5 条', '第 6 条'])
  })

  it('被挤掉的条目其定时器也一并清掉', () => {
    for (let index = 1; index <= 6; index++) useToast().ok(`第 ${index} 条`)
    // 最旧那条的定时器若还在，它的 dismiss 会命中同 id 的新条目
    vi.advanceTimersByTime(3200)
    expect(useToast().toasts.value).toHaveLength(0)
  })

  it('clear 清空一切', () => {
    useToast().ok('a')
    useToast().fail('b')
    useToast().clear()
    expect(useToast().toasts.value).toHaveLength(0)
  })
})

describe('悬停暂停', () => {
  it('按住时不再计时', () => {
    const id = useToast().ok('已保存')
    useToast().hold(id)

    vi.advanceTimersByTime(10_000)
    expect(useToast().toasts.value).toHaveLength(1)
  })

  it('移开之后重新计时', () => {
    const id = useToast().ok('已保存')
    useToast().hold(id)
    vi.advanceTimersByTime(3000)
    useToast().resume(id)

    vi.advanceTimersByTime(3200)
    expect(useToast().toasts.value).toHaveLength(0)
  })

  it('对已经不存在的条目 hold/resume 不会炸', () => {
    useToast().hold(999)
    useToast().resume(999)
    expect(useToast().toasts.value).toHaveLength(0)
  })
})

describe('状态是模块级共享的', () => {
  it('两处 useToast 看到同一个栈', () => {
    // 组件与被调用的视图必须看到同一份，否则通知会推到一个没人渲染的栈里
    useToast().ok('已保存')
    expect(useToast().toasts.value).toHaveLength(1)
  })
})
