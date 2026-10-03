/**
 * 确认对话框的组合式 API。
 *
 * 盯的是几件容易做错的事：等待中的调用方不能被抛下、同一时刻只允许一个、
 * 超时要按"取消"结算。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useConfirm } from './useConfirm'

beforeEach(() => {
  vi.useFakeTimers()
  useConfirm().clear()
})

afterEach(() => {
  useConfirm().clear()
  vi.useRealTimers()
})

describe('问答', () => {
  it('ask 之后请求出现，settle(true) 兑现为 true', async () => {
    const pending = useConfirm().ask({ title: '删除', message: '确定吗？' })
    expect(useConfirm().request.value).toMatchObject({ title: '删除', message: '确定吗？' })

    useConfirm().settle(true)
    await expect(pending).resolves.toBe(true)
    expect(useConfirm().request.value).toBeNull()
  })

  it('settle(false) 兑现为 false', async () => {
    const pending = useConfirm().ask({ title: '删除', message: '确定吗？' })
    useConfirm().settle(false)
    await expect(pending).resolves.toBe(false)
  })

  it('默认文案与语气', () => {
    void useConfirm().ask({ title: 't', message: 'm' })
    expect(useConfirm().request.value).toMatchObject({
      confirmText: '确定',
      cancelText: '取消',
      danger: false,
      fromEvent: false,
    })
  })

  it('文案可以覆盖', () => {
    void useConfirm().ask({
      title: 't',
      message: 'm',
      confirmText: '删除',
      cancelText: '算了',
      danger: true,
      fromEvent: true,
    })
    expect(useConfirm().request.value).toMatchObject({
      confirmText: '删除',
      cancelText: '算了',
      danger: true,
      fromEvent: true,
    })
  })

  it('没有请求时 settle 是空操作', () => {
    expect(() => useConfirm().settle(true)).not.toThrow()
    expect(useConfirm().request.value).toBeNull()
  })
})

describe('同一时刻只允许一个', () => {
  it('第二个 ask 会把前一个按取消结算', async () => {
    /*
      排队会让用户对着一个已经没人关心的框点"确定"；直接丢弃则会让前一个调用方
      的 promise 永远悬着 —— 那是最糟的一种：调用点停在 await 上，什么都不会发生。
    */
    const first = useConfirm().ask({ title: '第一个', message: 'm' })
    const second = useConfirm().ask({ title: '第二个', message: 'm' })

    await expect(first).resolves.toBe(false)
    expect(useConfirm().request.value!.title).toBe('第二个')

    useConfirm().settle(true)
    await expect(second).resolves.toBe(true)
  })
})

describe('超时', () => {
  it('到期按取消结算并收掉框', async () => {
    // 用户把框晾在那儿不管时，调用方不能一直等下去
    const pending = useConfirm().ask({ title: 't', message: 'm', timeoutMs: 1000 })

    vi.advanceTimersByTime(999)
    expect(useConfirm().request.value).not.toBeNull()

    vi.advanceTimersByTime(1)
    await expect(pending).resolves.toBe(false)
    expect(useConfirm().request.value).toBeNull()
  })

  it('中途结算之后定时器不会再触发一次', async () => {
    const pending = useConfirm().ask({ title: 't', message: 'm', timeoutMs: 1000 })
    useConfirm().settle(true)
    await expect(pending).resolves.toBe(true)

    // 泄漏的定时器会去结算一个已经不存在的请求
    vi.advanceTimersByTime(5000)
    expect(useConfirm().request.value).toBeNull()
  })

  it('不给 timeoutMs 就一直等', () => {
    void useConfirm().ask({ title: 't', message: 'm' })
    vi.advanceTimersByTime(600_000)
    expect(useConfirm().request.value).not.toBeNull()
  })
})

describe('clear', () => {
  it('把等待中的调用方按取消结算', async () => {
    // 测试之间必须清干净，否则上一个用例的 promise 会悬着
    const pending = useConfirm().ask({ title: 't', message: 'm' })
    useConfirm().clear()
    await expect(pending).resolves.toBe(false)
    expect(useConfirm().request.value).toBeNull()
  })
})
